"""Mock scenario engine for `python -m brain --mock` (Phase 1 Step 2, D1/D2).

Scenarios are data played back through the caller-supplied `send`/`broadcast`
callables, which in server.py wrap `_send`/`_broadcast` -- so every emitted
frame goes through `_envelope()` and is contract-validated at send time,
exactly like every other frame the Brain emits. This module never touches a
socket or the contract validator directly.

# ponytail: module-level mutable state for pending approvals/undo tokens --
# fine because the Brain already enforces single-instance (single_instance_lock
# in server.py), so there is only ever one mock engine alive. A multi-tenant
# mock would need this state scoped per connection-set instead of global.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from websockets.exceptions import ConnectionClosed

SendFn = Callable[[str, dict], Awaitable[None]]
BroadcastFn = Callable[[str, dict], Awaitable[None]]

# approval_id -> Future resolved with the approval_response payload (or the
# {"decision": "cancelled"} sentinel set by handle_interrupt).
_pending_approvals: dict[str, asyncio.Future] = {}
# approval_id -> the approval_request payload last broadcast, kept so a
# reconnecting client's snapshot can re-push still-pending approvals (D6).
_pending_approval_payloads: dict[str, dict] = {}
# conversation_id -> {"approval_id", "task_id"} for the *one* approval that
# conversation is currently waiting on -- enough to resolve interrupt's
# cancel-pending-approval rule. # ponytail: last-writer-wins if a
# conversation somehow has two concurrent awaits; out of scope for the demo
# scenarios, which are always sequential per conversation.
_conversation_pending: dict[str, dict] = {}
# undo_token -> task_id, so a later inbound `undo` can emit the right
# reversal activity.
_undo_tokens: dict[str, str] = {}

FLOOD_COUNT = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_undo_token(task_id: str) -> str:
    token = str(uuid.uuid4())
    _undo_tokens[token] = task_id
    return token


# ---- Snapshot-on-connect (D6) ----


def _seed_beliefs() -> list[dict]:
    """All five kinds, both provenances, one superseded chain, one archived."""
    return [
        {"belief_id": "belief-editor", "text": "Prefers VS Code over other editors.",
         "kind": "preference", "provenance": "user", "salience": 0.9, "status": "active"},
        {"belief_id": "belief-current-project", "text": "Currently working on the H.A.L.O. desktop app.",
         "kind": "project", "provenance": "inferred", "salience": 0.85, "status": "active"},
        {"belief_id": "belief-deploy-workflow", "text": "Deploys via GitHub Actions on merge to main.",
         "kind": "workflow", "provenance": "user", "salience": 0.7, "status": "active"},
        {"belief_id": "belief-db-choice", "text": "Chose SQLite for local-first storage to avoid a server dependency.",
         "kind": "decision", "provenance": "user", "salience": 0.6, "status": "active"},
        {"belief_id": "belief-retry-lesson", "text": "Retrying network calls without backoff caused a rate-limit ban once.",
         "kind": "lesson", "provenance": "inferred", "salience": 0.5, "status": "active"},
        # This chain is snapshot-only scaffolding (the "one superseded chain"
        # the panel needs to render), deliberately separate from the belief
        # `demo memory` supersedes live below -- otherwise the snapshot would
        # already show the post-correction state and the demo would have no
        # visible before/after.
        {"belief_id": "belief-notify-old", "text": "Wants desktop notifications for every task.",
         "kind": "preference", "provenance": "inferred", "salience": 0.2, "status": "superseded",
         "superseded_by": "belief-notify-new"},
        {"belief_id": "belief-notify-new", "text": "Wants desktop notifications only for Tier-3 approvals.",
         "kind": "preference", "provenance": "user", "salience": 0.75, "status": "active"},
        {"belief_id": "belief-pkg-manager", "text": "Uses npm for package management.",
         "kind": "preference", "provenance": "inferred", "salience": 0.6, "status": "active"},
        {"belief_id": "belief-old-project", "text": "Was working on a CLI tool called 'foo'.",
         "kind": "project", "provenance": "user", "salience": 0.1, "status": "archived"},
    ]


def _seed_skills() -> list[dict]:
    """Auto + user, skill + playbook, one <60% success, one retired."""
    now = _now_iso()
    return [
        {"skill_name": "changelog-summarizer", "origin": "auto", "kind": "skill",
         "uses": 14, "success_rate": 0.93, "status": "active", "born_at": now},
        {"skill_name": "invoice-formatter", "origin": "user", "kind": "skill",
         "uses": 6, "success_rate": 1.0, "status": "active", "born_at": now},
        {"skill_name": "weekly-standup-playbook", "origin": "user", "kind": "playbook",
         "uses": 9, "success_rate": 0.88, "status": "active", "born_at": now},
        {"skill_name": "flaky-scraper", "origin": "auto", "kind": "skill",
         "uses": 5, "success_rate": 0.4, "status": "active", "born_at": now},
        {"skill_name": "old-report-builder", "origin": "auto", "kind": "skill",
         "uses": 20, "success_rate": 0.55, "status": "retired", "born_at": now,
         "reason": "success rate fell below 60%"},
    ]


def _seed_tasks() -> list[dict]:
    """Two live tasks -- one running, one paused (as if resumed post-crash)."""
    return [
        {"task_id": "task-seed-1", "state": "running", "lane": 1, "title": "Syncing calendar",
         "step": 2, "steps_total": 3, "step_label": "Fetching events"},
        {"task_id": "task-seed-2", "state": "paused", "lane": 1, "title": "Cleaning up screenshots folder",
         "reason": "resumed safely — Brain restarted"},
    ]


def _seed_spend() -> dict:
    return {"session_usd": 0.42, "month_usd": 8.13}


async def push_snapshot(send: SendFn) -> None:
    """D6: right after hello_ack, push seeded + live state to the connecting
    client only. Id-keyed frames -> idempotent on reconnect. `spend_update`
    is always pushed last; tests rely on that to know the snapshot is done."""
    try:
        for belief in _seed_beliefs():
            await send("belief_state", belief)
        for skill in _seed_skills():
            await send("skill_state", skill)
        for task in _seed_tasks():
            await send("task_state", task)
        for payload in list(_pending_approval_payloads.values()):
            await send("approval_request", payload)
        await send("spend_update", _seed_spend())
    except ConnectionClosed:
        return


# ---- Reactive approval await-point ----


async def _await_approval(
    task_id: str,
    conversation_id: str,
    broadcast: BroadcastFn,
    *,
    tool: str,
    args: dict,
    summary: str,
    destructive: bool,
    lane: int,
) -> dict:
    approval_id = str(uuid.uuid4())
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_approvals[approval_id] = fut
    _conversation_pending[conversation_id] = {"approval_id": approval_id, "task_id": task_id}
    payload = {
        "approval_id": approval_id,
        "tool": tool,
        "args_redacted": args,
        "tier": 3,
        "task_id": task_id,
        "summary": summary,
        "destructive": destructive,
    }
    _pending_approval_payloads[approval_id] = payload
    await broadcast("task_state", {"task_id": task_id, "state": "waiting_approval", "lane": lane})
    await broadcast("approval_request", payload)
    try:
        return await fut
    finally:
        _pending_approvals.pop(approval_id, None)
        _pending_approval_payloads.pop(approval_id, None)
        _conversation_pending.pop(conversation_id, None)


async def handle_approval_response(msg: dict, send: SendFn) -> None:
    """First approval_response for an approval_id wins; a second response to
    an already-resolved (or unknown) approval gets a recoverable error."""
    try:
        approval_id = msg["reply_to"]
        fut = _pending_approvals.get(approval_id)
        if fut is None or fut.done():
            await send("error", {
                "code": "approval_already_handled",
                "message": "This approval was already handled.",
                "recoverable": True,
            })
            return
        fut.set_result(msg)
    except ConnectionClosed:
        return


async def handle_interrupt(msg: dict, broadcast: BroadcastFn) -> None:
    """Interrupt-vs-pending-approval rule (11-ipc-contract.md): cancel the
    pending approval as an implicit deny, then pause the task."""
    try:
        conversation_id = msg["conversation_id"]
        pending = _conversation_pending.pop(conversation_id, None)
        if pending is None:
            return
        approval_id = pending["approval_id"]
        task_id = pending["task_id"]
        _pending_approval_payloads.pop(approval_id, None)
        fut = _pending_approvals.pop(approval_id, None)
        if fut is not None and not fut.done():
            fut.set_result({"decision": "cancelled"})
        await broadcast("task_state", {"task_id": task_id, "state": "paused", "lane": 3, "reason": "interrupted"})
    except ConnectionClosed:
        return


async def handle_undo(msg: dict, broadcast: BroadcastFn) -> None:
    try:
        token = msg["undo_token"]
        task_id = _undo_tokens.pop(token, None)
        if task_id is None:
            return  # ponytail: unknown/already-used token is a silent no-op
        await broadcast("activity", {
            "text": "Undone.", "narrate": True, "task_id": task_id, "undoable": False,
            "tier": 1, "lane": 1,
        })
    except ConnectionClosed:
        return


async def handle_task_op(msg: dict, broadcast: BroadcastFn) -> None:
    """Status-strip / orb task controls (pause/resume/stop). The mock keeps no
    task registry beyond the seeds, so it just broadcasts the resulting
    task_state for the named id -- enough to satisfy the UI's rule-3
    disable-until-confirmed contract (a real Brain owns the lifecycle).
    ponytail: an omitted task_id ('Pause all') is treated as the running seed."""
    try:
        op = msg.get("op")
        new_state = {"stop": "done", "pause": "paused", "resume": "running"}.get(op)
        if new_state is None:
            return
        task_id = msg.get("task_id") or "task-seed-1"
        payload = {"task_id": task_id, "state": new_state, "lane": 1, "title": "Syncing calendar"}
        if op == "stop":
            payload["reason"] = "you stopped it"
        elif op == "pause":
            payload["reason"] = "you paused it"
        await broadcast("task_state", payload)
    except ConnectionClosed:
        return


# ---- Scenarios ----


GENERIC_REPLY = (
    "Got it — I'm a scripted mock right now, so here's a canned reply. "
    "Try 'demo everything' to see a full walkthrough."
)


async def _scenario_generic(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    for word in GENERIC_REPLY.split(" "):
        await broadcast("token", {"text": word + " ", "conversation_id": conversation_id})
        await asyncio.sleep(random.uniform(0.02, 0.04))
    await broadcast("done", {"conversation_id": conversation_id})


async def _scenario_approval(conversation_id: str, task_id: str, broadcast: BroadcastFn, destructive: bool) -> None:
    if destructive:
        tool, args, summary = "delete_files", {"count": 12}, \
            "Halo wants to permanently delete 12 files in Downloads/old-exports."
    else:
        tool, args, summary = "send_email", {"to": "team@example.com"}, \
            "Halo wants to send the weekly report email to the team."
    lane = 3 if destructive else 1

    result = await _await_approval(
        task_id, conversation_id, broadcast,
        tool=tool, args=args, summary=summary, destructive=destructive, lane=lane,
    )
    decision = result["decision"]
    if decision == "cancelled":
        return  # task_state:paused already emitted by handle_interrupt
    if decision == "deny":
        await broadcast("activity", {
            "text": "Skipped — you denied the request.", "narrate": True,
            "task_id": task_id, "undoable": False, "tier": 3, "lane": lane,
        })
        await broadcast("task_state", {"task_id": task_id, "state": "paused", "lane": lane, "reason": "denied by user"})
        return

    text = "Deleted 12 old export files." if destructive else "Sent the weekly report email."
    if decision == "edit":
        text = "Applied your edits and " + ("deleted the selected files." if destructive else "sent the email.")
    undo_token = _new_undo_token(task_id)
    await broadcast("activity", {
        "text": text, "narrate": True, "task_id": task_id, "undoable": True,
        "undo_token": undo_token, "tier": 3, "lane": lane,
    })
    await broadcast("task_state", {"task_id": task_id, "state": "done", "lane": lane})
    await broadcast("done", {"conversation_id": conversation_id, "task_id": task_id})


async def _scenario_task(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    title = "Reorganizing Downloads"
    for step, label in [(1, "Scanning files"), (2, "Sorting by type"), (3, "Moving PDFs")]:
        await broadcast("task_state", {
            "task_id": task_id, "state": "running", "lane": 1, "title": title,
            "step": step, "steps_total": 4, "step_label": label,
        })
        await asyncio.sleep(0.2)

    result = await _await_approval(
        task_id, conversation_id, broadcast,
        tool="delete_files", args={"count": 6},
        summary="Halo wants to delete 6 duplicate files it found.",
        destructive=False, lane=1,
    )
    if result["decision"] == "cancelled":
        return
    if result["decision"] == "deny":
        await broadcast("task_state", {
            "task_id": task_id, "state": "paused", "lane": 1, "title": title, "reason": "you denied a step",
        })
        return

    await broadcast("task_state", {
        "task_id": task_id, "state": "running", "lane": 1, "title": title,
        "step": 4, "steps_total": 4, "step_label": "Cleaning up",
    })
    await asyncio.sleep(0.2)
    await broadcast("task_state", {
        "task_id": task_id, "state": "done", "lane": 1, "title": title,
        "step": 4, "steps_total": 4, "step_label": "Done",
    })
    await broadcast("done", {"conversation_id": conversation_id, "task_id": task_id})


async def _scenario_memory(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    """Live auto-correct: supersedes the snapshot's active npm belief with a
    user-stated pnpm one -- a real, visible before/after (not already-settled
    in the snapshot)."""
    await broadcast("belief_state", {
        "belief_id": "belief-pkg-manager", "text": "Uses npm for package management.",
        "kind": "preference", "provenance": "inferred", "salience": 0.3,
        "status": "superseded", "superseded_by": "belief-pkg-manager-new",
    })
    await broadcast("belief_state", {
        "belief_id": "belief-pkg-manager-new", "text": "Uses pnpm for package management.",
        "kind": "preference", "provenance": "user", "salience": 0.8, "status": "active",
    })
    await broadcast("activity", {
        "text": "Updated what I remember — you switched to pnpm.", "narrate": True,
        "task_id": task_id, "undoable": False, "tier": 1, "lane": 1,
    })
    await broadcast("done", {"conversation_id": conversation_id})


async def _scenario_skill(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    skill_name = "weekly-report-formatter"
    await broadcast("skill_state", {
        "skill_name": skill_name, "origin": "auto", "kind": "skill",
        "uses": 1, "success_rate": 1.0, "status": "active", "born_at": _now_iso(),
    })
    undo_token = _new_undo_token(task_id)
    await broadcast("activity", {
        "text": f"Learned a new skill: {skill_name}.", "narrate": True,
        "task_id": task_id, "undoable": True, "undo_token": undo_token, "tier": 1, "lane": 1,
    })
    await broadcast("done", {"conversation_id": conversation_id})


async def _scenario_voice(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    await broadcast("voice_state", {"state": "wake"})
    await asyncio.sleep(0.15)
    await broadcast("voice_state", {"state": "listening"})
    partials = ["what's", "what's on", "what's on my", "what's on my calendar today"]
    for partial in partials:
        await broadcast("transcript", {"text": partial, "final": False, "conversation_id": conversation_id})
        await asyncio.sleep(0.12)
    await broadcast("transcript", {"text": "what's on my calendar today", "final": True, "conversation_id": conversation_id})
    await broadcast("voice_state", {"state": "thinking"})
    await asyncio.sleep(0.2)
    await broadcast("voice_state", {"state": "speaking"})
    for word in "You have two meetings today: standup at 10am and design review at 2pm.".split(" "):
        await broadcast("token", {"text": word + " ", "conversation_id": conversation_id})
        await asyncio.sleep(0.03)
    await broadcast("done", {"conversation_id": conversation_id})
    await broadcast("voice_state", {"state": "idle"})


async def _scenario_error(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    await broadcast("token", {"text": "Trying to fetch that for you...", "conversation_id": conversation_id})
    await asyncio.sleep(0.15)
    await broadcast("error", {
        "code": "tool_failed", "message": "The calendar service didn't respond in time.",
        "recoverable": True, "conversation_id": conversation_id,
    })


async def _scenario_flood(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    for i in range(FLOOD_COUNT):
        await broadcast("activity", {
            "text": f"Background scan step {i + 1}.", "narrate": False,
            "task_id": task_id, "undoable": False, "tier": 1, "lane": 1,
        })
    await broadcast("done", {"conversation_id": conversation_id})


async def _scenario_everything(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    for word in "Let's walk through everything I can do.".split(" "):
        await broadcast("token", {"text": word + " ", "conversation_id": conversation_id})
        await asyncio.sleep(0.02)
    await broadcast("done", {"conversation_id": conversation_id})

    title = "Reorganizing Downloads"
    for step, label in [(1, "Scanning files"), (2, "Sorting by type"), (3, "Moving PDFs")]:
        await broadcast("task_state", {
            "task_id": task_id, "state": "running", "lane": 1, "title": title,
            "step": step, "steps_total": 4, "step_label": label,
        })
        await asyncio.sleep(0.15)

    regular = await _await_approval(
        task_id, conversation_id, broadcast,
        tool="send_email", args={"to": "team@example.com"},
        summary="Halo wants to send the weekly report email to the team.",
        destructive=False, lane=1,
    )
    if regular["decision"] == "cancelled":
        return
    if regular["decision"] != "deny":
        undo_token = _new_undo_token(task_id)
        await broadcast("activity", {
            "text": "Sent the weekly report email.", "narrate": True, "task_id": task_id,
            "undoable": True, "undo_token": undo_token, "tier": 3, "lane": 1,
        })
        await asyncio.sleep(0.2)
        await handle_undo({"undo_token": undo_token}, broadcast)

    destructive = await _await_approval(
        task_id, conversation_id, broadcast,
        tool="delete_files", args={"count": 12},
        summary="Halo wants to permanently delete 12 files in Downloads/old-exports.",
        destructive=True, lane=3,
    )
    if destructive["decision"] == "cancelled":
        return

    await broadcast("task_state", {
        "task_id": task_id, "state": "done", "lane": 1, "title": title,
        "step": 4, "steps_total": 4, "step_label": "Done",
    })

    await _scenario_memory(conversation_id, task_id, broadcast)
    await _scenario_skill(conversation_id, task_id, broadcast)
    await _scenario_voice(conversation_id, task_id, broadcast)


_TRIGGERS: dict[str, Callable[[str, str, BroadcastFn], Awaitable[None]]] = {
    "demo approval": lambda c, t, b: _scenario_approval(c, t, b, destructive=False),
    "demo destructive": lambda c, t, b: _scenario_approval(c, t, b, destructive=True),
    "demo task": _scenario_task,
    "demo memory": _scenario_memory,
    "demo skill": _scenario_skill,
    "demo voice": _scenario_voice,
    "demo error": _scenario_error,
    "demo flood": _scenario_flood,
    "demo everything": _scenario_everything,
}


async def handle_user_msg(msg: dict, send: SendFn, broadcast: BroadcastFn) -> None:
    """Dispatch on the D2 keyword triggers; anything else gets a generic
    streamed reply. `send` is accepted for signature symmetry with the other
    handlers even though no scenario currently unicasts."""
    del send
    conversation_id = msg["conversation_id"]
    task_id = str(uuid.uuid4())
    scenario = _TRIGGERS.get(msg["text"].strip().lower(), _scenario_generic)
    await scenario(conversation_id, task_id, broadcast)
