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
import logging
import uuid

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from brain import store

logger = logging.getLogger("brain.gate")

# Tool registry: name -> {fn, tier, destructive, redact, summary}.
# tier: int or callable(args)->int (arg-predicate rules; real file rules
# arrive with the Step 7 tools). destructive: bool or callable(args)->bool.
# redact: callable(args)->dict producing args_redacted (rule 3 -- applied
# before anything leaves the gate). summary: str template or callable(args).
TOOLS: dict[str, dict] = {}

# Pending Tier-3 approvals: approval_id -> {conversation_id, task_id, payload}
# plus the reverse map for interrupt's implicit-deny rule.
# ponytail: in-memory only -- restart rehydration of this map is Step 9 (the
# LangGraph checkpoint itself already survives restart; only the routing map
# needs rebuilding there).
_pending: dict[str, dict] = {}
_by_conversation: dict[str, str] = {}


def register(name: str, fn, *, tier=3, destructive=False, redact=None, summary=None) -> None:
    TOOLS[name] = {"fn": fn, "tier": tier, "destructive": destructive, "redact": redact, "summary": summary}


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
    redactor = entry["redact"] if entry is not None else None
    if redactor is None:
        return dict(args)
    try:
        return redactor(args)
    except Exception:  # noqa: BLE001 - a broken redactor must not leak raw args
        logger.exception("redactor failed for tool=%s; dropping args", tool)
        return {}


# ------------------------------------------------- pending approval registry


def register_pending(payload: dict, conversation_id: str) -> None:
    aid = payload["approval_id"]
    _pending[aid] = {"conversation_id": conversation_id, "task_id": payload["task_id"], "payload": payload}
    _by_conversation[conversation_id] = aid


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


def _record(tool: str, args_redacted: dict, tier: int, result: str, task_id: str | None) -> None:
    store.connect()
    # ponytail: undoable=False / inverse_json=None for every action until
    # Step 6 records real inverses at execution time.
    store.record_action(tool, args_redacted, tier, 1, result, False, None, task_id)


async def gated_execute(tool: str, args: dict, *, conversation_id: str, task_id: str | None, broadcast) -> dict:
    """The ONE path every tool call takes. Returns the graph-state update.

    Tier 3 calls interrupt(): LangGraph suspends here (GraphInterrupt bubbles
    up), run_turn emits the approval_request, and resume_turn re-enters this
    node from the top -- classify/redact re-run (pure), interrupt() then
    returns the approval_response decision instead of raising.
    """
    frame_task = task_id or f"tool-{conversation_id}"  # contract: task_id is required in these frames
    while True:
        tier = classify(tool, args)
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
        }
        decision = interrupt(payload)  # C2: never inside a try/except
        d = decision.get("decision")
        if d in ("deny", "cancelled"):
            await asyncio.to_thread(_record, tool, redact(tool, args), tier, "denied", task_id)
            note = "you denied it" if d == "deny" else "you stopped me"
            update = {
                "pending_tool_intent": None,
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
            args = decision.get("edited_args") or args
            continue
        break  # approve

    entry = TOOLS.get(tool)
    args_redacted = redact(tool, args)
    if entry is None:
        result = "error: unknown tool"
        content = f"I couldn't run {tool} — I don't have that tool."
    else:
        try:
            out = entry["fn"](args)
            if asyncio.iscoroutine(out):
                out = await out
            result = "ok"
            content = f"I ran {tool}."
        except GraphInterrupt:
            raise  # C2: a tool must never swallow the suspension signal
        except Exception as exc:  # noqa: BLE001 - rule 2: turn continues honestly
            logger.exception("tool %s failed", tool)
            result = f"error: {exc}"
            content = f"I tried to run {tool} but it failed: {exc}"
    await asyncio.to_thread(_record, tool, args_redacted, tier, result, task_id)
    if tier >= 2:
        await broadcast(
            "activity",
            {
                "text": summarize(tool, args),
                "narrate": False,
                "task_id": frame_task,
                "undoable": False,
                "tier": tier,
                "lane": 1,
            },
        )
    return {
        "pending_tool_intent": None,
        "pending_tool_result": {"tool": tool, "status": result},
        "messages": [{"role": "assistant", "content": content}],
    }
