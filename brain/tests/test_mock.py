"""Runnable self-check for brain/brain/mock.py (Phase 1 Step 2).

No test framework -- plain asyncio + assert, matching test_server.py style.
Run with:
    python brain/tests/test_mock.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain.ipc.contract import parse_ipc_message
from brain.server import start

import test_server  # brain/tests/test_server.py -- reuse _frame/_connect/_authenticate

_frame = test_server._frame
_connect = test_server._connect
_authenticate = test_server._authenticate

SNAPSHOT_TYPES = {"belief_state", "skill_state", "task_state", "approval_request", "spend_update"}


async def _recv(ws) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=2)
    frame = json.loads(raw)
    parse_ipc_message(frame)  # (b) every emitted frame must validate against the contract
    return frame


async def _drain_snapshot(ws) -> None:
    """push_snapshot always ends with spend_update -- see mock.py."""
    while True:
        frame = await _recv(ws)
        assert frame["type"] in SNAPSHOT_TYPES, f"unexpected frame during snapshot: {frame}"
        if frame["type"] == "spend_update":
            return


async def check_snapshot_after_hello_ack(port: int, token: str) -> None:
    """(d) snapshot arrives right after hello_ack and before any scenario."""
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)  # asserts the very first frame is hello_ack
        seen_types = []
        while True:
            frame = await _recv(ws)
            assert frame["type"] in SNAPSHOT_TYPES, f"unexpected non-snapshot frame before any user_msg: {frame}"
            seen_types.append(frame["type"])
            if frame["type"] == "spend_update":
                break
    finally:
        await ws.close()
    assert {"belief_state", "skill_state", "task_state"} <= set(seen_types), seen_types
    print("[check 1] snapshot arrives right after hello_ack, before any scenario: OK")


async def check_demo_error_frame_sequence(port: int, token: str) -> None:
    """(a) a trigger produces the expected ordered frame sequence."""
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        await ws.send(json.dumps(_frame("user_msg", text="demo error", conversation_id="c-err", source="ui")))
        types = [(await _recv(ws))["type"], (await _recv(ws))["type"]]
        assert types == ["token", "error"], types
    finally:
        await ws.close()
    print("[check 2] 'demo error' trigger -> [token, error] in order: OK")


async def check_approval_round_trip(port: int, token: str) -> None:
    """(c) the approval await-point branches correctly on approve vs deny."""
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        await ws.send(json.dumps(_frame("user_msg", text="demo approval", conversation_id="c-appr", source="ui")))
        req = await _recv(ws)
        while req["type"] != "approval_request":
            req = await _recv(ws)  # skip task_state:waiting_approval
        await ws.send(json.dumps(_frame("approval_response", reply_to=req["approval_id"], decision="approve")))
        frame = await _recv(ws)
        while frame["type"] not in ("activity", "task_state", "done"):
            frame = await _recv(ws)
        assert frame["type"] == "activity" and frame["undoable"] is True, frame
    finally:
        await ws.close()
    print("[check 3a] approval round-trip: approve -> undoable activity emitted: OK")

    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        await ws.send(json.dumps(_frame("user_msg", text="demo approval", conversation_id="c-appr-2", source="ui")))
        req = await _recv(ws)
        while req["type"] != "approval_request":
            req = await _recv(ws)
        await ws.send(json.dumps(_frame("approval_response", reply_to=req["approval_id"], decision="deny")))
        frame = await _recv(ws)
        while frame["type"] not in ("activity", "task_state"):
            frame = await _recv(ws)
        assert frame["type"] == "activity" and "denied" in frame["text"].lower(), frame
    finally:
        await ws.close()
    print("[check 3b] approval round-trip: deny -> denial activity emitted: OK")


async def check_second_approval_response_gets_error(port: int, token: str) -> None:
    """Two clients answering the same approval: first wins, second gets a
    recoverable error (not a second, contradictory resolution)."""
    ws1 = await _connect(port)
    ws2 = await _connect(port)
    try:
        await _authenticate(ws1, token)
        await _drain_snapshot(ws1)
        await _authenticate(ws2, token)
        await _drain_snapshot(ws2)

        await ws1.send(json.dumps(_frame("user_msg", text="demo approval", conversation_id="c-race", source="ui")))
        req = await _recv(ws1)
        while req["type"] != "approval_request":
            req = await _recv(ws1)
        # ws2 also observes the broadcast approval_request -- drain until it does.
        req2 = await _recv(ws2)
        while req2["type"] != "approval_request":
            req2 = await _recv(ws2)

        await ws1.send(json.dumps(_frame("approval_response", reply_to=req["approval_id"], decision="approve")))

        # Drain ws2's view of the resolution broadcast (activity, task_state,
        # done) so the future is unambiguously resolved before ws2's late
        # response arrives at the server.
        resolved = await _recv(ws2)
        while resolved["type"] != "done":
            resolved = await _recv(ws2)

        await ws2.send(json.dumps(_frame("approval_response", reply_to=req["approval_id"], decision="deny")))
        err = await _recv(ws2)
        assert err["type"] == "error" and err["recoverable"] is True, err
    finally:
        await ws1.close()
        await ws2.close()
    print("[check 4] second approval_response for an already-resolved approval -> recoverable error: OK")


async def check_interrupt_cancels_pending_approval(port: int, token: str) -> None:
    """Interrupt-vs-pending-approval rule (11-ipc-contract.md): interrupt
    cancels the pending approval as an implicit deny, then the task pauses --
    the stale-card rule Step 10 relies on."""
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        conversation_id = "c-interrupt"
        await ws.send(json.dumps(_frame("user_msg", text="demo approval", conversation_id=conversation_id, source="ui")))
        req = await _recv(ws)
        while req["type"] != "approval_request":
            req = await _recv(ws)
        await ws.send(json.dumps(_frame("interrupt", conversation_id=conversation_id)))
        frame = await _recv(ws)
        assert frame["type"] == "task_state" and frame["state"] == "paused", frame
    finally:
        await ws.close()
    print("[check 5] interrupt during a pending approval -> implicit deny + task_state:paused: OK")


async def check_linear_scenarios_all_validate(port: int, token: str) -> None:
    """(b), widened: every trigger that doesn't suspend on an approval emits
    only contract-valid frames -- not just the two triggers checks 2-4 happen
    to exercise."""
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        for i, text in enumerate(("demo memory", "demo skill", "demo voice", "demo flood", "demo stream", "just chatting")):
            await ws.send(json.dumps(_frame("user_msg", text=text, conversation_id=f"c-lin-{i}", source="ui")))
            count = 0
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    break
                parse_ipc_message(json.loads(raw))
                count += 1
            assert count > 0, f"trigger {text!r} produced no frames"
    finally:
        await ws.close()
    print("[check 6] every linear scenario (memory/skill/voice/flood/stream/generic) emits only contract-valid frames: OK")


async def check_voice_routing_subset(port: int, token: str) -> None:
    """Regression guard for the contract's routing rule (11-ipc-contract.md): a
    role:"voice" client must NOT receive the snapshot, and must NOT receive
    broadcast frames outside its subset (e.g. `done`) -- only token / narrated
    activity / approval_request. This shipped green once because no test
    asserted it; only running the real stack surfaced Voice getting the
    snapshot."""
    voice = await _connect(port)
    ui = await _connect(port)
    try:
        # Voice authenticates like any client, then must sit in silence -- the
        # snapshot goes to UI clients only.
        await voice.send(json.dumps(_frame("hello", token=token, role="voice")))
        assert json.loads(await asyncio.wait_for(voice.recv(), timeout=1))["type"] == "hello_ack"
        try:
            leaked = json.loads(await asyncio.wait_for(voice.recv(), timeout=1))
            assert False, f"voice received an unsolicited snapshot frame: {leaked}"
        except asyncio.TimeoutError:
            pass  # silence after hello_ack is exactly right

        # A UI client drives a generic reply -- token(s) + done, broadcast to all.
        await _authenticate(ui, token)
        await _drain_snapshot(ui)
        await ui.send(json.dumps(_frame("user_msg", text="route check", conversation_id="c-route", source="ui")))

        # Voice must see the token (in its subset) but never the done (outside it).
        got_token = False
        while True:
            try:
                frame = json.loads(await asyncio.wait_for(voice.recv(), timeout=1))
            except asyncio.TimeoutError:
                break
            assert frame["type"] != "done", f"voice received 'done', outside its subset: {frame}"
            if frame["type"] == "token":
                got_token = True
        assert got_token, "voice did not receive the broadcast token it should have"
    finally:
        await voice.close()
        await ui.close()
    print("[check 7] role:voice gets its routing subset only (no snapshot, token not done): OK")


async def check_same_conversation_is_serialized(port: int, token: str) -> None:
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        conversation_id = "c-serialized"
        await ws.send(json.dumps(_frame("user_msg", text="demo error", conversation_id=conversation_id, source="ui")))
        await ws.send(json.dumps(_frame("user_msg", text="second turn", conversation_id=conversation_id, source="ui")))
        first_two = [(await _recv(ws))["type"], (await _recv(ws))["type"]]
        assert first_two == ["token", "error"], first_two
    finally:
        await ws.close()
    print("[check 8] mock messages in one conversation are serialized: OK")


async def check_skill_op_round_trip(port: int, token: str) -> None:
    """Step 13: disable -> paused, restore -> active (reason cleared)."""
    ws = await _connect(port)
    try:
        await _authenticate(ws, token)
        await _drain_snapshot(ws)
        skill_name = "invoice-formatter"  # seeded active/user skill

        await ws.send(json.dumps(_frame("skill_op", skill_name=skill_name, op="disable")))
        disabled = await _recv(ws)
        assert disabled["type"] == "skill_state" and disabled["status"] == "paused", disabled

        await ws.send(json.dumps(_frame("skill_op", skill_name=skill_name, op="restore")))
        restored = await _recv(ws)
        assert restored["type"] == "skill_state" and restored["status"] == "active", restored
        assert "reason" not in restored, restored
    finally:
        await ws.close()
    print("[check 9] skill_op disable->paused, restore->active (reason cleared): OK")


async def main() -> None:
    server, token = await start(mock=True)
    port = server.sockets[0].getsockname()[1]
    try:
        await check_snapshot_after_hello_ack(port, token)
        await check_demo_error_frame_sequence(port, token)
        await check_approval_round_trip(port, token)
        await check_second_approval_response_gets_error(port, token)
        await check_interrupt_cancels_pending_approval(port, token)
        await check_linear_scenarios_all_validate(port, token)
        await check_voice_routing_subset(port, token)
        await check_same_conversation_is_serialized(port, token)
        await check_skill_op_round_trip(port, token)
    finally:
        server.close()
        await server.wait_closed()
    print("[brain.mock] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
