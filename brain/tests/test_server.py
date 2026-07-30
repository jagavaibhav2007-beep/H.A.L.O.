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
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")  # secrets_store test seam -- never touch real keyring

import websockets

from brain import graph, secrets_store, server
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


async def _drain_snapshot(ws) -> None:
    """Drain the Step-9 connect snapshot; server.py ends it with the explicit
    `snapshot_complete` marker (spend_update used to be the de-facto sentinel,
    a role the contract never gave it)."""
    while json.loads(await asyncio.wait_for(ws.recv(), timeout=5))["type"] != "snapshot_complete":
        pass


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
        # A fresh non-mock UI connection gets one settings_state push right
        # after hello_ack (server.py) -- drain it before asserting the
        # malformed-frame error sequence below.
        settings = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
        assert settings["type"] == "settings_state", settings
        await _drain_snapshot(ws)  # rest of the Step-9 connect snapshot
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
            # An earlier check's turn runs as a background task, and its
            # spend_update is a GLOBAL broadcast (session/month totals) that
            # correctly reaches every connected client -- including one opened
            # after that turn started. Skip such unsolicited frames rather than
            # mistaking them for this connection's reply.
            while True:
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                if reply["type"] != "spend_update":
                    break
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


async def check_snapshot_not_interleaved() -> None:
    """A broadcast aimed at a client whose snapshot is still streaming must be
    HELD and delivered after it, never spliced into the middle -- otherwise
    `spend_update` (the snapshot's last-frame sentinel every drain reads to)
    can arrive first, and the UI sees a live delta before the state it applies
    to. This was a real intermittent failure.

    Driven directly rather than by racing two sockets: the bug needs a
    broadcast to land inside the snapshot's own await window, which timing
    alone reproduces only sometimes (a racing version of this test passed 6/6
    against the BROKEN code -- it proved nothing)."""

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, raw: str) -> None:
            self.sent.append(raw)

    ws = FakeWS()
    authenticated = {ws: "ui"}
    server._deferred[ws] = []  # client is mid-snapshot
    try:
        await server._broadcast(authenticated, "spend_update", {"session_usd": 1.0, "month_usd": 2.0})
        assert ws.sent == [], "broadcast leaked into a snapshot that was still streaming"
        assert len(server._deferred[ws]) == 1, server._deferred[ws]

        # Snapshot finishes -> the REAL _release_deferred flushes held frames.
        await server._release_deferred(ws, authenticated)
        assert [json.loads(r)["type"] for r in ws.sent] == ["spend_update"], ws.sent

        # Once released, broadcasts pass straight through again.
        await server._broadcast(authenticated, "spend_update", {"session_usd": 3.0, "month_usd": 4.0})
        assert len(ws.sent) == 2, ws.sent
    finally:
        server._deferred.pop(ws, None)
        server._deferred_bytes.pop(ws, None)
    print("[check 11] broadcast during a snapshot is held, then flushed in order: OK")


async def check_broadcast_during_flush_queues_behind_held_frames() -> None:
    """The other half of the interleave window, which had no coverage at all:
    a broadcast landing DURING the flush must queue behind the frames still
    held, not overtake them. The previous test reimplemented the drain inline,
    so it could never see that the real function popped the deferral key before
    draining -- leaving `_deferred[ws]` absent for the whole flush, which sends
    every live frame straight out ahead of held ones.

    Driven by a socket whose send() fires one live broadcast while the flush is
    still in progress -- exactly the window that reopened."""

    class ReentrantWS:
        """Fires a live broadcast from inside the first flushed send."""

        def __init__(self) -> None:
            self.sent: list[dict] = []
            self.authenticated: dict = {}
            self.fired = False

        async def send(self, raw: str) -> None:
            self.sent.append(json.loads(raw))
            if not self.fired:
                self.fired = True
                await server._broadcast(self.authenticated, "voice_state", {"state": "listening"})

    ws = ReentrantWS()
    authenticated = {ws: "ui"}
    ws.authenticated = authenticated
    server._deferred[ws] = []
    server._deferred_bytes[ws] = 0
    try:
        for state in ("idle", "wake", "thinking"):
            await server._broadcast(authenticated, "voice_state", {"state": state})
        assert ws.sent == [], "broadcasts leaked during the snapshot"

        await server._release_deferred(ws, authenticated)
        assert [f["state"] for f in ws.sent] == ["idle", "wake", "thinking", "listening"], ws.sent
        assert ws not in server._deferred, "deferral key survived an emptied queue"
        assert ws not in server._deferred_bytes, "deferral byte count survived an emptied queue"
    finally:
        server._deferred.pop(ws, None)
        server._deferred_bytes.pop(ws, None)
    print("[check 20] a broadcast during the flush queues behind still-held frames: OK")


