"""Memory: extraction, retrieval, decay, panel round-trips (Phase 2 Step 8).

Write path: end of a successful turn -> light-model extraction -> dedupe /
supersede against the store (provenance enforced there, rule 6). Extraction
failure of any kind skips the write and never touches existing rows (rule 5).
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

_EXTRACT_SYSTEM = (
    "Extract durable facts about the user from this conversation. Reply with ONLY a "
    'JSON array: [{"text": "...", "kind": "preference|project|workflow|decision|lesson", '
    '"provenance": "user|inferred"}]. provenance is "user" only if the user explicitly '
    'stated the fact. Reply [] if there is nothing durable.'
)


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
    # is the LLM path in _llm_candidates.
    out = []
    for line in last_user.splitlines():
        line = line.strip()
        if line.startswith("remember(inferred):"):
            out.append({"text": line[len("remember(inferred):"):].strip(), "kind": "preference", "provenance": "inferred"})
        elif line.startswith("remember:"):
            out.append({"text": line[len("remember:"):].strip(), "kind": "preference", "provenance": "user"})
    return out


async def _llm_candidates(messages: list[dict], api_key: str) -> list[dict]:
    convo = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages[-10:])
    prompt = [{"role": "system", "content": _EXTRACT_SYSTEM}, {"role": "user", "content": convo}]
    parts: list[str] = []
    usage: dict = {}
    async for delta in llm.stream_chat(prompt, llm.LIGHT, api_key, usage):
        parts.append(delta)
    if usage.get("cost"):
        # ponytail: month rollup only -- extraction runs fire-and-forget after
        # the turn closed, so there's no broadcast to ride and the session
        # counter belongs to graph. The next turn's spend_update includes it.
        await asyncio.to_thread(store.add_spend, usage["cost"])
    text = "".join(parts)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        logger.info("extraction reply had no JSON array; skipping (rule 5)")
        return []
    raw = json.loads(text[start : end + 1])
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


def _apply_candidates(cands: list[dict]) -> list[tuple[dict, str | None]]:
    """Sync store work (run via to_thread). Returns (changed_row, narrate_text)."""
    store.connect()
    changed: list[tuple[dict, str | None]] = []
    for cand in cands:
        hits = store.search_beliefs(cand["text"], k=3)
        contra = next((h for h in hits if _contradicts(cand["text"], h["text"])), None)
        if contra is not None:
            new_id = store.add_belief(cand["text"], cand["kind"], cand["provenance"])
            try:
                store.supersede(contra["belief_id"], new_id)
                changed.append((store.get_belief(contra["belief_id"]), None))
                changed.append((store.get_belief(new_id), f"updated what I remember — {cand['text']}"))
            except ValueError:
                # Provenance rule (rule 6): inferred can't displace user-stated.
                # Keep both, log honestly.
                logger.info("supersede refused (provenance): kept both beliefs")
                changed.append((store.get_belief(new_id), None))
            continue
        dup = next(
            (h for h in hits if h["text"] == cand["text"] or (h.get("distance") is not None and h["distance"] < _DUP_DIST)),
            None,
        )
        if dup is not None:
            store.bump_salience([dup["belief_id"]])
            changed.append((store.get_belief(dup["belief_id"]), None))
            continue
        new_id = store.add_belief(cand["text"], cand["kind"], cand["provenance"])
        changed.append((store.get_belief(new_id), None))
    return changed


async def extract(conversation_id: str, messages: list[dict], api_key: str, broadcast) -> None:
    """Fire-and-forget at end of a successful turn. Never raises -- the turn
    already completed; an extraction crash must not surface as a turn error."""
    try:
        last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        if len(last_user.strip()) < 20:
            return  # trivially transient turn
        if os.environ.get("HALO_EXTRACT_STUB"):
            cands = _stub_candidates(last_user)
        else:
            cands = await _llm_candidates(messages, api_key)
        if not cands:
            return
        changed = await asyncio.to_thread(_apply_candidates, cands)
        for row, narrate_text in changed:
            await broadcast("belief_state", belief_frame(row))
            if narrate_text:
                await broadcast(
                    "activity", {"text": narrate_text, "narrate": True, "task_id": "memory", "undoable": False}
                )
    except Exception:  # noqa: BLE001 - rule 5: log, never touch the turn
        logger.exception("belief extraction failed (turn unaffected)")


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
            "error", {"code": "belief_not_found", "message": "I couldn't find that memory.", "recoverable": True}
        )
        return
    if op == "edit":
        text = msg.get("text")
        if not text:
            await broadcast(
                "error", {"code": "memory_edit_invalid", "message": "Editing a memory needs new text.", "recoverable": True}
            )
            return
        # A human typed it -> provenance user; update_belief re-embeds.
        await asyncio.to_thread(store.update_belief, belief_id, text, "user")
    elif op == "delete":
        await asyncio.to_thread(store.set_belief_status, belief_id, "archived")
    elif op == "restore":
        await asyncio.to_thread(store.set_belief_status, belief_id, "active")
    await broadcast("belief_state", belief_frame(await asyncio.to_thread(store.get_belief, belief_id)))


async def push_beliefs(send, limit: int = 100) -> None:
    """Connect-time hydration: replay beliefs (all statuses -- the panel
    renders supersede chains) as belief_state frames, oldest first."""
    store.connect()
    rows = await asyncio.to_thread(store.list_beliefs)
    for row in reversed(rows[:limit]):
        await send("belief_state", belief_frame(row))
