"""Runnable self-check for brain/server.py (Phase 0 Steps 3-4).

No test framework -- plain asyncio + assert. Run with:
    python brain/tests/test_server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Phase 2: the non-mock default handler is the real graph (brain/graph.py).
# Run it against the deterministic offline LLM stub, with store/checkpoints
# redirected to a temp dir so tests never touch %LOCALAPPDATA%\Halo.
os.environ["HALO_LLM_STUB"] = "1"
_TMP = tempfile.mkdtemp(prefix="halo-test-server-")
os.environ["LOCALAPPDATA"] = _TMP

import websockets

from brain.server import (
    BrainAlreadyRunning,
    _frame_visible_to,
    single_instance_lock,
    start,
    write_session_file,
)


def _frame(msg_type: str, **payload) -> dict:
    return {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


async def _connect(port: int):
    return await websockets.connect(f"ws://127.0.0.1:{port}")


async def _authenticate(ws, token: str) -> None:
    await ws.send(json.dumps(_frame("hello", token=token)))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
    assert ack["type"] == "hello_ack", ack


async def _read_turn(ws, conversation_id: str, timeout: float = 10) -> str:
    """Read frames until `done` for this conversation; returns the joined
    token text. Skips unrelated frames (e.g. spend_update)."""
    parts: list[str] = []
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if frame["type"] == "token":
            assert frame["conversation_id"] == conversation_id, frame
            parts.append(frame["text"])
        elif frame["type"] == "done":
            assert frame["conversation_id"] == conversation_id, frame
            return "".join(parts)


async def check_good_token_echo(port: int, token: str) -> None:
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        conversation_id = "conv-1"
        await ws.send(json.dumps(_frame("user_msg", text="hi", conversation_id=conversation_id, source="ui")))
        reply = await _read_turn(ws, conversation_id)
        assert "hi" in reply, reply  # stub reply echoes the user text
    finally:
        await ws.close()
    print("[check 1] good token -> token(s)+done with matching conversation_id: OK")


async def check_bad_token_dropped(port: int) -> None:
    ws = await _connect(port)
    await ws.send(json.dumps(_frame("hello", token="not-the-real-token")))
    try:
        await asyncio.wait_for(ws.recv(), timeout=2)
        raise AssertionError("expected connection to be dropped, but got a frame")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await ws.close()
    print("[check 2] bad token -> connection dropped before any other frame: OK")


async def check_missing_hello_dropped(port: int) -> None:
    ws = await _connect(port)
    await ws.send(json.dumps(_frame("user_msg", text="hi", conversation_id="conv", source="ui")))
    try:
        await asyncio.wait_for(ws.recv(), timeout=2)
        raise AssertionError("expected a non-hello first frame to close the connection")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await ws.close()
    print("[check 3] missing hello -> connection dropped: OK")


async def check_idle_auth_times_out() -> None:
    server, _ = await start(auth_timeout=0.05)
    port = server.sockets[0].getsockname()[1]
    ws = await _connect(port)
    try:
        await asyncio.wait_for(ws.recv(), timeout=1)
        raise AssertionError("expected an idle unauthenticated connection to close")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await ws.close()
        server.close()
        await server.wait_closed()
    print("[check 6] idle unauthenticated connection -> timed out: OK")


def check_session_file_is_atomic_and_private() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "Halo" / "session.json"
        write_session_file(1234, "secret", path)
        assert json.loads(path.read_text(encoding="utf-8")) == {"port": 1234, "token": "secret"}
        assert not path.with_suffix(".tmp").exists()
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
    print("[check 7] session file is atomic and private: OK")


def check_second_brain_lock_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock_file = Path(tmp) / "brain.lock"
        with single_instance_lock(lock_file):
            try:
                with single_instance_lock(lock_file):
                    raise AssertionError("second Brain acquired the same lock")
            except BrainAlreadyRunning:
                pass
        with single_instance_lock(lock_file):
            pass
    print("[check 8] second Brain lock rejected and released cleanly: OK")


async def check_malformed_frame_rejected(port: int, token: str) -> None:
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        bad_frames = [
            {"type": "not_a_real_type", "id": "x", "ts": "x"},
            _frame("user_msg", text="hi", conversation_id=[], source="ui"),
            _frame("user_msg", text="hi", conversation_id="conv", source="other"),
            _frame("approval_response", reply_to=[], decision="approve"),
            _frame("interrupt", conversation_id=[]),
            _frame("undo", undo_token=[]),
            _frame("memory_edit", belief_id=[], op="delete"),
            _frame("memory_edit", belief_id="belief", op="erase"),
            _frame("skill_op", skill_name=[], op="disable"),
            _frame("skill_op", skill_name="skill", op="enable"),
            _frame("lane_pin", task_id="task", lane=True),
            _frame("lane_pin", task_id="task", lane=[]),
            _frame("lane_pin", task_id="task", lane={}),
            _frame("lane_pin", task_id="task", lane=4),
            _frame("task_op", task_id=[], op="stop"),
            _frame("task_op", op="restart"),
            _frame("mic", op="explode"),
        ]
        for frame in bad_frames:
            await ws.send(json.dumps(frame))
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
            assert reply["type"] == "error", reply
    finally:
        await ws.close()
    print("[check 4] malformed frames -> error frames, connection stays up: OK")


async def check_conversation_order(port: int, token: str) -> None:
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        cid = "conv-order"
        await ws.send(json.dumps(_frame("user_msg", text="first", conversation_id=cid, source="ui")))
        await ws.send(json.dumps(_frame("user_msg", text="second", conversation_id=cid, source="ui")))

        reply_1 = await _read_turn(ws, cid)
        reply_2 = await _read_turn(ws, cid)
        assert "first" in reply_1 and "second" not in reply_1, reply_1
        assert "second" in reply_2, reply_2
    finally:
        await ws.close()
    print("[check 5] two messages to one conversation handled in arrival order: OK")


def check_frame_visible_to_routing() -> None:
    """Full truth table for the Voice/UI routing rule (server.py). test_mock's
    check 7 exercises token(yes)/done(no) over the wire, but never the subtlest
    branch: `activity` reaches Voice ONLY when narrate is True. This pins it."""
    # Voice sees only its subset.
    assert _frame_visible_to("voice", "token", {}) is True
    assert _frame_visible_to("voice", "approval_request", {}) is True
    assert _frame_visible_to("voice", "activity", {"narrate": True}) is True
    assert _frame_visible_to("voice", "activity", {"narrate": False}) is False
    assert _frame_visible_to("voice", "activity", {}) is False  # missing narrate != narrated
    assert _frame_visible_to("voice", "done", {}) is False
    assert _frame_visible_to("voice", "task_state", {}) is False
    # UI gets everything, regardless of narrate.
    assert _frame_visible_to("ui", "done", {}) is True
    assert _frame_visible_to("ui", "activity", {"narrate": False}) is True
    print("[check 9] _frame_visible_to routing truth table (Voice narrated-only, UI all): OK")


async def main() -> None:
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        await check_good_token_echo(port, token)
        await check_bad_token_dropped(port)
        await check_missing_hello_dropped(port)
        await check_malformed_frame_rejected(port, token)
        await check_conversation_order(port, token)
    finally:
        server.close()
        await server.wait_closed()
    await check_idle_auth_times_out()
    check_session_file_is_atomic_and_private()
    check_second_brain_lock_is_rejected()
    check_frame_visible_to_routing()
    print("[brain.server] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
