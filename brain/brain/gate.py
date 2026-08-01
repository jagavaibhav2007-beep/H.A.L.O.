"""Permission gate & tool registry (Phase 2 Step 5, D4).

One choke point every tool call passes through: classify -> Tier 1 run+log,
Tier 2 run+log+activity, Tier 3 LangGraph interrupt() -> approval_request ->
approve/deny/edit resume. Unknown tool, out-of-range tier, or a classification
exception all fail closed to Tier 3 (rule 8).

gated_execute runs INSIDE a graph node: interrupt() raises GraphInterrupt,
which must bubble to LangGraph's checkpointer -- never wrap the interrupt()
call in a broad except (see graph.py C2 note).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import uuid
from pathlib import Path

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from brain import store

logger = logging.getLogger("brain.gate")

# Tool registry: name -> {fn, tier, destructive, redact, summary, inverse}.
# tier: int or callable(args)->int (arg-predicate rules; real file rules
# arrive with the Step 7 tools). destructive: bool or callable(args)->bool.
# redact: callable(args)->dict producing args_redacted (rule 3 -- applied
# before anything leaves the gate). summary: str template or callable(args).
# inverse: callable(args, result) -> {tool, args, precondition?} | None --
# built AT EXECUTION TIME (D7); precondition is {"path", "sha256"?} data the
# undo re-checks ("still safe to reverse?"). No inverse (or builder returns
# None) => genuinely non-reversible, undoable:false up front.
TOOLS: dict[str, dict] = {}

# Pending Tier-3 approvals: approval_id -> {conversation_id, task_id, payload}
# plus the reverse map for interrupt's implicit-deny rule.
# ponytail: in-memory only -- rebuilt after a restart from the LangGraph
# checkpoints themselves (graph.rehydrate_pending, Step 9), which are the one
# source of truth; nothing about this map is separately persisted.
_pending: dict[str, dict] = {}
_by_conversation: dict[str, str] = {}


def register(
    name: str, fn, *, tier=3, destructive=False, redact=None, summary=None,
    inverse=None, schema=None, mutating: bool = False, user_intent=None,
    task: bool = False, supports_pause: bool = False, title=None, steps_total=None,
) -> None:
    """schema: the OpenAI function body sans name -- {"description": str,
    "parameters": <JSON Schema>}. Only tools WITH one are advertised to the
    model (tool_specs); schema-less tools stay internal-only."""
    TOOLS[name] = {
        "fn": fn, "tier": tier, "destructive": destructive, "redact": redact,
        "summary": summary, "inverse": inverse, "schema": schema,
        "mutating": mutating, "user_intent": user_intent,
        "task": task, "supports_pause": supports_pause,
        "title": title, "steps_total": steps_total,
    }


def tool_specs() -> list[dict]:
    """What the model is offered. Tier is NOT expressed here on purpose: the
    model asks for whatever it wants and the gate decides -- advertising tiers
    would invite the model to shop for a cheaper one."""
    return [
        {"type": "function", "function": {"name": name, **entry["schema"]}}
        for name, entry in TOOLS.items()
        if entry["schema"]
    ]


def classify(tool: str, args: dict) -> int:
    """Pure tier rule. Fail closed: unknown tool, bad tier value, or any
    classification exception -> Tier 3 (rule 8)."""
    entry = TOOLS.get(tool)
    if entry is None:
        return 3
    try:
        tier = entry["tier"]
        if callable(tier):
            tier = tier(args)
        return tier if tier in (1, 2, 3) else 3
    except Exception:  # noqa: BLE001 - rule 8: classification exception -> ask
        logger.exception("classify failed for tool=%s; defaulting to Tier 3", tool)
        return 3


def is_mutating(tool: str) -> bool:
    entry = TOOLS.get(tool)
    return bool(entry and entry.get("mutating"))


def classify_for_request(tool: str, args: dict, user_text: str) -> int:
    """Bind approval-free mutations to the current human request.

    Model output, retrieved memory, summaries, and file/tool content can suggest
    a tool call, but none of them are authority. A mutating Tier-1/2 call stays
    approval-free only when its registry predicate finds explicit operation and
    target evidence in the current user message. Missing/broken evidence fails
    closed to Tier 3; already-Tier-3 calls remain Tier 3.
    """
    tier = classify(tool, args)
    entry = TOOLS.get(tool)
    if tier == 3 or not entry or not entry.get("mutating"):
        return tier
    if os.environ.get("HALO_LLM_STUB") and user_text.startswith(f"CALL_TOOL {tool} "):
        # Deterministic test seam: the human fixture names the exact tool/args.
        # Real-provider turns can never enter graph._route_node's sentinel path.
        return tier
    predicate = entry.get("user_intent")
    try:
        return tier if predicate and predicate(args, user_text) else 3
    except Exception:  # noqa: BLE001 - authority checks always fail closed
        logger.exception("user-intent check failed for tool=%s; requiring approval", tool)
        return 3
def is_destructive(tool: str, args: dict) -> bool:
    entry = TOOLS.get(tool)
    if entry is None:
        return False  # unknown is already Tier 3; destructive adds hold-to-approve only
    try:
        flag = entry["destructive"]
        return bool(flag(args)) if callable(flag) else bool(flag)
    except Exception:  # noqa: BLE001 - when in doubt, the scarier card
        return True


def summarize(tool: str, args: dict) -> str:
    """The one plain first-person sentence the approval card leads with --
    the Brain authors it, the UI holds no business logic (rule 10)."""
    entry = TOOLS.get(tool)
    if entry is not None and entry["summary"] is not None:
        try:
            s = entry["summary"]
            return s(args) if callable(s) else s.format(**args)
        except Exception:  # noqa: BLE001 - a bad template never blocks the card
            logger.exception("summary failed for tool=%s", tool)
    return f"I want to run {tool}."


def redact(tool: str, args: dict) -> dict:
    entry = TOOLS.get(tool)
    if entry is None:
        # Unknown model-emitted tools fail closed at Tier 3. Their arbitrary
        # argument shape has no trustworthy redactor, so never echo it into an
        # approval frame, checkpoint, or action row.
        return {}
    redactor = entry["redact"] if entry is not None else None
    if redactor is None:
        return dict(args)
    try:
        return redactor(args)
    except Exception:  # noqa: BLE001 - a broken redactor must not leak raw args
        logger.exception("redactor failed for tool=%s; dropping args", tool)
        return {}


def _apply_redacted_edits(original, redacted, edited):
    """Apply user edits without turning display-only redaction placeholders
    into executable arguments.

    The approval contract deliberately exposes only ``args_redacted``. When an
    editor leaves a redacted value unchanged, that means "preserve the original
    value", not "replace it with '<15 chars>'". Changed values remain explicit
    edits, omitted object keys remain omitted, and nested objects/lists follow
    the same rule.
    """
    if edited == redacted:
        return original
    if isinstance(edited, dict) and isinstance(redacted, dict):
        source = original if isinstance(original, dict) else {}
        return {
            key: _apply_redacted_edits(source.get(key), redacted.get(key), value)
            for key, value in edited.items()
        }
    if isinstance(edited, list) and isinstance(redacted, list):
        source = original if isinstance(original, list) else []
        return [
            _apply_redacted_edits(
                source[index] if index < len(source) else None,
                redacted[index] if index < len(redacted) else None,
                value,
            )
            for index, value in enumerate(edited)
        ]
    return edited


# ------------------------------------------------- pending approval registry


def register_pending(payload: dict, conversation_id: str) -> None:
    aid = payload["approval_id"]
    _pending[aid] = {"conversation_id": conversation_id, "task_id": payload["task_id"], "payload": payload}
    _by_conversation[conversation_id] = aid


def pending_payloads() -> list[dict]:
    """Every still-open approval's card payload -- what snapshot-on-connect
    re-emits. Read from the live map (not from a rehydration delta), so a
    second window connecting to an already-rehydrated Brain still sees it."""
    return [entry["payload"] for entry in _pending.values()]


def peek_conversation(approval_id: str) -> str | None:
    entry = _pending.get(approval_id)
    return entry["conversation_id"] if entry else None


def pop_pending(approval_id: str) -> dict | None:
    entry = _pending.pop(approval_id, None)
    if entry is not None:
        _by_conversation.pop(entry["conversation_id"], None)
    return entry


def pending_for_conversation(conversation_id: str) -> str | None:
    return _by_conversation.get(conversation_id)


# ----------------------------------------------------------------- the gate


def _record(
    tool: str, args_redacted: dict, tier: int, result: str, task_id: str | None,
    undoable: bool = False, inverse: dict | None = None,
) -> str | None:
    """Write the action row; returns the undo_token iff undoable."""
    store.connect()
    _, undo_token = store.record_action(tool, args_redacted, tier, 1, result, undoable, inverse, task_id)
    return undo_token


async def gated_execute(
    tool: str,
    args: dict,
    *,
    conversation_id: str,
    task_id: str | None,
    broadcast,
    user_text: str | None = None,
) -> dict:
    """The ONE path every tool call takes. Returns the graph-state update.

    Tier 3 calls interrupt(): LangGraph suspends here (GraphInterrupt bubbles
    up), run_turn emits the approval_request, and resume_turn re-enters this
    node from the top -- classify/redact re-run (pure), interrupt() then
    returns the approval_response decision instead of raising.
    """
    entry = TOOLS.get(tool)
    if entry and entry.get("task") and task_id is None:
        from brain.task_runtime import new_task_id

        frame_task = new_task_id(tool.replace("_", "-"))
    else:
        frame_task = task_id or f"tool-{conversation_id}"  # contract: task_id is required in these frames
    while True:
        tier = classify_for_request(tool, args, user_text) if user_text is not None else classify(tool, args)
        if tier != 3:
            break
        payload = {
            "approval_id": str(uuid.uuid4()),
            "tool": tool,
            "args_redacted": redact(tool, args),
            "tier": 3,
            "task_id": frame_task,
            "summary": summarize(tool, args),
            "destructive": is_destructive(tool, args),
            # Optional in the contract, but it's what lets the UI route this
            # card's "Stop this task" (an `interrupt`, keyed by conversation)
            # to the right thread when several are open. Set here so it rides
            # along everywhere the payload does: the live broadcast, the
            # _pending map, and snapshot's pending_payloads() replay -- and
            # since it's inside the interrupt() value, the checkpoint keeps it
            # across a restart for rehydrate_pending too.
            "conversation_id": conversation_id,
        }
        decision = interrupt(payload)  # C2: never inside a try/except
        d = decision.get("decision")
        if d in ("deny", "cancelled"):
            await asyncio.to_thread(_record, tool, redact(tool, args), tier, "denied", frame_task)
            note = "you denied it" if d == "deny" else "you stopped me"
            update = {
                "pending_tool_result": {"tool": tool, "status": d},
                "messages": [{"role": "assistant", "content": f"I didn't run {tool} — {note}."}],
            }
            if d == "cancelled":
                update["redirected"] = True  # same sticky the streaming interrupt sets
            return update
        if d == "edit":
            # Re-classify the edited args -- an edit can RAISE the tier (or
            # stay Tier 3, which emits a fresh approval card for what will
            # actually run); it never silently lowers it.
            edited_args = decision.get("edited_args")
            if edited_args is not None:
                args = _apply_redacted_edits(args, redact(tool, args), edited_args)
            continue
        break  # approve

    if entry and entry.get("task"):
        return await _start_task_tail(
            tool, args, tier, frame_task, conversation_id, broadcast,
        )
    return await _execute_tail(tool, args, tier, frame_task, broadcast)


_RESULT_CAP = 8 * 1024  # serialized tool-result bytes fed back to the model


async def _start_task_tail(
    tool: str,
    args: dict,
    tier: int,
    task_id: str,
    conversation_id: str,
    broadcast,
) -> dict:
    from brain import task_runtime

    runtime = task_runtime.current()
    entry = TOOLS[tool]
    if runtime is None:
        return {
            "pending_tool_result": {"tool": tool, "status": "error: task runtime unavailable"},
            "messages": [{"role": "assistant", "content": f"I couldn't start {tool} because the task runtime is unavailable."}],
        }

    def value(meta, default=None):
        return meta(args) if callable(meta) else (meta if meta is not None else default)

    title = value(entry.get("title"), summarize(tool, args))
    total = value(entry.get("steps_total"))
    await runtime.submit(
        task_id=task_id,
        conversation_id=conversation_id,
        tool=tool,
        args=args,
        args_redacted=redact(tool, args),
        tier=tier,
        lane=1,
        title=title,
        steps_total=total,
        supports_pause=bool(entry.get("supports_pause")),
        fn=entry["fn"],
        inverse_builder=entry.get("inverse"),
    )
    return {
        "pending_tool_result": {"tool": tool, "status": "started", "task_id": task_id},
        "messages": [{"role": "assistant", "content": f"Started task {task_id} for {tool}."}],
    }


def _cap_result(out) -> tuple[str, int]:
    """Serialize a tool result under _RESULT_CAP, returning (json, omitted).

    Slicing already-serialized JSON to a byte cap cut the string mid-token, so
    the model got un-parseable output. Instead drop trailing entries until the
    JSON fits, so what remains always parses -- for a list directly, and for a
    dict by trimming its longest list value. The dict case is dir_organize (up
    to 200 moves across `moves`/`skipped`/`failed`), the one unbounded producer
    left now that dir_list/file_search return pre-bounded strings; it used to
    take exactly the mid-string byte slice this exists to avoid. Anything with
    no list to trim falls back to a marked byte-slice rather than silently emit
    invalid JSON.

    Capping here and not in the tool is deliberate: _execute_tail builds the
    undo inverse from the UNCAPPED return, so trimming dir_organize's own
    result would silently destroy the reversal for every dropped move.
    """
    def dump(x) -> str:
        return json.dumps(x, ensure_ascii=False, default=str)

    payload = dump(out)
    if len(payload) <= _RESULT_CAP:
        return payload, 0
    if isinstance(out, list):
        kept = out
        while len(kept) > 1 and len(dump(kept)) > _RESULT_CAP:
            kept = kept[: len(kept) // 2]
        return dump(kept), len(out) - len(kept)
    if isinstance(out, dict):
        kept = dict(out)
        omitted = 0
        while len(dump(kept)) > _RESULT_CAP:
            key = max(
                (k for k, v in kept.items() if isinstance(v, list) and v),
                key=lambda k: len(kept[k]),
                default=None,
            )
            if key is None:
                break  # nothing left to trim: byte-slice below
            trimmed = kept[key][: len(kept[key]) // 2]
            omitted += len(kept[key]) - len(trimmed)
            kept[key] = trimmed
        if omitted and len(dump(kept)) <= _RESULT_CAP:
            return dump(kept), omitted
    return payload[:_RESULT_CAP] + " …[truncated]", 0


async def _execute_tail(tool: str, args: dict, tier: int, frame_task: str, broadcast) -> dict:
    """Non-interrupting execution tail: run fn + record (with inverse, D7) +
    emit activity. Shared by gated_execute and handle_undo -- C-UNDO: undo
    runs OUTSIDE any graph thread, so it must never reach interrupt(); this
    helper contains no interrupt() by construction."""
    entry = TOOLS.get(tool)
    args_redacted = redact(tool, args)
    inverse: dict | None = None
    if entry is None:
        result = "error: unknown tool"
        content = f"I couldn't run {tool} — I don't have that tool."
    else:
        try:
            fn = entry["fn"]
            if inspect.iscoroutinefunction(fn):
                out = await fn(args)
            else:
                # File tools and run_readonly_cmd use blocking filesystem and
                # subprocess APIs. Keep them off the server's event loop so
                # one slow tool cannot stall unrelated conversations.
                out = await asyncio.to_thread(fn, args)
                # Be tolerant of a synchronous adapter that returns an
                # awaitable, while true coroutine functions stay on-loop.
                if inspect.isawaitable(out):
                    out = await out
            result = "ok"
            builder = entry["inverse"]
            if builder is not None:
                try:
                    # ponytail: inverse args stored as built -- no current tool's
                    # redactor hides a value its inverse needs; if one ever does,
                    # its builder must store a redacted-safe form here.
                    inverse = builder(args, out)
                except Exception:  # noqa: BLE001 - a broken builder just means non-undoable
                    logger.exception("inverse builder failed for tool=%s", tool)
            # Surface the tool's actual return to the model. A search that found
            # the file and one that found nothing must not both read "I ran X." --
            # the graph feeds this content back as the role:"tool" message, so a
            # content-free ack is what makes the model confabulate "not found".
            # None-returning tools (e.g. file_delete) keep the bare ack.
            if out is None:
                content = f"I ran {tool}."
            else:
                payload, omitted = _cap_result(out)
                # An empty result needs an explicit "no matches" or the model
                # confabulates "not found" from the content-free ack (see above).
                # Not dead despite no production list-returning tool: file_search
                # et al. can legitimately return [], and check_empty_result guards it.
                extra = " (no matches)" if out == [] else ""
                if omitted:
                    extra += f" [{omitted} more result(s) omitted at the {_RESULT_CAP // 1024}KB cap; narrow the query]"
                content = f"I ran {tool}. Result: {payload}{extra}."
        except GraphInterrupt:
            raise  # C2: a tool must never swallow the suspension signal
        except Exception as exc:  # noqa: BLE001 - rule 2: turn continues honestly
            logger.exception("tool %s failed", tool)
            result = f"error: {exc}"
            content = f"I tried to run {tool} but it failed: {exc}"
    undo_token = await asyncio.to_thread(
        _record, tool, args_redacted, tier, result, frame_task, inverse is not None, inverse
    )
    if result == "ok":
        await broadcast(
            "activity", _activity_frame(tool, args, frame_task, undo_token is not None, tier, 1, undo_token)
        )
    return {
        "pending_tool_result": {"tool": tool, "status": result},
        "messages": [{"role": "assistant", "content": content}],
    }


def _activity_frame(tool, args, task_id, undoable, tier, lane, undo_token=None) -> dict:
    """The 6-key activity payload every feed frame shares (+ undo_token when the
    row is still reversible). Callers derive undoable/token themselves -- live
    execution off the fresh token, backlog replay off the row's consumed state."""
    frame = {
        "text": summarize(tool, args),
        "narrate": False,
        "task_id": task_id,
        "undoable": undoable,
        "tier": tier,
        "lane": lane,
    }
    if undo_token is not None:
        frame["undo_token"] = undo_token
    return frame