async def check_broadcast_closes_a_stalled_client() -> None:
    """A client that stopped reading (send buffer full, socket still OPEN) must
    be dropped from routing AND closed. Leaving teardown to the connection
    handler's `finally` never happens: that handler is parked in
    `async for raw in ws` and only reaches `finally` once the socket closes --
    so the socket, its handler task and its state leak for the process
    lifetime. The overflow branch already closes with 1013; this mirrors it."""

    class StalledWS:
        def __init__(self) -> None:
            self.closed: tuple | None = None

        async def send(self, raw: str) -> None:
            await asyncio.Event().wait()

        async def close(self, code=None, reason=None) -> None:
            self.closed = (code, reason)

    stalled = StalledWS()
    authenticated = {stalled: "ui"}
    old_timeout = server._SEND_TIMEOUT_S
    server._SEND_TIMEOUT_S = 0.02
    try:
        await server._broadcast(authenticated, "voice_state", {"state": "idle"})
    finally:
        server._SEND_TIMEOUT_S = old_timeout
    assert stalled not in authenticated, "stalled client stayed routable"
    assert stalled.closed == (1013, "send timeout"), stalled.closed
    print("[check 21] a stalled client is de-routed AND its socket closed: OK")


async def check_non_ascii_token_is_rejected_not_raised() -> None:
    """The contract validates `hello.token` as a str only, so a non-ASCII token
    reaches compare_digest -- which raises TypeError on str operands that
    aren't pure ASCII, from OUTSIDE every try. No auth bypass (the connection
    drops either way), but the traceback was reachable by any unauthenticated
    local process. Driven through _auth directly: over the wire, "raised" and
    "rejected" both look like a closed socket."""

    class OneFrameWS:
        def __init__(self, frame: dict) -> None:
            self.raw = json.dumps(frame)

        async def recv(self) -> str:
            return self.raw

    for supplied in ("é", "你好", "wrong-but-ascii"):
        role = await server._auth(OneFrameWS(_frame("hello", token=supplied)), "real-token", 1)
        assert role is None, supplied
    accepted = await server._auth(OneFrameWS(_frame("hello", token="real-token")), "real-token", 1)
    assert accepted == "ui", accepted
    print("[check 22] a non-ASCII hello token is rejected, not raised: OK")


async def check_sends_and_deferred_queue_are_bounded() -> None:
    class StalledWS:
        async def send(self, raw: str) -> None:
            await asyncio.Event().wait()

        async def close(self, *args, **kwargs) -> None:
            return None

    stalled = StalledWS()
    old_timeout = server._SEND_TIMEOUT_S
    old_frames = server._DEFERRED_MAX_FRAMES
    old_bytes = server._DEFERRED_MAX_BYTES
    server._SEND_TIMEOUT_S = 0.02
    server._DEFERRED_MAX_FRAMES = 1
    server._DEFERRED_MAX_BYTES = 1024
    try:
        try:
            await server._send(stalled, "settings_state", {"key": "openrouter_key", "status": "set"})
            raise AssertionError("a stalled direct/snapshot send was allowed to hang")
        except asyncio.TimeoutError:
            pass

        authenticated = {stalled: "ui"}
        server._deferred[stalled] = []
        await server._broadcast(authenticated, "spend_update", {"session_usd": 1.0, "month_usd": 1.0})
        assert stalled in authenticated and len(server._deferred[stalled]) == 1
        await server._broadcast(authenticated, "spend_update", {"session_usd": 2.0, "month_usd": 2.0})
        assert stalled not in authenticated, "overflowing snapshot client remained routable"
        assert stalled not in server._deferred, "overflowing deferred queue remained allocated"
    finally:
        server._SEND_TIMEOUT_S = old_timeout
        server._DEFERRED_MAX_FRAMES = old_frames
        server._DEFERRED_MAX_BYTES = old_bytes
        server._deferred.pop(stalled, None)
    print("[check 12] direct sends time out and deferred snapshot queues are bounded: OK")


async def _recv_settings_state(ws) -> dict:
    """Next settings_state, skipping an earlier check's global spend_update --
    same unsolicited-broadcast race check 4 documents."""
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        if frame["type"] == "settings_state":
            return frame
        assert frame["type"] == "spend_update", frame


