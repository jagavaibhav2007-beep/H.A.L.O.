"""Runnable self-check for brain/gate.py (Phase 2 Step 5).

No test framework -- plain asyncio + assert, matching test_graph.py. Run with:
    python brain/tests/test_gate.py

Drives the real server in-process as a fake UI client, with the LLM stub and
all data dirs redirected to a temp dir. Tools are fake test tools registered
straight into gate.TOOLS; the "CALL_TOOL <name> <json>" sentinel (graph.py's
Step-5 test seam) makes the graph attempt them deterministically.
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

_TMP = tempfile.mkdtemp(prefix="halo-test-gate-")
os.environ["HALO_LLM_STUB"] = "1"
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_CHECKPOINT_DB"] = str(Path(_TMP) / "checkpoints.db")
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")

import websockets

from brain import gate, graph, store
from brain.server import start

EXECUTED: list[tuple[str, dict]] = []


def _make_tool(name):
    def fn(args):
        EXECUTED.append((name, dict(args)))
        return "done"

    return fn


gate.register("t1_tool", _make_tool("t1_tool"), tier=1, summary="I read something quietly.")
gate.register("t2_tool", _make_tool("t2_tool"), tier=2, summary="I moved a file in your project.")
gate.register("t3_tool", _make_tool("t3_tool"), tier=3, summary="I want to send an email.")
gate.register(
    "t3_boom", _make_tool("t3_boom"), tier=3, destructive=True, summary="I want to delete 12 files."
)
# Arg-predicate rule: risky args are Tier 3, safe ones Tier 2 -- exercises the
# edit-reclassify branch in both directions.
gate.register(
    "argtool",
    _make_tool("argtool"),
    tier=lambda args: 3 if args.get("risky") else 2,
    summary="I want to run argtool.",
)
gate.register("t_broken_rule", _make_tool("t_broken_rule"), tier=lambda args: 1 / 0)


def _frame(msg_type: str, **payload) -> dict:
    return {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


async def _connect_auth(port: int, token: str):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(json.dumps(_frame("hello", token=token)))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
    assert ack["type"] == "hello_ack", ack
    # Step 9: drain the whole snapshot. `spend_update` is always its last
    # frame (the "snapshot done" sentinel), so draining to it stays correct as
    # the snapshot grows -- no per-suite frame counting.
    while json.loads(await asyncio.wait_for(ws.recv(), timeout=5))["type"] != "spend_update":
        pass
    return ws


async def _call_tool(ws, cid: str, tool: str, args: dict) -> None:
    text = f"CALL_TOOL {tool} {json.dumps(args)}"
    await ws.send(json.dumps(_frame("user_msg", text=text, conversation_id=cid, source="ui")))


async def _recv(ws, timeout: float = 10) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def _recv_type(ws, msg_type: str, timeout: float = 10) -> dict:
    """Next frame of msg_type, skipping spend_update noise."""
    while True:
        frame = await _recv(ws, timeout)
        if frame["type"] == msg_type:
            return frame
        assert frame["type"] == "spend_update", f"unexpected frame waiting for {msg_type}: {frame}"


async def _respond(ws, approval_id: str, decision: str, edited_args: dict | None = None) -> None:
    payload = {"reply_to": approval_id, "decision": decision}
    if edited_args is not None:
        payload["edited_args"] = edited_args
    await ws.send(json.dumps(_frame("approval_response", **payload)))


def _actions_for(tool: str) -> list[dict]:
    store.connect()
    return [a for a in store.recent_actions(200) if a["tool"] == tool]


def check_classify_table() -> None:
    assert gate.classify("t1_tool", {}) == 1
    assert gate.classify("t2_tool", {}) == 2
    assert gate.classify("t3_tool", {}) == 3
    assert gate.classify("no_such_tool", {}) == 3  # unknown -> fail safe
    assert gate.classify("t_broken_rule", {}) == 3  # classify exception -> fail safe
    assert gate.classify("argtool", {"risky": True}) == 3
    assert gate.classify("argtool", {"risky": False}) == 2
    assert gate.is_destructive("t3_boom", {}) is True
    assert gate.is_destructive("t3_tool", {}) is False
    assert "delete" in gate.summarize("t3_boom", {})
    print("[check 1] classify table: T1/T2/T3, unknown->3, exception->3, destructive flag: OK")


async def check_tier1(port: int, token: str) -> None:
    ws = await _connect_auth(port, token)
    try:
        await _call_tool(ws, "gate-t1", "t1_tool", {"path": "x"})
        frame = await _recv(ws)
        assert frame["type"] == "done" and frame["conversation_id"] == "gate-t1", frame
        assert ("t1_tool", {"path": "x"}) in EXECUTED
        rows = _actions_for("t1_tool")
        assert rows and rows[0]["tier"] == 1 and rows[0]["result"] == "ok", rows
    finally:
        await ws.close()
    print("[check 2] Tier 1: runs silently, action row written, no approval, done: OK")


async def check_tier2(port: int, token: str) -> None:
    ws = await _connect_auth(port, token)
    try:
        await _call_tool(ws, "gate-t2", "t2_tool", {})
        act = await _recv_type(ws, "activity")
        assert act["tier"] == 2 and act["lane"] == 1 and act["narrate"] is False, act
        assert act["undoable"] is False, act
        frame = await _recv_type(ws, "done")
        assert frame["conversation_id"] == "gate-t2", frame
        assert ("t2_tool", {}) in EXECUTED
        assert _actions_for("t2_tool")[0]["tier"] == 2
    finally:
        await ws.close()
    print("[check 3] Tier 2: runs + activity(tier/lane) + done: OK")


async def check_tier3_approve_and_lock(port: int, token: str) -> None:
    ws = await _connect_auth(port, token)
    try:
        await _call_tool(ws, "gate-t3a", "t3_tool", {"to": "a@b.c"})
        req = await _recv_type(ws, "approval_request")
        assert req["tool"] == "t3_tool" and req["tier"] == 3, req
        assert req["args_redacted"] == {"to": "a@b.c"}, req
        assert req["summary"].startswith("I want"), req
        assert req.get("destructive") is False, req
        assert not any(t == "t3_tool" for t, _ in EXECUTED), "tool ran before approval"

        # C1 proof: while the approval is pending, a DIFFERENT conversation is
        # serviced -- the conversation-lock was released at suspend (and this
        # one's lock isn't held either; either way nothing blocks).
        await ws.send(
            json.dumps(_frame("user_msg", text="hello there", conversation_id="gate-other", source="ui"))
        )
        while True:
            frame = await _recv(ws)
            if frame["type"] == "done" and frame["conversation_id"] == "gate-other":
                break
            assert frame["type"] in ("token", "spend_update"), frame

        await _respond(ws, req["approval_id"], "approve")
        act = await _recv_type(ws, "activity")  # confirming activity for the approved run
        assert act["tier"] == 3, act
        frame = await _recv_type(ws, "done")
        assert frame["conversation_id"] == "gate-t3a", frame
        assert ("t3_tool", {"to": "a@b.c"}) in EXECUTED
        assert _actions_for("t3_tool")[0]["result"] == "ok"

        # Double response: second one gets a recoverable error.
        await _respond(ws, req["approval_id"], "approve")
        err = await _recv_type(ws, "error")
        assert err["code"] == "approval_already_handled" and err["recoverable"] is True, err
    finally:
        await ws.close()
    print("[check 4] Tier 3 approve: approval_request -> other conversation serviced while pending -> execute -> done; double response errors: OK")


async def check_tier3_deny(port: int, token: str) -> None:
    ws = await _connect_auth(port, token)
    try:
        before = len([e for e in EXECUTED if e[0] == "t3_tool"])
        await _call_tool(ws, "gate-t3d", "t3_tool", {"to": "x@y.z"})
        req = await _recv_type(ws, "approval_request")
        await _respond(ws, req["approval_id"], "deny")
        frame = await _recv_type(ws, "done")
        assert frame["conversation_id"] == "gate-t3d", frame
        assert len([e for e in EXECUTED if e[0] == "t3_tool"]) == before, "tool ran despite deny"
        denied = [a for a in _actions_for("t3_tool") if a["result"] == "denied"]
        assert denied, "denied intent not recorded"
    finally:
        await ws.close()
    print("[check 5] Tier 3 deny: tool not executed, denial recorded, turn completes: OK")


async def check_tier3_edit(port: int, token: str) -> None:
    ws = await _connect_auth(port, token)
    try:
        # Edit that stays Tier 3 -> a SECOND approval_request (an edit never
        # silently lowers the tier; what runs is what was last approved).
        await _call_tool(ws, "gate-edit", "argtool", {"risky": True, "v": 1})
        req1 = await _recv_type(ws, "approval_request")
        await _respond(ws, req1["approval_id"], "edit", {"risky": True, "v": 2})
        req2 = await _recv_type(ws, "approval_request")
        assert req2["approval_id"] != req1["approval_id"], req2
        assert req2["args_redacted"] == {"risky": True, "v": 2}, req2
        await _respond(ws, req2["approval_id"], "approve")
        await _recv_type(ws, "activity")
        frame = await _recv_type(ws, "done")
        assert frame["conversation_id"] == "gate-edit", frame
        assert ("argtool", {"risky": True, "v": 2}) in EXECUTED

        # Edit whose re-classification lands at Tier 2 -> executes immediately.
        await _call_tool(ws, "gate-edit2", "argtool", {"risky": True, "v": 3})
        req3 = await _recv_type(ws, "approval_request")
        await _respond(ws, req3["approval_id"], "edit", {"risky": False, "v": 4})
        act = await _recv_type(ws, "activity")
        assert act["tier"] == 2, act
        frame = await _recv_type(ws, "done")
        assert frame["conversation_id"] == "gate-edit2", frame
        assert ("argtool", {"risky": False, "v": 4}) in EXECUTED
    finally:
        await ws.close()
    print("[check 6] Tier 3 edit: edited args re-classified; still-Tier-3 edit -> second approval; Tier-2 edit -> executes: OK")


async def check_destructive(port: int, token: str) -> None:
    ws = await _connect_auth(port, token)
    try:
        await _call_tool(ws, "gate-boom", "t3_boom", {"count": 12})
        req = await _recv_type(ws, "approval_request")
        assert req["destructive"] is True, req
        # The card names its own thread: the UI routes "Stop this task" (an
        # interrupt, keyed by conversation) by this, so with several
        # conversations open it stops the one that asked, not the one on screen.
        assert req["conversation_id"] == "gate-boom", req
        await _respond(ws, req["approval_id"], "deny")
        await _recv_type(ws, "done")
        assert not any(t == "t3_boom" for t, _ in EXECUTED)
    finally:
        await ws.close()
    print("[check 7] destructive tool -> approval_request.destructive == true: OK")


async def check_interrupt_while_waiting(port: int, token: str) -> None:
    ws = await _connect_auth(port, token)
    try:
        cid = "gate-stop"
        await _call_tool(ws, cid, "t3_tool", {"to": "stop@me"})
        req = await _recv_type(ws, "approval_request")
        await ws.send(json.dumps(_frame("interrupt", conversation_id=cid)))
        frame = await _recv_type(ws, "done")  # implicit deny closes the turn
        assert frame["conversation_id"] == cid, frame
        assert not any(a == {"to": "stop@me"} for t, a in EXECUTED if t == "t3_tool"), "ran despite interrupt"
        # The pending approval is gone: a late response errors.
        await _respond(ws, req["approval_id"], "approve")
        err = await _recv_type(ws, "error")
        assert err["code"] == "approval_already_handled", err
        assert not any(a == {"to": "stop@me"} for t, a in EXECUTED if t == "t3_tool")
    finally:
        await ws.close()
    print("[check 8] interrupt while waiting_approval -> implicit deny, tool not run, approval gone: OK")


async def check_restart_durability(port: int, token: str) -> None:
    ws = await _connect_auth(port, token)
    cid = "gate-restart"
    try:
        await _call_tool(ws, cid, "t3_tool", {"to": "durable@x"})
        req = await _recv_type(ws, "approval_request")
    finally:
        await ws.close()

    # Simulated Brain restart: close + reopen the checkpointer (test_graph.py
    # idiom). The checkpoint must still hold the interrupted state.
    await graph.aclose()
    g = await graph._ensure_graph()
    snap = await g.aget_state({"configurable": {"thread_id": cid}})
    assert snap.next == ("gate",), snap.next
    assert snap.interrupts, "pending interrupt lost across checkpointer restart"
    assert snap.interrupts[0].value["approval_id"] == req["approval_id"], snap.interrupts
    # ponytail: the approval_id -> conversation_id map here survives only
    # because this is one process; rehydrating it from checkpoints after a
    # real restart is Step 9. The checkpoint itself is proven durable above.
    print("[check 9] Tier-3 suspension survives checkpointer close/reopen (resumable): OK")


async def main() -> None:
    check_classify_table()
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        await check_tier1(port, token)
        await check_tier2(port, token)
        await check_tier3_approve_and_lock(port, token)
        await check_tier3_deny(port, token)
        await check_tier3_edit(port, token)
        await check_destructive(port, token)
        await check_interrupt_while_waiting(port, token)
        await check_restart_durability(port, token)
    finally:
        server.close()
        await server.wait_closed()
        await graph.aclose()
    print("[brain.gate] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