# -------------------------------------------------------------------- undo


def _precondition_ok(pre: dict | None) -> bool:
    """Precondition shape: {"path": p, "sha256"?: hex} -- the reversal target
    still exists (and is byte-identical if hashed), so undo won't clobber
    anything that changed since."""
    if not pre:
        return True
    if "batch" in pre:
        entries = pre["batch"]
        return isinstance(entries, list) and bool(entries) and all(
            isinstance(entry, dict) and _precondition_ok(entry) for entry in entries
        )
    p = Path(pre["path"])
    if pre.get("must_be_absent"):
        return not p.exists()
    if not p.is_file():
        return False
    want = pre.get("sha256")
    if want:
        return hashlib.sha256(p.read_bytes()).hexdigest() == want
    return True


async def handle_undo(msg: dict, broadcast) -> None:
    """The feed's Undo button (D7). Looks up the recorded inverse, re-checks
    its precondition, consumes the token (atomic -- a double-fire loses the
    UPDATE race and errors), then runs the inverse through the shared
    execution tail so the reversal is itself logged with its own activity,
    referencing the SAME task_id as the original."""

    async def fail(code: str, message: str) -> None:
        await broadcast("error", {
            "code": code, "message": message, "recoverable": True,
            "operation_kind": "undo", "operation_id": msg["undo_token"],
        })

    token = msg["undo_token"]
    store.connect()
    row = await asyncio.to_thread(store.get_action_by_undo_token, token)
    if row is None:
        await fail("undo_unknown", "I couldn't find that action to undo.")
        return
    if row["consumed"]:
        await fail("undo_already_done", f"I can't undo {row['tool']} again — it's already undone.")
        return
    inverse = json.loads(row["inverse_json"])
    pre_ok = await asyncio.to_thread(_precondition_ok, inverse.get("precondition"))
    if not pre_ok:
        await fail(
            "undo_precondition_failed",
            f"I can't undo {row['tool']} — it's changed since, so I won't overwrite it.",
        )
        return
    # Recorded inverses are pre-authorized: WE built them at execution time of
    # an action that already went through the gate, and the precondition +
    # token consumption guard staleness/replays. Re-classifying here would let
    # arg shapes demote tiers for direct callers (a bypass) or block honest
    # reversals of approved Tier-3 actions -- so trust the record, run Tier 2.
    tier = 2
    # Consume BEFORE executing: the atomic False-on-second-call is what makes
    # a double-click run the inverse exactly once (idempotence, matches the
    # UI's rule-3 lock).
    if not await asyncio.to_thread(store.consume_undo_token, token):
        await fail("undo_already_done", f"I can't undo {row['tool']} again — it's already undone.")
        return
    # The reversal is just an action: if its tool has an inverse builder it
    # gets its own token (undo-of-an-undo comes free), otherwise it's honestly
    # non-undoable.
    result = await _execute_tail(
        inverse["tool"], inverse["args"], tier, row["task_id"] or "history", broadcast
    )
    if result["pending_tool_result"]["status"] != "ok":
        # The claim prevents concurrent double execution, but a failed
        # filesystem inverse remains retryable and must not look successful.
        await asyncio.to_thread(store.release_undo_token, token)
        await fail("undo_failed", f"I couldn't undo {row['tool']} safely.")


async def push_activity_backlog(send, limit: int = 100) -> None:
    """Connect-time feed hydration (Step 6): replay recent action rows as
    activity frames, oldest first (the feed appends, newest lands last). A
    still-unconsumed undo_token rides along so the Undo button re-arms after
    reconnect; consumed/non-undoable rows replay undoable:false."""
    store.connect()
    rows = await asyncio.to_thread(store.recent_actions, limit)
    for row in reversed(rows):
        args = json.loads(row["args_redacted"] or "{}")
        undoable = bool(row["undoable"]) and not row["consumed"]
        await send("activity", _activity_frame(
            row["tool"], args, row["task_id"] or "history", undoable, row["tier"], row["lane"],
            row["undo_token"] if undoable else None,
        ))
