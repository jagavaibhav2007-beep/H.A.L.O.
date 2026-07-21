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
import functools
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
# key -> current settings_state status. ponytail: only one settings key
# exists so far (openrouter_key); a real per-key registry is the upgrade
# path if Settings grows more entries that need mock round-tripping.
_settings: dict[str, str] = {"openrouter_key": "missing"}

FLOOD_COUNT = 2000

# Three tiny (64x36) solid-colour JPEGs — red/blue/green — cycled by
# `demo stream` so the sandbox tile visibly updates and the store's
# drop-stale-by-seq rule is exercised. Kept small to stay out of the way in
# source. # ponytail: real Lane-2/3 desktop capture is a Phase-3 concern; these
# scripted frames only prove the tile renders and throttles.
_STREAM_JPEGS = [
    "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",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_undo_token(task_id: str) -> str:
    token = str(uuid.uuid4())
    _undo_tokens[token] = task_id
    return token


def _swallow_closed(fn):
    """ConnectionClosed is a routine race (client dropped mid-send) every
    broadcast/send-driven handler must swallow the same way -- factor the
    identical try/except out of all nine of them."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ConnectionClosed:
            return

    return wrapper


async def _activity(
    broadcast: BroadcastFn,
    text: str,
    task_id: str,
    *,
    narrate: bool = True,
    undoable: bool = False,
    undo_token: str | None = None,
    tier: int = 1,
    lane: int = 1,
) -> None:
    """Shared shape for the ~10 `activity` frames scenarios/handlers emit."""
    payload = {
        "text": text, "narrate": narrate, "task_id": task_id, "undoable": undoable,
        "tier": tier, "lane": lane,
    }
    if undo_token is not None:
        payload["undo_token"] = undo_token
    await broadcast("activity", payload)


async def _stream_words(broadcast: BroadcastFn, conversation_id: str, text: str, delay: float) -> None:
    """Shared fixed-delay word-token streaming loop used by several scenarios."""
    for word in text.split(" "):
        await broadcast("token", {"text": word + " ", "conversation_id": conversation_id})
        await asyncio.sleep(delay)


async def _run_reorg_steps(task_id: str, broadcast: BroadcastFn, delay: float) -> str:
    """Shared "Reorganizing Downloads" step sequence used by `_scenario_task`
    and `_scenario_everything` -- identical apart from the per-step delay."""
    title = "Reorganizing Downloads"
    for step, label in [(1, "Scanning files"), (2, "Sorting by type"), (3, "Moving PDFs")]:
        await _emit_task(broadcast, {
            "task_id": task_id, "state": "running", "lane": 1, "title": title,
            "step": step, "steps_total": 4, "step_label": label,
        })
        await asyncio.sleep(delay)
    return title


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


# Live belief registry — seeded once, then mutated by handle_memory_edit and
# _scenario_memory so the memory panel's edit/delete/restore round-trips are
# real, and a reconnect snapshot re-pushes the CURRENT state (id-keyed upsert
# converges, D6). # ponytail: global because the Brain is single-instance.
_beliefs: dict[str, dict] = {}


def _reset_beliefs() -> None:
    _beliefs.clear()
    _beliefs.update({b["belief_id"]: b for b in _seed_beliefs()})


_reset_beliefs()


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


# Live skill registry — same pattern as _beliefs (Step 12): seeded once,
# mutated by handle_skill_op so trial/disable/restore/delete round-trip for
# real and a reconnect snapshot re-pushes current state (D6).
_skills: dict[str, dict] = {}


def _reset_skills() -> None:
    _skills.clear()
    _skills.update({s["skill_name"]: s for s in _seed_skills()})


_reset_skills()


def _seed_tasks() -> list[dict]:
    """Two live tasks -- one running, one paused (as if resumed post-crash)."""
    return [
        {"task_id": "task-seed-1", "state": "running", "lane": 1, "title": "Syncing calendar",
         "step": 2, "steps_total": 3, "step_label": "Fetching events"},
        {"task_id": "task-seed-2", "state": "paused", "lane": 1, "title": "Cleaning up screenshots folder",
         "reason": "resumed safely — Brain restarted"},
    ]


# Live task registry — same pattern as _beliefs/_skills. The UI reducer REPLACES
# a task on its task_id (it does not merge), so every task_state the UI receives
# must be a COMPLETE object. _emit_task merges each partial update onto the
# stored task and broadcasts the whole thing; a reconnect snapshot re-pushes the
# current state (D6). # ponytail: global because the Brain is single-instance.
_tasks: dict[str, dict] = {}


def _reset_tasks() -> None:
    _tasks.clear()
    _tasks.update({t["task_id"]: t for t in _seed_tasks()})


_reset_tasks()


async def _emit_task(broadcast: BroadcastFn, patch: dict) -> None:
    """Merge `patch` into the live task by task_id and broadcast the complete
    task. A key set to None is removed (e.g. clearing `reason` on resume)."""
    task_id = patch["task_id"]
    merged = dict(_tasks.get(task_id, {}))
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    _tasks[task_id] = merged
    await broadcast("task_state", merged)


def _seed_spend() -> dict:
    return {"session_usd": 0.42, "month_usd": 8.13}


@_swallow_closed
async def push_snapshot(send: SendFn) -> None:
    """D6: right after hello_ack, push seeded + live state to the connecting
    client only. Id-keyed frames -> idempotent on reconnect. `spend_update`
    is always pushed last; tests rely on that to know the snapshot is done."""
    for belief in _beliefs.values():
        await send("belief_state", belief)
    for skill in _skills.values():
        await send("skill_state", skill)
    for task in _tasks.values():
        await send("task_state", task)
    for payload in list(_pending_approval_payloads.values()):
        await send("approval_request", payload)
    # The key's status comes from the real keystore, not the in-memory dict:
    # a mock relaunch reporting "missing" for a key that IS stored is exactly
    # the lie that cost a user a key rotation (see handle_settings_update).
    from brain import secrets_store

    _settings["openrouter_key"] = await asyncio.to_thread(secrets_store.key_status)
    for key, status in _settings.items():
        await send("settings_state", {"key": key, "status": status})
    await send("spend_update", _seed_spend())


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
        "conversation_id": conversation_id,  # UI routes "Stop this task" by it
    }
    _pending_approval_payloads[approval_id] = payload
    await _emit_task(broadcast, {"task_id": task_id, "state": "waiting_approval", "lane": lane})
    await broadcast("approval_request", payload)
    try:
        return await fut
    finally:
        _pending_approvals.pop(approval_id, None)
        _pending_approval_payloads.pop(approval_id, None)
        _conversation_pending.pop(conversation_id, None)


@_swallow_closed
async def handle_approval_response(msg: dict, send: SendFn) -> None:
    """First approval_response for an approval_id wins; a second response to
    an already-resolved (or unknown) approval gets a recoverable error."""
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


@_swallow_closed
async def handle_interrupt(msg: dict, broadcast: BroadcastFn) -> None:
    """Interrupt-vs-pending-approval rule (11-ipc-contract.md): cancel the
    pending approval as an implicit deny, then pause the task."""
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
    await _emit_task(broadcast, {"task_id": task_id, "state": "paused", "reason": "interrupted"})


@_swallow_closed
async def handle_undo(msg: dict, broadcast: BroadcastFn) -> None:
    token = msg["undo_token"]
    task_id = _undo_tokens.pop(token, None)
    if task_id is None:
        return  # ponytail: unknown/already-used token is a silent no-op
    await _activity(broadcast, "Undone.", task_id)


@_swallow_closed
async def handle_task_op(msg: dict, broadcast: BroadcastFn) -> None:
    """Status-strip / orb task controls (pause/resume/stop). Merges the new
    state onto the live task via _emit_task so the confirming frame keeps the
    task's real title/lane/step (the UI reducer replaces on task_id) -- a real
    Brain owns the lifecycle.
    ponytail: an omitted task_id ('Pause all') is treated as the running seed."""
    op = msg.get("op")
    new_state = {"stop": "done", "pause": "paused", "resume": "running"}.get(op)
    if new_state is None:
        return
    task_id = msg.get("task_id") or "task-seed-1"
    patch = {"task_id": task_id, "state": new_state}
    if op == "stop":
        patch["reason"] = "you stopped it"
    elif op == "pause":
        patch["reason"] = "you paused it"
    else:  # resume clears any prior pause/stop reason
        patch["reason"] = None
    await _emit_task(broadcast, patch)


@_swallow_closed
async def handle_memory_edit(msg: dict, broadcast: BroadcastFn) -> None:
    """Memory panel round-trips (Step 12). edit -> supersede the old belief and
    emit a new *user-stated* one (the provenance rule made visible); delete ->
    archive; restore -> reactivate. The store never mutates a belief locally
    (rule 3) — these confirming belief_state frames are the only source of truth."""
    belief_id = msg.get("belief_id")
    op = msg.get("op")
    cur = _beliefs.get(belief_id)
    if cur is None:
        return  # ponytail: unknown belief -> silent no-op
    if op == "edit":
        new_id = str(uuid.uuid4())
        superseded = {**cur, "status": "superseded", "superseded_by": new_id,
                      "salience": min(cur.get("salience", 0.5), 0.3)}
        new_belief = {
            "belief_id": new_id, "text": msg.get("text") or cur["text"], "kind": cur["kind"],
            "provenance": "user", "salience": max(cur.get("salience", 0.5), 0.8), "status": "active",
        }
        _beliefs[belief_id] = superseded
        _beliefs[new_id] = new_belief
        await broadcast("belief_state", superseded)
        await broadcast("belief_state", new_belief)
    elif op == "delete":
        archived = {**cur, "status": "archived"}
        _beliefs[belief_id] = archived
        await broadcast("belief_state", archived)
    elif op == "restore":
        restored = {**cur, "status": "active"}
        _beliefs[belief_id] = restored
        await broadcast("belief_state", restored)


@_swallow_closed
async def handle_skill_op(msg: dict, broadcast: BroadcastFn) -> None:
    """Skills panel round-trips (Step 13). trial -> stream a scripted dry run
    into the results drawer as narrated activity, no state change; disable ->
    paused; restore -> active (clears any retire reason); delete -> retired
    (soft, like memory's delete -- the schema has no 'deleted' status, so
    retired-with-a-reason IS the soft-delete state, same as an auto-retire).
    The store never mutates a skill locally (rule 3) -- confirming skill_state
    frames are the only source of truth."""
    skill_name = msg.get("skill_name")
    op = msg.get("op")
    cur = _skills.get(skill_name)
    if cur is None:
        return  # ponytail: unknown skill -> silent no-op
    if op == "trial":
        trial_task = f"skill-trial-{skill_name}"
        for line in (f"Trying {skill_name} on a sample input…", "Looks right.", "Trial run complete."):
            await _activity(broadcast, line, trial_task, narrate=False)
            await asyncio.sleep(0.15)
    elif op == "disable":
        paused = {**cur, "status": "paused"}
        _skills[skill_name] = paused
        await broadcast("skill_state", paused)
    elif op == "restore":
        restored = {k: v for k, v in cur.items() if k != "reason"}
        restored["status"] = "active"
        _skills[skill_name] = restored
        await broadcast("skill_state", restored)
    elif op == "delete":
        retired = {**cur, "status": "retired", "reason": "you removed it"}
        _skills[skill_name] = retired
        await broadcast("skill_state", retired)


@_swallow_closed
async def handle_mic(msg: dict, broadcast: BroadcastFn) -> None:
    """Mute round-trip (Step 14). Mute -> muted (visually loud everywhere);
    unmute -> idle. There is no ambiguous mic state by design."""
    await broadcast("voice_state", {"state": "muted" if msg.get("op") == "mute" else "idle"})


@_swallow_closed
async def handle_settings_update(msg: dict, send: SendFn) -> None:
    """Mock round-trip for the Settings rows, with ONE deliberate exception:
    the API key is stored for real.

    The mock exists to script conversations, not to fake the user's
    credentials. It used to keep key status in the in-memory `_settings` dict
    and never write the value anywhere -- so pasting a real key here reported
    "connected" and then silently lost it on the next launch, which cost a
    real user a real key rotation (see mem/Bugs.md). A credential the user
    deliberately typed is not mock data, so openrouter_key goes to the same
    keystore the real Brain reads.
    """
    key = msg["key"]
    value = msg.get("value")
    if key == "openrouter_key":
        from brain import secrets_store

        if value:
            await asyncio.to_thread(secrets_store.set_key, value)
        else:
            await asyncio.to_thread(secrets_store.delete_key)
        # ponytail: no validate_key() call here -- the mock never touches the
        # network, so an unverified-but-stored key is the honest answer; the
        # real Brain verifies on first use.
        status = await asyncio.to_thread(secrets_store.key_status)
        _settings[key] = "unverified" if status == "set" else status
    else:
        _settings[key] = "set" if value else "missing"
    await send("settings_state", {"key": key, "status": _settings[key]})


@_swallow_closed
async def handle_lane_pin(msg: dict, broadcast: BroadcastFn) -> None:
    """Re-pin a task's lane. Merges the new lane onto the live task via
    _emit_task so the confirming frame keeps the task's real state/title/step
    (rule 3 disable-until-confirmed)."""
    task_id = msg.get("task_id")
    lane = msg.get("lane")
    if task_id is None or lane not in (1, 2, 3):
        return
    await _emit_task(broadcast, {"task_id": task_id, "lane": lane})


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
        await _activity(broadcast, "Skipped — you denied the request.", task_id, tier=3, lane=lane)
        await _emit_task(broadcast, {"task_id": task_id, "state": "paused", "lane": lane, "reason": "denied by user"})
        return

    text = "Deleted 12 old export files." if destructive else "Sent the weekly report email."
    if decision == "edit":
        text = "Applied your edits and " + ("deleted the selected files." if destructive else "sent the email.")
    undo_token = _new_undo_token(task_id)
    await _activity(broadcast, text, task_id, undoable=True, undo_token=undo_token, tier=3, lane=lane)
    await _emit_task(broadcast, {"task_id": task_id, "state": "done", "lane": lane})
    await broadcast("done", {"conversation_id": conversation_id, "task_id": task_id})


async def _scenario_task(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    title = await _run_reorg_steps(task_id, broadcast, 0.2)

    result = await _await_approval(
        task_id, conversation_id, broadcast,
        tool="delete_files", args={"count": 6},
        summary="Halo wants to delete 6 duplicate files it found.",
        destructive=False, lane=1,
    )
    if result["decision"] == "cancelled":
        return
    if result["decision"] == "deny":
        await _emit_task(broadcast, {
            "task_id": task_id, "state": "paused", "lane": 1, "title": title, "reason": "you denied a step",
        })
        return

    await _emit_task(broadcast, {
        "task_id": task_id, "state": "running", "lane": 1, "title": title,
        "step": 4, "steps_total": 4, "step_label": "Cleaning up",
    })
    await asyncio.sleep(0.2)
    await _emit_task(broadcast, {
        "task_id": task_id, "state": "done", "lane": 1, "title": title,
        "step": 4, "steps_total": 4, "step_label": "Done",
    })
    await broadcast("done", {"conversation_id": conversation_id, "task_id": task_id})


async def _scenario_memory(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    """Live auto-correct: supersedes the snapshot's active npm belief with a
    user-stated pnpm one -- a real, visible before/after (not already-settled
    in the snapshot)."""
    superseded = {
        "belief_id": "belief-pkg-manager", "text": "Uses npm for package management.",
        "kind": "preference", "provenance": "inferred", "salience": 0.3,
        "status": "superseded", "superseded_by": "belief-pkg-manager-new",
    }
    new_belief = {
        "belief_id": "belief-pkg-manager-new", "text": "Uses pnpm for package management.",
        "kind": "preference", "provenance": "user", "salience": 0.8, "status": "active",
    }
    # Keep the registry in step so a reconnect snapshot shows the corrected state.
    _beliefs["belief-pkg-manager"] = superseded
    _beliefs["belief-pkg-manager-new"] = new_belief
    await broadcast("belief_state", superseded)
    await broadcast("belief_state", new_belief)
    await _activity(broadcast, "Updated what I remember — you switched to pnpm.", task_id)
    await broadcast("done", {"conversation_id": conversation_id})


async def _scenario_skill(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    skill_name = "weekly-report-formatter"
    born = {
        "skill_name": skill_name, "origin": "auto", "kind": "skill",
        "uses": 1, "success_rate": 1.0, "status": "active", "born_at": _now_iso(),
    }
    _skills[skill_name] = born  # keep the registry in step so a reconnect snapshot shows the birth
    await broadcast("skill_state", born)
    undo_token = _new_undo_token(task_id)
    await _activity(broadcast, f"Learned a new skill: {skill_name}.", task_id, undoable=True, undo_token=undo_token)
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
    await _stream_words(broadcast, conversation_id, "You have two meetings today:", 0.03)
    # barge-in beat: user cuts in mid-reply -> orb drops to listening instantly
    # (the speaking pulse stops), then resumes.
    await broadcast("voice_state", {"state": "listening"})
    await asyncio.sleep(0.2)
    await broadcast("voice_state", {"state": "speaking"})
    await _stream_words(broadcast, conversation_id, "standup at 10am and design review at 2pm.", 0.03)
    await broadcast("done", {"conversation_id": conversation_id})
    await broadcast("voice_state", {"state": "idle"})


async def _scenario_tts_down(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    """Degraded: text-to-speech is out, so Halo replies as text and says so."""
    await _activity(broadcast, "My voice output is down — I'll reply as text until it's back.", task_id)
    await _stream_words(broadcast, conversation_id, "Sure — you have three meetings tomorrow, first one at 9.", 0.03)
    await broadcast("done", {"conversation_id": conversation_id})


async def _scenario_stt_down(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    """Degraded: speech-to-text is out, so listening is off and typing works."""
    await _activity(broadcast, "I can't hear right now — my mic input is unavailable. Type and I'll answer.", task_id)
    await broadcast("done", {"conversation_id": conversation_id})


async def _scenario_error(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    await broadcast("token", {"text": "Trying to fetch that for you...", "conversation_id": conversation_id})
    await asyncio.sleep(0.15)
    await broadcast("error", {
        "code": "tool_failed", "message": "The calendar service didn't respond in time.",
        "recoverable": True, "conversation_id": conversation_id,
    })


async def _scenario_flood(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    for i in range(FLOOD_COUNT):
        await _activity(broadcast, f"Background scan step {i + 1}.", task_id, narrate=False)
    await broadcast("done", {"conversation_id": conversation_id})


async def _scenario_stream(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    """Sandbox (lane 3) task with a scripted desktop stream — cycles the three
    tiny JPEGs so the tile visibly updates and the store's drop-stale-by-seq
    rule gets exercised. Leaves the task running so the tile stays inspectable."""
    await _emit_task(broadcast, {
        "task_id": task_id, "state": "running", "lane": 3, "title": "Booking a table (sandbox)",
        "step": 1, "steps_total": 1, "step_label": "Driving the browser",
    })
    for seq in range(6):
        await broadcast("stream_frame", {
            "task_id": task_id, "jpeg_b64": _STREAM_JPEGS[seq % len(_STREAM_JPEGS)], "seq": seq,
        })
        await asyncio.sleep(0.25)
    await broadcast("done", {"conversation_id": conversation_id, "task_id": task_id})


async def _scenario_everything(conversation_id: str, task_id: str, broadcast: BroadcastFn) -> None:
    await _stream_words(broadcast, conversation_id, "Let's walk through everything I can do.", 0.02)
    await broadcast("done", {"conversation_id": conversation_id})

    title = await _run_reorg_steps(task_id, broadcast, 0.15)

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
        await _activity(broadcast, "Sent the weekly report email.", task_id, undoable=True, undo_token=undo_token, tier=3)
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
    if destructive["decision"] == "deny":
        await _activity(broadcast, "Skipped — you denied the file deletion.", task_id, tier=3, lane=3)
        await _emit_task(broadcast, {"task_id": task_id, "state": "paused", "reason": "you denied a step"})
        return

    await _emit_task(broadcast, {
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
    "demo tts-down": _scenario_tts_down,
    "demo stt-down": _scenario_stt_down,
    "demo error": _scenario_error,
    "demo flood": _scenario_flood,
    "demo stream": _scenario_stream,
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
