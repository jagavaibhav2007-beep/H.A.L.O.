"""Phase 1 end-to-end protocol check for the scripted mock Brain.

This plain asyncio + assert script drives a real authenticated WebSocket
client against the same ``start(mock=True)`` path used by
``python -m brain --mock``. Visual rendering remains covered by VERIFY.md.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))

import websockets  # noqa: E402

from brain.ipc.contract import parse_ipc_message  # noqa: E402
from brain.server import start  # noqa: E402

SNAPSHOT_TYPES = {
    "belief_state",
    "skill_state",
    "task_state",
    "approval_request",
    "settings_state",
    "capabilities_state",
    "spend_update",
    "snapshot_complete",
}


def _frame(msg_type: str, **payload: object) -> dict:
    return {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


async def _recv(ws, timeout: float = 3.0) -> dict:
    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    parse_ipc_message(frame)
    return frame


async def _connect_ui(port: int, token: str):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(json.dumps(_frame("hello", token=token, role="ui")))
    ack = await _recv(ws)
    assert ack["type"] == "hello_ack", ack
    return ws


async def _snapshot(ws) -> list[dict]:
    frames: list[dict] = []
    while True:
        frame = await _recv(ws)
        assert frame["type"] in SNAPSHOT_TYPES, frame
        frames.append(frame)
        if frame["type"] == "snapshot_complete":
            return frames


def _payloads(frames: list[dict]) -> list[dict]:
    return [{k: v for k, v in frame.items() if k not in {"id", "ts"}} for frame in frames]


async def check_snapshot_reconnect_is_idempotent(port: int, token: str) -> None:
    first = await _connect_ui(port, token)
    try:
        first_snapshot = await _snapshot(first)
    finally:
        await first.close()
    second = await _connect_ui(port, token)
    try:
        second_snapshot = await _snapshot(second)
    finally:
        await second.close()

    assert _payloads(first_snapshot) == _payloads(second_snapshot)
    domain_keys = [
        (
            frame["type"],
            frame.get("belief_id")
            or frame.get("skill_name")
            or frame.get("task_id")
            or frame.get("approval_id")
            or frame.get("key")
            or frame["type"],
        )
        for frame in first_snapshot
        if frame["type"] not in {"spend_update", "snapshot_complete"}
    ]
    assert len(domain_keys) == len(set(domain_keys)), "snapshot contains duplicate identities"
    print("PASS -- reconnect snapshot is contract-valid, stable, and identity-idempotent")


async def _approval_branch(port: int, token: str, decision: str) -> None:
    ws = await _connect_ui(port, token)
    try:
        await _snapshot(ws)
        await ws.send(json.dumps(_frame(
            "user_msg", text="demo approval",
            conversation_id=f"phase1-approval-{decision}", source="ui"
        )))
        while (request := await _recv(ws))["type"] != "approval_request":
            pass
        response = {"reply_to": request["approval_id"], "decision": decision}
        if decision == "edit":
            response["edited_args"] = {"to": "reviewed@example.com"}
        await ws.send(json.dumps(_frame("approval_response", **response)))
        while (result := await _recv(ws))["type"] != "activity":
            pass
        text = result["text"].lower()
        if decision == "approve":
            assert result["undoable"] is True and "sent" in text, result
        elif decision == "deny":
            assert result["undoable"] is False and "denied" in text, result
        else:
            assert result["undoable"] is True and "edits" in text, result
    finally:
        await ws.close()


async def check_approval_branches(port: int, token: str) -> None:
    for decision in ("approve", "deny", "edit"):
        await _approval_branch(port, token, decision)
    print("PASS -- approval round-trip covers approve, deny, and edited branches")


async def check_undo_reverses(port: int, token: str) -> None:
    ws = await _connect_ui(port, token)
    try:
        await _snapshot(ws)
        await ws.send(json.dumps(_frame(
            "user_msg", text="demo skill", conversation_id="phase1-undo", source="ui"
        )))
        undoable = None
        while True:
            frame = await _recv(ws)
            if frame["type"] == "activity" and frame["undoable"]:
                undoable = frame
            if frame["type"] == "done":
                break
        assert undoable and undoable.get("undo_token"), undoable
        await ws.send(json.dumps(_frame("undo", undo_token=undoable["undo_token"])))
        reversal = await _recv(ws)
        assert reversal["type"] == "activity", reversal
        assert reversal["undoable"] is False and reversal["task_id"] == undoable["task_id"], reversal
        assert "undone" in reversal["text"].lower(), reversal
    finally:
        await ws.close()
    print("PASS -- undo token emits a non-undoable reversal for the same task")


async def check_everything_walkthrough(port: int, token: str) -> None:
    ws = await _connect_ui(port, token)
    seen: list[dict] = []
    approvals = 0
    try:
        await _snapshot(ws)
        await ws.send(json.dumps(_frame(
            "user_msg", text="demo everything", conversation_id="phase1-everything", source="ui"
        )))
        while True:
            frame = await _recv(ws, timeout=5.0)
            seen.append(frame)
            if frame["type"] == "approval_request":
                approvals += 1
                await ws.send(json.dumps(_frame(
                    "approval_response", reply_to=frame["approval_id"], decision="approve"
                )))
            if frame["type"] == "voice_state" and frame["state"] == "idle":
                break
    finally:
        await ws.close()

    types = [frame["type"] for frame in seen]
    assert types[0] == "token" and "done" in types, types[:10]
    assert any(f["type"] == "task_state" and f["state"] == "running" for f in seen)
    approval_frames = [f for f in seen if f["type"] == "approval_request"]
    assert approvals == 2, approval_frames
    assert {bool(f.get("destructive")) for f in approval_frames} == {False, True}
    assert any(f["type"] == "activity" and f["text"] == "Undone." for f in seen)
    assert any(f["type"] == "belief_state" and f["status"] == "superseded" for f in seen)
    assert any(f["type"] == "skill_state" and f["skill_name"] == "weekly-report-formatter" for f in seen)
    assert any(f["type"] == "transcript" and not f["final"] for f in seen)
    assert any(f["type"] == "transcript" and f["final"] for f in seen)
    voice_states = [f["state"] for f in seen if f["type"] == "voice_state"]
    for state in ("wake", "listening", "thinking", "speaking", "idle"):
        assert state in voice_states, voice_states
    print("PASS -- demo everything covers streaming, task, both approvals, undo, memory, skill, and voice")


async def check_remaining_scenarios(port: int, token: str) -> None:
    scenarios = {
        "demo task": "task_state",
        "demo destructive": "approval_request",
        "demo memory": "belief_state",
        "demo voice": "transcript",
        "demo error": "error",
        "demo flood": "activity",
        "demo stream": "stream_frame",
        "demo tts-down": "activity",
        "demo stt-down": "activity",
    }
    for index, (trigger, expected_type) in enumerate(scenarios.items()):
        ws = await _connect_ui(port, token)
        seen_expected = False
        activity_count = 0
        try:
            await _snapshot(ws)
            await ws.send(json.dumps(_frame(
                "user_msg", text=trigger,
                conversation_id=f"phase1-scenario-{index}", source="ui"
            )))
            while True:
                frame = await _recv(ws, timeout=5.0)
                seen_expected |= frame["type"] == expected_type
                activity_count += frame["type"] == "activity"
                if frame["type"] == "approval_request":
                    await ws.send(json.dumps(_frame(
                        "approval_response", reply_to=frame["approval_id"], decision="deny"
                    )))
                if frame["type"] in {"done", "error"}:
                    break
                if (
                    trigger in {"demo task", "demo destructive"}
                    and frame["type"] == "task_state"
                    and frame["state"] == "paused"
                ):
                    break
            assert seen_expected, f"{trigger!r} never emitted {expected_type!r}"
            if trigger == "demo flood":
                assert activity_count == 2_000, activity_count
        finally:
            await ws.close()
    print("PASS -- standalone task, trust, memory, voice, flood, stream, error, and degraded scenarios validate")


async def check_memory_history_surface(port: int, token: str) -> None:
    """Contract 1.1 moved archived/superseded beliefs out of the connect
    snapshot and behind an explicit `memory_query`, and added `belief_deleted`
    plus `memory_edit{op:"purge"}`. None of that had gate coverage -- this file
    was only extended far enough to tolerate `capabilities_state`. Asserts the
    whole path, including the half that would regress silently: that the
    snapshot no longer carries history."""
    ws = await _connect_ui(port, token)
    try:
        snapshot = await _snapshot(ws)
        history_in_snapshot = [
            f for f in snapshot
            if f["type"] == "belief_state" and f["status"] in {"archived", "superseded"}
        ]
        assert not history_in_snapshot, history_in_snapshot

        await ws.send(json.dumps(_frame("memory_query")))
        start = await _recv(ws)
        assert start["type"] == "memory_history_state" and start["complete"] is False, start
        history: list[dict] = []
        while (frame := await _recv(ws))["type"] != "memory_history_state":
            assert frame["type"] == "belief_state", frame
            assert frame["status"] in {"archived", "superseded"}, frame
            history.append(frame)
        assert frame["complete"] is True, frame
        statuses = {f["status"] for f in history}
        assert statuses == {"archived", "superseded"}, history

        # purge is archived-only, and its rejection must be correlated or the
        # UI's rule-3 lock on that row never clears.
        await ws.send(json.dumps(_frame("memory_edit", belief_id="belief-editor", op="purge")))
        refused = await _recv(ws)
        assert refused["type"] == "error" and refused["code"] == "memory_purge_invalid", refused
        assert refused["operation_kind"] == "memory_edit", refused
        assert refused["operation_id"] == "belief-editor", refused

        archived = next(f["belief_id"] for f in history if f["status"] == "archived")
        await ws.send(json.dumps(_frame("memory_edit", belief_id=archived, op="purge")))
        deleted = await _recv(ws)
        assert deleted["type"] == "belief_deleted" and deleted["belief_id"] == archived, deleted

        # Purged for real: the history query no longer returns it.
        await ws.send(json.dumps(_frame("memory_query")))
        remaining = []
        while (frame := await _recv(ws))["type"] != "memory_history_state" or frame["complete"] is False:
            if frame["type"] == "belief_state":
                remaining.append(frame["belief_id"])
        assert archived not in remaining, remaining
    finally:
        await ws.close()
    print("PASS -- memory history query, purge, and belief_deleted round-trip (contract 1.1)")


async def main() -> None:
    print("=== Halo Phase 1 mock E2E check ===")
    server, token = await start(mock=True)
    port = server.sockets[0].getsockname()[1]
    try:
        await check_snapshot_reconnect_is_idempotent(port, token)
        await check_approval_branches(port, token)
        await check_undo_reverses(port, token)
        await check_everything_walkthrough(port, token)
        await check_remaining_scenarios(port, token)
        # Last: purge permanently drops a seeded belief, so anything asserting
        # on the seeded set must have run already.
        await check_memory_history_surface(port, token)
    finally:
        server.close()
        await server.wait_closed()
    print("=== Halo Phase 1 mock E2E: ALL CHECKS PASS ===")


if __name__ == "__main__":
    asyncio.run(main())
