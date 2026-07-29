"""Memory: consolidation, retrieval, episodic summaries, decay, panel round-trips.

Write path (v2, memory doc 03-memory): NOT per-turn. A successful turn only
`note_turn`s the conversation dirty and (re)arms an idle timer; the actual
extract->decide->summarize pass runs off the hot path via `consolidate` on an
idle / shutdown / startup-recovery / pressure trigger. One extraction call over
the whole un-consolidated span, an AUDN decision per candidate against its
vector neighbors, then one session_summary row -- the cursor advances only on
full success (rule 5). Provenance (rule 6) stays enforced in the store, below
the decision layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from brain import llm, store

logger = logging.getLogger("brain.memory")

_KINDS = {"preference", "project", "workflow", "decision", "lesson"}
# ponytail: L2 distance on normalized bge vectors; < 0.35 ~= cosine > 0.94,
# "near-identical". Conservative on purpose -- a near-duplicate belief beats
# a wrong merge. Tune via settings if false-merges ever show up.
_DUP_DIST = 0.35
_BUDGET_TOKENS = 1000  # ~len(text)//4 estimate, per the memory doc
_PRESSURE_TOKENS = 8000  # un-consolidated span this big consolidates immediately (Letta)
_SUMMARY_CHARS = 1200  # ~300-token cap on episodic prepend / fallback summary text

_EXTRACT_SYSTEM = (
    "Extract durable facts about the user from this conversation segment. Reply with ONLY a "
    'JSON array: [{"text": "...", "kind": "preference|project|workflow|decision|lesson", '
    '"provenance": "user|inferred"}]. provenance is "user" only if the user explicitly '
    "stated the fact. Do NOT extract: restatements of what the assistant said, tool-call "
    "noise or results, transient task state, or things the user only asked about. "
    "Reply [] if there is nothing durable."
)

_SUMMARY_SYSTEM = (
    "Summarize this conversation segment as ONLY a JSON object: "
    '{"summary": "2-3 sentences: what happened and why", "key_points": [...], "open_loops": [...]}.'
)

# Consolidation runs off the hot path. `_dirty` holds the latest full message
# list + api_key + broadcast per conversation; `_idle_tasks` the armed idle
# timers (cancelled/re-armed on each turn, cancelled on flush); `_bg_tasks`
# keeps pressure/idle tasks referenced so exceptions are observed; a per-cid
# lock serializes overlapping triggers (idle vs flush vs startup recovery).
_dirty: dict[str, dict] = {}
_idle_tasks: dict[str, asyncio.Task] = {}
_consolidate_locks: dict[str, asyncio.Lock] = {}
_bg_tasks: set[asyncio.Task] = set()


def _idle_seconds() -> float:
    return float(os.environ.get("HALO_MEMORY_IDLE_S", "1800"))


def _observe(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("background memory task failed: %r", task.exception())


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    task.add_done_callback(_observe)
    return task


def belief_frame(row: dict) -> dict:
    """Store belief row -> exact belief_state payload (contract is frozen)."""
    frame = {
        "belief_id": row["belief_id"],
        "text": row["text"],
        "kind": row["kind"] if row.get("kind") in _KINDS else "preference",
        "provenance": row["provenance"],
        "salience": float(row["salience"]),
        "status": row["status"],
    }
    if row.get("superseded_by"):
        frame["superseded_by"] = row["superseded_by"]
    if row.get("last_used_at"):
        frame["used_at"] = row["last_used_at"]
    return frame


# ------------------------------------------------------------- extraction --


def _contradicts(a: str, b: str) -> bool:
    # ponytail: naive heuristic -- same first 3 words, different tail. The
    # upgrade is a light-model "does A contradict B?" yes/no call riding the
    # same dedupe pass; heuristic-only until extraction quality demands it.
    aw, bw = a.lower().split(), b.lower().split()
    return len(aw) > 3 and len(bw) > 3 and aw[:3] == bw[:3] and aw[3:] != bw[3:]


def _stub_candidates(last_user: str) -> list[dict]:
    # ponytail: offline test seam (HALO_EXTRACT_STUB) -- "remember: ..." lines
    # become user facts, "remember(inferred): ..." inferred. Real extraction
    # is the LLM path in _llm_span_candidates.
    out = []
    for line in last_user.splitlines():
        line = line.strip()
        if line.startswith("remember(inferred):"):
            out.append({"text": line[len("remember(inferred):"):].strip(), "kind": "preference", "provenance": "inferred"})
        elif line.startswith("remember:"):
            out.append({"text": line[len("remember:"):].strip(), "kind": "preference", "provenance": "user"})
    return out


def _render_span(span: list[dict]) -> str:
    """The span as text for extraction/summary, WITHOUT tool results. Beliefs
    and summaries come from what the user and assistant said; a `role: "tool"`
    dir_list/file_read payload carries no durable fact and the extraction prompt
    is told to ignore it anyway -- so don't pay to send it (systemdesign/14 B5;
    measured ~12,656-token surcharge on a file-heavy span)."""
    return "\n".join(
        f"{m.get('role')}: {m.get('content')}" for m in span if m.get("role") != "tool"
    )


async def _llm_span_candidates(span: list[dict], api_key: str) -> list[dict]:
    """One extraction call over the WHOLE un-consolidated span (v2), not the
    last-10 window. Malformed/empty reply -> [] (rule 5)."""
    convo = _render_span(span)
    prompt = [{"role": "system", "content": _EXTRACT_SYSTEM}, {"role": "user", "content": convo}]
    parts: list[str] = []
    async for delta in llm.stream_chat(prompt, llm.LIGHT, api_key):
        parts.append(delta)
    text = "".join(parts)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        logger.info("extraction reply had no JSON array; skipping (rule 5)")
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.info("extraction reply was not valid JSON; skipping (rule 5)")
        return []
    cands = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            continue
        cands.append(
            {
                "text": item["text"].strip(),
                "kind": item.get("kind") if item.get("kind") in _KINDS else "preference",
                "provenance": item.get("provenance") if item.get("provenance") in ("user", "inferred") else "inferred",
            }
        )
    return cands


async def _extract_span(span: list[dict], api_key: str) -> list[dict]:
    if os.environ.get("HALO_EXTRACT_STUB"):
        joined = "\n".join(m.get("content", "") for m in span if m.get("role") == "user")
        return _stub_candidates(joined)
    return await _llm_span_candidates(span, api_key)


def _can_replace(cand_provenance: str, target_provenance: str) -> bool:
    """Rule 6 for UPDATE: a user statement may replace anything; an inference
    may replace only another inference."""
    return cand_provenance == "user" or target_provenance == "inferred"


async def _decide(cand: dict, neighbors: list[dict], api_key: str) -> tuple[str, str | None]:
    """AUDN decision (mem0): (op, target_id). op in add|update|invalidate|noop.

    Under the extract stub, or whenever there is no real key, the v1 distance +
    first-3-words heuristics ARE the deterministic decision function (contradict
    -> invalidate, near-duplicate -> noop, else add). The real per-candidate
    light-model call is used only with a real key."""
    dup = next(
        (h for h in neighbors if h["text"] == cand["text"]
         or (h.get("distance") is not None and h["distance"] < _DUP_DIST)),
        None,
    )
    contra = next((h for h in neighbors if _contradicts(cand["text"], h["text"])), None)
    if os.environ.get("HALO_EXTRACT_STUB") or not api_key or api_key == "stub-key":
        if contra is not None:
            return "invalidate", contra["belief_id"]
        if dup is not None:
            return "noop", dup["belief_id"]
        return "add", None
    # ponytail: real per-candidate AUDN -- one light-model call, strict JSON.
    # Untested offline (no gate exercises a real key); malformed -> ADD if no
    # close neighbor else NOOP, a fail-safe that never risks a wrong overwrite.
    return await _llm_decide(cand, neighbors, dup, api_key)


async def _llm_decide(cand: dict, neighbors: list[dict], dup: dict | None, api_key: str) -> tuple[str, str | None]:
    listing = "\n".join(f'{i}. ({n["belief_id"]}) {n["text"]}' for i, n in enumerate(neighbors))
    system = (
        "Decide how a new fact relates to existing memories. Reply with ONLY a JSON object "
        '{"op": "add|update|invalidate|noop", "target_id": "<id or null>"}. '
        "add: no equivalent exists. update: complements/corrects one. invalidate: contradicts one. "
        "noop: duplicate or not durable."
    )
    user = f'New fact: {cand["text"]}\nExisting:\n{listing or "(none)"}'
    parts: list[str] = []
    try:
        async for delta in llm.stream_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], llm.LIGHT, api_key
        ):
            parts.append(delta)
        text = "".join(parts)
        obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
        op = obj.get("op")
        target = obj.get("target_id")
        valid = {n["belief_id"] for n in neighbors}
        if op in ("update", "invalidate", "noop") and target in valid:
            return op, target
        if op == "add":
            return "add", None
    except Exception:  # noqa: BLE001 - fail-safe below
        logger.info("AUDN decision parse failed; falling back to add/noop")
    return ("noop", dup["belief_id"]) if dup is not None else ("add", None)


def _execute_decision(cand: dict, op: str, target: str | None) -> list[tuple[dict, str | None]]:
    """Sync store work (run via to_thread). Returns (changed_row, narrate_text)."""
    store.connect()
    if op == "invalidate" and target:
        new_id, superseded = store.add_candidate_belief(
            cand["text"], cand["kind"], cand["provenance"], supersede_id=target
        )
        if superseded:
            return [(store.get_belief(target), None),
                    (store.get_belief(new_id), f"updated what I remember — {cand['text']}")]
        # Provenance rule (rule 6): inferred can't displace user-stated. Keep both.
        logger.info("supersede refused (provenance): kept both beliefs")
        return [(store.get_belief(new_id), None)]
    if op == "update" and target:
        tgt = store.get_belief(target)
        if tgt and _can_replace(cand["provenance"], tgt["provenance"]):
            store.update_belief(target, cand["text"])
            return [(store.get_belief(target), None)]
        # ponytail: unlawful provenance replacement -> downgrade to ADD (never lose the fact).
        new_id, _ = store.add_candidate_belief(cand["text"], cand["kind"], cand["provenance"])
        return [(store.get_belief(new_id), None)]
    if op == "noop" and target:
        store.bump_salience([target])
        return [(store.get_belief(target), None)]
    new_id, _ = store.add_candidate_belief(cand["text"], cand["kind"], cand["provenance"])
    return [(store.get_belief(new_id), None)]


async def _write_summary(conversation_id: str, span: list[dict], api_key: str) -> None:
    if os.environ.get("HALO_EXTRACT_STUB"):
        first_user = next((m.get("content", "") for m in span if m.get("role") == "user"), "")
        text, key_points = ("session: " + first_user)[:_SUMMARY_CHARS], {}
    else:
        text, key_points = await _llm_summary(span, api_key)
    await asyncio.to_thread(store.add_session_summary, conversation_id, text, key_points)


async def _llm_summary(span: list[dict], api_key: str) -> tuple[str, dict]:
    """Best-effort episodic summary. Soft-fails to a first-user-line fallback --
    a bad parse must NOT raise, or the cursor would never advance (rule 5)."""
    convo = _render_span(span)
    parts: list[str] = []
    try:
        async for delta in llm.stream_chat(
            [{"role": "system", "content": _SUMMARY_SYSTEM}, {"role": "user", "content": convo}],
            llm.LIGHT, api_key,
        ):
            parts.append(delta)
        text = "".join(parts)
        obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
        summary = (obj.get("summary") or "").strip()
        key_points = {k: obj[k] for k in ("key_points", "open_loops") if obj.get(k)}
        if summary:
            return summary[:_SUMMARY_CHARS], key_points
    except Exception:  # noqa: BLE001 - fall through to the fallback
        logger.info("summary generation failed; using fallback")
    first_user = next((m.get("content", "") for m in span if m.get("role") == "user"), "")
    return ("session: " + first_user)[:_SUMMARY_CHARS], {}


async def consolidate_span(conversation_id: str, messages: list[dict], api_key: str, broadcast) -> bool:
    """Core off-hot-path pass over `messages[cursor:]`. Never raises (same
    contract as v1 extract). Advances the cursor ONLY on full success, so any
    failure retries the same span next trigger (rule 5). Shared by the idle/
    pressure/flush triggers and by startup recovery. Returns True when the span
    is consolidated (an empty span counts -- there is nothing left to retry)."""
    lock = _consolidate_locks.setdefault(conversation_id, asyncio.Lock())
    async with lock:
        try:
            meta = await asyncio.to_thread(store.get_conversation_meta, conversation_id)
            cursor = (meta or {}).get("consolidation_cursor") or 0
            span = messages[cursor:]
            if not span:
                return True  # empty span -> silent no-op (idle/flush/recovery all land here)
            cands = await _extract_span(span, api_key)
            changed: list[tuple[dict, str | None]] = []
            for cand in cands:
                neighbors = await asyncio.to_thread(store.search_beliefs, cand["text"], 5)
                op, target = await _decide(cand, neighbors, api_key)
                changed.extend(await asyncio.to_thread(_execute_decision, cand, op, target))
            for row, narrate_text in changed:
                await broadcast("belief_state", belief_frame(row))
                if narrate_text:
                    await broadcast(
                        "activity", {"text": narrate_text, "narrate": True, "task_id": "memory", "undoable": False}
                    )
            await _write_summary(conversation_id, span, api_key)
            await asyncio.to_thread(store.set_consolidation_cursor, conversation_id, len(messages))
            return True
        except Exception:  # noqa: BLE001 - rule 5: cursor stays put, retry next trigger
            logger.exception("consolidation failed for %s (cursor not advanced)", conversation_id)
            return False


async def consolidate(conversation_id: str) -> None:
    """Consolidate a dirty conversation from its stashed `_dirty` entry."""
    entry = _dirty.get(conversation_id)
    if entry is None:
        return
    ok = await consolidate_span(
        conversation_id, entry["messages"], entry["api_key"], entry["broadcast"]
    )
    # Drop the entry once it is consolidated: it pins the conversation's whole
    # message list AND a live broadcast closure (holding the websocket) for the
    # process lifetime otherwise. Identity check, not a bare pop -- a turn that
    # landed while we were awaiting has already stashed a newer entry, and that
    # one still needs its own pass.
    if ok and _dirty.get(conversation_id) is entry:
        _dirty.pop(conversation_id, None)


async def _idle_then_consolidate(conversation_id: str) -> None:
    try:
        await asyncio.sleep(_idle_seconds())
    except asyncio.CancelledError:
        return
    _idle_tasks.pop(conversation_id, None)
    await consolidate(conversation_id)


async def note_turn(conversation_id: str, messages: list[dict], api_key: str, broadcast) -> None:
    """Called after each successful turn (fire-and-forget from graph). Cheap:
    mark the conversation dirty + record its message count, then (re)arm the
    idle-consolidation timer. Pressure trigger (Letta): a large un-consolidated
    span consolidates immediately instead of waiting for the clock. Never raises."""
    try:
        _dirty[conversation_id] = {"messages": messages, "api_key": api_key, "broadcast": broadcast}
        await asyncio.to_thread(store.note_conversation_activity, conversation_id, len(messages))
        meta = await asyncio.to_thread(store.get_conversation_meta, conversation_id)
        cursor = (meta or {}).get("consolidation_cursor") or 0
        est = sum(len(str(m)) // 4 for m in messages[cursor:])
        old = _idle_tasks.pop(conversation_id, None)
        if old is not None:
            old.cancel()
        if est > _PRESSURE_TOKENS:
            _spawn(consolidate(conversation_id))
            # Fall through and arm the idle timer anyway: a failed pressure pass
            # left nothing pending, so the span waited for the next turn to get
            # another chance. On success the timer fires into a popped `_dirty`
            # and no-ops; _consolidate_locks serializes the overlap either way.
        _idle_tasks[conversation_id] = _spawn(_idle_then_consolidate(conversation_id))
    except Exception:  # noqa: BLE001 - rule 5: never surface as a turn error
        logger.exception("note_turn failed (turn unaffected)")


async def flush_all() -> None:
    """Shutdown hook: cancel every armed idle timer, then consolidate every
    dirty conversation now (an already-consolidated span is a silent no-op)."""
    for task in list(_idle_tasks.values()):
        task.cancel()
    _idle_tasks.clear()
    for conversation_id in list(_dirty.keys()):
        await consolidate(conversation_id)
    _dirty.clear()


# -------------------------------------------------------------- retrieval --


def retrieve(user_text: str) -> list[dict]:
    """Sync (call via to_thread). Top-15-or-~1k-tokens, relevance x salience
    when vector scores exist (else store order). Bumps salience + last_used_at
    on the injected rows (store.bump_salience does both)."""
    store.connect()
    rows = store.search_beliefs(user_text, k=15)
    if any(r.get("distance") is not None for r in rows):
        rows.sort(key=lambda r: (1.0 / (1.0 + (r.get("distance") or 0.0))) * r["salience"], reverse=True)
    out: list[dict] = []
    budget = _BUDGET_TOKENS
    for row in rows:
        cost = len(row["text"]) // 4 + 1
        if cost > budget:
            break
        budget -= cost
        out.append(row)
    if out:
        store.bump_salience([r["belief_id"] for r in out])
    return out


def episodic_prepend(conversation_id: str) -> str | None:
    """Most recent prior session summary for this conversation, ~300-token
    capped, for continuity across sessions. ponytail: recency-only, no summary
    vector search yet (memory doc marks that a later step). Returns None if the
    conversation has no summary yet."""
    store.connect()
    row = store.latest_session_summary(conversation_id)
    if not row or not row.get("text"):
        return None
    return row["text"][:_SUMMARY_CHARS]


# ------------------------------------------------------------------ decay --


async def run_decay(broadcast) -> list[str]:
    def _decay() -> list[str]:
        store.connect()
        half = store.get_setting("memory_half_life_days", 30)
        below = store.get_setting("memory_archive_below", 0.2)
        return store.decay(half, below)

    archived = await asyncio.to_thread(_decay)
    for belief_id in archived:
        row = await asyncio.to_thread(store.get_belief, belief_id)
        if row:
            await broadcast("belief_state", belief_frame(row))
    return archived


async def decay_loop(broadcast) -> None:
    """Once at Brain start, then daily."""
    while True:
        try:
            await run_decay(broadcast)
        except Exception:  # noqa: BLE001 - decay must never kill the server
            logger.exception("belief decay pass failed")
        await asyncio.sleep(86400)


# ------------------------------------------------------------------ panel --


async def handle_memory_edit(msg: dict, broadcast) -> None:
    """Panel round-trips: edit/delete/restore against the store. Each success
    broadcasts the confirming belief_state delta (rule-3 unlock)."""
    belief_id, op = msg["belief_id"], msg["op"]
    store.connect()
    row = await asyncio.to_thread(store.get_belief, belief_id)
    if row is None:
        await broadcast(
            "error", {"code": "belief_not_found", "message": "I couldn't find that memory.", "recoverable": True,
                      "operation_kind": "memory_edit", "operation_id": belief_id}
        )
        return
    if op == "edit":
        text = msg.get("text")
        if not text:
            await broadcast(
                "error", {"code": "memory_edit_invalid", "message": "Editing a memory needs new text.", "recoverable": True,
                          "operation_kind": "memory_edit", "operation_id": belief_id}
            )
            return
        # A human edit is a new user statement, never an in-place rewrite:
        # preserve the prior version and close its validity window atomically.
        replacement_id, _ = await asyncio.to_thread(
            store.add_candidate_belief,
            text,
            row["kind"],
            "user",
            supersede_id=belief_id,
        )
        await broadcast("belief_state", belief_frame(await asyncio.to_thread(store.get_belief, belief_id)))
        await broadcast(
            "belief_state",
            belief_frame(await asyncio.to_thread(store.get_belief, replacement_id)),
        )
        return
    elif op == "delete":
        await asyncio.to_thread(store.set_belief_status, belief_id, "archived")
    elif op == "restore":
        restored = await asyncio.to_thread(store.restore_belief, belief_id)
        for changed in restored:
            await broadcast("belief_state", belief_frame(changed))
        return
    elif op == "purge":
        try:
            deleted = await asyncio.to_thread(store.purge_belief, belief_id)
        except ValueError:
            await broadcast(
                "error",
                {
                    "code": "memory_purge_invalid",
                    "message": "Only archived memories can be permanently deleted.",
                    "recoverable": True,
                    "operation_kind": "memory_edit",
                    "operation_id": belief_id,
                },
            )
            return
        if deleted:
            await broadcast("belief_deleted", {"belief_id": belief_id})
        return
    await broadcast("belief_state", belief_frame(await asyncio.to_thread(store.get_belief, belief_id)))


async def push_beliefs(send) -> None:
    """Connect-time hydration (v2): live beliefs ONLY -- invalid_at IS NULL,
    status active, salience-ranked, capped at 50. Dead rows (superseded/archived)
    and full supersede history are fetched on demand by the panel, never replayed
    on every connect (that was v1's 60%-dead firehose)."""
    store.connect()
    rows = await asyncio.to_thread(store.live_beliefs, 50)
    for row in rows:
        await send("belief_state", belief_frame(row))


async def push_history(send) -> None:
    """On-demand panel hydration for durable archived/superseded history."""
    store.connect()
    await send("memory_history_state", {"complete": False})
    rows = await asyncio.to_thread(store.list_beliefs)
    for row in rows:
        if row["status"] in {"archived", "superseded"}:
            await send("belief_state", belief_frame(row))
    await send("memory_history_state", {"complete": True})