async def check_settings_update_round_trip(port: int, token: str) -> None:
    """settings_update for openrouter_key -> a settings_state reply to the
    sender only (not broadcast), reporting "set" (HALO_LLM_STUB makes
    validate_key an instant True) then "missing" after an empty value."""
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        initial = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
        assert initial["type"] == "settings_state" and initial["status"] == "missing", initial
        await _drain_snapshot(ws)  # rest of the Step-9 connect snapshot

        await ws.send(json.dumps(_frame("settings_update", key="openrouter_key", value="sk-or-test")))
        saved = await _recv_settings_state(ws)
        assert saved["key"] == "openrouter_key", saved
        assert saved["status"] == "set", saved  # HALO_LLM_STUB -> validate_key() is instant True

        await ws.send(json.dumps(_frame("settings_update", key="openrouter_key", value="")))
        cleared = await _recv_settings_state(ws)
        assert cleared["status"] == "missing", cleared
    finally:
        await ws.close()
    print("[check 10] settings_update -> settings_state reply (set, then missing on clear): OK")


async def check_settings_failures_and_status_persistence(port: int, token: str) -> None:
    frames: list[tuple[str, dict]] = []

    async def capture(msg_type: str, payload: dict) -> None:
        frames.append((msg_type, payload))

    real_set = secrets_store.set_key
    real_delete = secrets_store.delete_key
    real_validate = graph.llm.validate_key
    real_status = secrets_store.key_status
    try:
        def fail_set(value: str) -> None:
            raise RuntimeError("sentinel-set-failure")

        secrets_store.set_key = fail_set
        await server._handle_settings_update({"key": "openrouter_key", "value": "must-not-leak"}, capture)
        assert [kind for kind, _ in frames] == ["settings_state", "error"], frames
        assert frames[0][1]["status"] == "invalid" and frames[1][1]["recoverable"] is True
        assert "must-not-leak" not in json.dumps(frames)

        frames.clear()

        def fail_delete() -> None:
            raise RuntimeError("sentinel-delete-failure")

        secrets_store.delete_key = fail_delete
        await server._handle_settings_update({"key": "openrouter_key", "value": ""}, capture)
        assert [kind for kind, _ in frames] == ["settings_state", "error"], frames
        assert frames[0][1]["status"] == "invalid"

        secrets_store.set_key = real_set
        secrets_store.delete_key = real_delete

        async def reject_key(value: str) -> bool:
            return False

        graph.llm.validate_key = reject_key
        frames.clear()
        await server._handle_settings_update({"key": "openrouter_key", "value": "rejected"}, capture)
        assert frames == [("settings_state", {"key": "openrouter_key", "status": "invalid"})], frames
        assert secrets_store.key_status() == "invalid"

        async def validation_offline(value: str) -> bool:
            raise OSError("offline")

        graph.llm.validate_key = validation_offline
        frames.clear()
        await server._handle_settings_update({"key": "openrouter_key", "value": "unverified"}, capture)
        assert frames == [("settings_state", {"key": "openrouter_key", "status": "unverified"})], frames
        assert secrets_store.key_status() == "unverified"

        def fail_status() -> str:
            raise RuntimeError("sentinel-status-failure")

        secrets_store.key_status = fail_status
        ws = await _connect(port)
        try:
            await _authenticate(ws, token)
            status = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            error = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            assert status["type"] == "settings_state" and status["status"] == "invalid", status
            assert error["type"] == "error" and error["recoverable"] is True, error
            await _drain_snapshot(ws)
        finally:
            await ws.close()
    finally:
        secrets_store.set_key = real_set
        secrets_store.delete_key = real_delete
        graph.llm.validate_key = real_validate
        secrets_store.key_status = real_status
        secrets_store.delete_key()
    print("[check 13] keyring failures report invalid+error; invalid/unverified survive snapshots: OK")


async def check_tasks_are_supervised_and_cancelled_on_close() -> None:
    real_handler = server._handle_settings_update
    raised = asyncio.Event()

    async def explode(msg: dict, send_fn) -> None:
        raised.set()
        raise RuntimeError("sentinel-handler-failure")

    server._handle_settings_update = explode
    managed, token = await start()
    port = managed.sockets[0].getsockname()[1]
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        await ws.send(json.dumps(_frame("settings_update", key="openrouter_key", value="secret")))
        await asyncio.wait_for(raised.wait(), timeout=1)
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
        assert reply["type"] == "error" and reply["code"] == "request_failed", reply
    finally:
        await ws.close()
        managed.close()
        await managed.wait_closed()

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def block(msg: dict, send_fn) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    server._handle_settings_update = block
    managed, token = await start()
    port = managed.sockets[0].getsockname()[1]
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        await ws.send(json.dumps(_frame("settings_update", key="openrouter_key", value="secret")))
        await asyncio.wait_for(started.wait(), timeout=1)
        retained_tasks = tuple(managed._runtime.tasks)
        assert len(retained_tasks) >= 2  # request handler + daily decay loop
        managed.close()
        await managed.wait_closed()
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        assert all(task.done() for task in retained_tasks)
    finally:
        server._handle_settings_update = real_handler
        await ws.close()
    print("[check 14] detached handlers are observed; close cancels handlers and decay work: OK")


async def check_real_task_controls_fail_honestly() -> None:
    frames = []
    async def send(kind, payload):
        frames.append((kind, payload))
    await server._send_unsupported_operation(_frame("task_op", task_id="task-1", op="pause"), send)
    await server._send_unsupported_operation(_frame("lane_pin", task_id="task-2", lane=3), send)
    # mic carries no domain id -> correlates by the envelope id (fallback);
    # skill_op correlates by skill_name, which is what SkillsView locks on.
    mic = _frame("mic", op="mute")
    skill = _frame("skill_op", skill_name="demo", op="disable")
    await server._send_unsupported_operation(mic, send)
    await server._send_unsupported_operation(skill, send)
    assert frames[0][1]["operation_kind"] == "task_op" and frames[0][1]["operation_id"] == "task-1", frames
    assert frames[1][1]["operation_kind"] == "lane_pin" and frames[1][1]["operation_id"] == "task-2", frames
    assert frames[2][1]["operation_kind"] == "mic" and frames[2][1]["operation_id"] == mic["id"], frames
    assert frames[3][1]["operation_kind"] == "skill_op" and frames[3][1]["operation_id"] == "demo", frames
    assert all(payload["code"] == "operation_unsupported" for _, payload in frames), frames
    assert "Voice input" in frames[2][1]["message"], frames
    assert "Skill controls" in frames[3][1]["message"], frames
    print("[check 15] real task/mic/skill controls return correlated unsupported errors: OK")


def check_real_dispatch_covers_all_ui_inbound_types() -> None:
    """Every UI-sendable inbound type in the contract has a real (non-mock)
    dispatch branch. A new inbound type added to the contract but not wired into
    _connection_handler is the "mock handles it, real Brain drops it silently,
    affordance hangs forever" bug (mem/Bugs.md #12) -- this fails the instant
    the handled set drifts from the contract. `hello` is excluded: _auth
    consumes it before the message loop."""
    from brain.ipc.contract import CONTRACT_SPEC

    inbound = {
        name
        for name, spec in CONTRACT_SPEC["messages"].items()
        if spec["direction"] == "inbound" and name != "hello"
    }
    assert inbound == server._REAL_DISPATCH_TYPES, inbound ^ server._REAL_DISPATCH_TYPES
    print("[check 17] real dispatch covers every UI-sendable inbound type: OK")


def check_mock_dispatch_covers_all_ui_inbound_types() -> None:
    """The same guard for the OTHER dispatch table -- which never had one, even
    though the documented "affordance hangs forever waiting for a confirming
    frame" incident (mem/Bugs.md #12) happened on the MOCK side. `user_msg` is
    excluded with `hello`: it has its own branch ahead of the table. Also
    resolves each handler name on mock.py, since a typo there is the same hang
    one layer down."""
    from brain import mock as mock_engine
    from brain.ipc.contract import CONTRACT_SPEC

    inbound = {
        name
        for name, spec in CONTRACT_SPEC["messages"].items()
        if spec["direction"] == "inbound" and name not in ("hello", "user_msg")
    }
    assert inbound == set(server._MOCK_DISPATCH), inbound ^ set(server._MOCK_DISPATCH)
    for handler_name, _needs_send in server._MOCK_DISPATCH.values():
        assert callable(getattr(mock_engine, handler_name, None)), handler_name
    print("[check 23] mock dispatch covers every UI-sendable inbound type: OK")


async def check_real_dispatch_routes_unsupported_over_wire(port: int, token: str) -> None:
    """Proves the dispatch BRANCH (not just the helper) routes mic/skill_op:
    the exact regression the mock-only handler masked. Each returns a correlated
    operation_unsupported error over the real socket, connection stays up."""
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        settings = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
        assert settings["type"] == "settings_state", settings
        await _drain_snapshot(ws)
        # mic has no domain id -> correlates by the envelope id; skill_op does
        # (skill_name), which is what the SkillsView rule-3 lock keys off.
        for frame, expected_id in (
            (_frame("mic", op="mute"), None),
            (_frame("skill_op", skill_name="x", op="disable"), "x"),
        ):
            await ws.send(json.dumps(frame))
            while True:
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                if reply["type"] != "spend_update":  # unsolicited global broadcast
                    break
            assert reply["type"] == "error" and reply["code"] == "operation_unsupported", reply
            assert reply["operation_kind"] == frame["type"], reply
            assert reply["operation_id"] == (expected_id or frame["id"]), reply
    finally:
        await ws.close()
    print("[check 18] real dispatch routes mic/skill_op to unsupported over the wire: OK")


async def check_contract_version_negotiation(port: int, token: str) -> None:
    """hello_ack advertises the Brain's contract version; a same-major client
    (any minor) is accepted; a major mismatch gets a terminal
    incompatible_contract error, then the socket closes -- so the UI can stop
    retrying instead of storming the reconnect loop forever."""
    from brain.ipc.contract import CONTRACT_VERSION

    ws = await _connect(port)
    try:
        await ws.send(json.dumps(_frame("hello", token=token, contract_version="1.999")))
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
        assert ack["type"] == "hello_ack" and ack["contract_version"] == CONTRACT_VERSION, ack
    finally:
        await ws.close()

    ws = await _connect(port)
    try:
        await ws.send(json.dumps(_frame("hello", token=token, contract_version="2.0")))
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
        assert reply["type"] == "error" and reply["code"] == "incompatible_contract", reply
        assert reply["recoverable"] is False, reply
        try:
            await asyncio.wait_for(ws.recv(), timeout=2)
            raise AssertionError("expected socket to close after incompatible_contract")
        except websockets.exceptions.ConnectionClosed:
            pass
    finally:
        await ws.close()
    print("[check 19] contract version: same-major accepted, major mismatch refused then closed: OK")


async def check_turn_admission_and_ordering() -> None:
    """Distinct real conversations are capped; same-conversation order holds."""
    real_run_turn = graph.run_turn
    locks: dict[str, asyncio.Lock] = {}
    slots = asyncio.Semaphore(2)
    active = 0
    peak = 0
    order: list[str] = []

    async def fake_run_turn(msg, _broadcast):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        order.append(msg["text"])
        await asyncio.sleep(0.02)
        active -= 1

    async def sink(*_args):
        return None

    graph.run_turn = fake_run_turn
    try:
        distinct = [
            asyncio.create_task(server._serialize_user_msg(
                _frame("user_msg", text=str(i), conversation_id=f"c-{i}", source="ui"),
                locks, slots, sink, sink, False,
            ))
            for i in range(12)
        ]
        await asyncio.gather(*distinct)
        assert peak == 2, peak

        order.clear()
        same = [
            asyncio.create_task(server._serialize_user_msg(
                _frame("user_msg", text=str(i), conversation_id="same", source="ui"),
                locks, slots, sink, sink, False,
            ))
            for i in range(5)
        ]
        await asyncio.gather(*same)
        assert order == ["0", "1", "2", "3", "4"], order
    finally:
        graph.run_turn = real_run_turn
    print("[check 16] real turns cap at 2 in the test; same-conversation order holds: OK")


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
        await check_settings_update_round_trip(port, token)
        await check_settings_failures_and_status_persistence(port, token)
        await check_snapshot_not_interleaved()
        await check_broadcast_during_flush_queues_behind_held_frames()
        await check_broadcast_closes_a_stalled_client()
        await check_sends_and_deferred_queue_are_bounded()
        await check_real_dispatch_routes_unsupported_over_wire(port, token)
        await check_contract_version_negotiation(port, token)
    finally:
        server.close()
        await server.wait_closed()
        # v2 memory arms an idle-consolidation timer per real turn; aclose ->
        # memory.flush_all cancels them so they don't leak past interpreter exit.
        await graph.aclose()
    await check_idle_auth_times_out()
    check_session_file_is_atomic_and_private()
    check_second_brain_lock_is_rejected()
    check_frame_visible_to_routing()
    await check_tasks_are_supervised_and_cancelled_on_close()
    await check_real_task_controls_fail_honestly()
    check_real_dispatch_covers_all_ui_inbound_types()
    check_mock_dispatch_covers_all_ui_inbound_types()
    await check_non_ascii_token_is_rejected_not_raised()
    await check_turn_admission_and_ordering()
    print("[brain.server] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
