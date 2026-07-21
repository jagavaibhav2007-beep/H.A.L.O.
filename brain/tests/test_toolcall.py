"""Runnable self-check for real OpenRouter tool calling (Phase 2 Step 10).

No test framework -- plain asyncio + assert, matching test_gate.py. Run with:
    python brain/tests/test_toolcall.py

Two halves:
  * a PURE check of the streaming tool-call accumulator against canned,
    deliberately fragmented SSE chunks (the piece most likely to be subtly
    wrong -- a per-chunk json.loads yields {} and everything downstream still
    "works", so it is tested directly rather than only end to end);
  * the agentic loop driven through the real server over a real WS, offline
    via HALO_LLM_STUB: Tier-1 tool -> loop back to a final answer, Tier-3
    approval mid-loop -> suspend -> approve -> execute AND keep looping,
    multi-call round with the Tier 3 in slot 2, and the round cap.
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

_TMP = tempfile.mkdtemp(prefix="halo-test-toolcall-")
os.environ["HALO_LLM_STUB"] = "1"
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_CHECKPOINT_DB"] = str(Path(_TMP) / "checkpoints.db")
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")

import websockets

from brain import gate, graph, llm
from brain.server import start

EXECUTED: list[tuple[str, dict]] = []


def _make_tool(name):
    def fn(args):
        EXECUTED.append((name, dict(args)))
        return "done"

    return fn


gate.register(
    "tc_read", _make_tool("tc_read"), tier=1, summary="I read something quietly.",
    schema={"description": "read", "parameters": {"type": "object", "properties": {}}},
)
gate.register(
    "tc_send", _make_tool("tc_send"), tier=3, summary="I want to send an email.",
    schema={"description": "send", "parameters": {"type": "object", "properties": {}}},
)
# Registered WITHOUT a schema: internal-only, must never be advertised.
gate.register("tc_internal", _make_tool("tc_internal"), tier=1, summary="internal")


# ------------------------------------------------------- 1. pure accumulator


def check_accumulator() -> None:
    """Canned SSE chunks in the exact shape OpenRouter streams them: the first
    chunk for an index carries id+name and NO usable arguments, later chunks
    carry only argument fragments that must be concatenated."""
    chunks = [
        # first chunk: id + name, arguments opened but incomplete
        {"index": 0, "id": "call_abc", "type": "function",
         "function": {"name": "file_delete", "arguments": '{"pa'}},
        {"index": 0, "function": {"arguments": 'th": "/tm'}},
        {"index": 0, "function": {"arguments": 'p/a b.txt"'}},
        {"index": 0, "function": {"arguments": "}"}},
    ]
    # Parsing any single fragment fails -- that is the failure mode being guarded.
    try:
        json.loads(chunks[0]["function"]["arguments"])
        raise AssertionError("a lone fragment parsed; the canned chunks aren't fragmented")
    except json.JSONDecodeError:
        pass

    acc: dict[int, dict] = {}
    for c in chunks:
        llm.accumulate_tool_call_deltas(acc, [c])
    calls = llm.finish_tool_calls(acc)
    assert len(calls) == 1, calls
    assert calls[0]["id"] == "call_abc", calls
    assert calls[0]["function"]["name"] == "file_delete", calls
    assert calls[0]["args"] == {"path": "/tmp/a b.txt"}, calls
    assert calls[0]["error"] is None, calls
    assert calls[0]["function"]["arguments"] == '{"path": "/tmp/a b.txt"}', calls

    # Two calls interleaved across chunks, out of index order, name only on the
    # first fragment for each index.
    acc = {}
    llm.accumulate_tool_call_deltas(acc, [
        {"index": 0, "id": "c0", "function": {"name": "file_read", "arguments": '{"p'}},
        {"index": 1, "id": "c1", "function": {"name": "dir_list", "arguments": '{"p'}},
    ])
    llm.accumulate_tool_call_deltas(acc, [{"index": 1, "function": {"arguments": 'ath": "/b"}'}}])
    llm.accumulate_tool_call_deltas(acc, [{"index": 0, "function": {"arguments": 'ath": "/a"}'}}])
    calls = llm.finish_tool_calls(acc)
    assert [c["function"]["name"] for c in calls] == ["file_read", "dir_list"], calls
    assert [c["args"]["path"] for c in calls] == ["/a", "/b"], calls

    # Malformed arguments surface as an error instead of raising.
    acc = {}
    llm.accumulate_tool_call_deltas(acc, [
        {"index": 0, "id": "c0", "function": {"name": "file_read", "arguments": '{"path": '}},
    ])
    bad = llm.finish_tool_calls(acc)[0]
    assert bad["args"] == {} and bad["error"], bad

    # Arguments that parse but aren't an object are equally refused.
    acc = {}
    llm.accumulate_tool_call_deltas(acc, [
        {"index": 0, "id": "c0", "function": {"name": "x", "arguments": "[1,2]"}},
    ])
    assert llm.finish_tool_calls(acc)[0]["error"], "a JSON array was accepted as args"

    # A no-argument call still yields valid empty args.
    acc = {}
    llm.accumulate_tool_call_deltas(acc, [{"index": 0, "id": "c0", "function": {"name": "x"}}])
    assert llm.finish_tool_calls(acc)[0]["args"] == {}, "empty arguments didn't default to {}"

    # Empty/absent delta.tool_calls is a no-op (every text-only chunk hits this).
    acc = {}
    llm.accumulate_tool_call_deltas(acc, None)
    llm.accumulate_tool_call_deltas(acc, [])
    assert llm.finish_tool_calls(acc) == []
    print("[check 1] streaming accumulator: fragments concatenate per index, parse once at end, "
          "malformed args reported not raised: OK")


# ---------------------------------------------------------- 2. tool_specs


def check_tool_specs() -> None:
    specs = gate.tool_specs()
    names = [s["function"]["name"] for s in specs]
    assert "tc_read" in names and "tc_send" in names, names
    assert "tc_internal" not in names, "schema-less tool was advertised to the model"
    assert len(names) == len(set(names)), names
    # The nine real file tools all carry schemas.
    for real in ("file_read", "dir_list", "file_search", "file_create", "file_edit",
                 "file_move", "file_delete", "dir_organize", "run_readonly_cmd"):
        assert real in names, f"{real} not advertised"
    for spec in specs:
        assert spec["type"] == "function", spec
        fn = spec["function"]
        assert set(fn) == {"name", "description", "parameters"}, fn
        assert isinstance(fn["description"], str) and fn["description"], fn
        assert fn["parameters"]["type"] == "object", fn
        assert isinstance(fn["parameters"]["properties"], dict), fn
        for req in fn["parameters"].get("required", []):
            assert req in fn["parameters"]["properties"], (fn["name"], req)
    # Tier must not leak into the advertisement -- the gate decides, not the model.
    assert "tier" not in json.dumps(specs), "tier leaked into tool specs"
    print(f"[check 2] tool_specs: {len(specs)} tools advertised, schema-less excluded, shape valid: OK")


# ------------------------------------------------------------- WS plumbing


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
    assert json.loads(await asyncio.wait_for(ws.recv(), timeout=1))["type"] == "hello_ack"
    while json.loads(await asyncio.wait_for(ws.recv(), timeout=5))["type"] != "spend_update":
        pass
    return ws


async def _recv(ws, timeout: float = 15) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def _recv_type(ws, msg_type: str, timeout: float = 15) -> dict:
    while True:
        frame = await _recv(ws, timeout)
        if frame["type"] == msg_type:
            return frame


async def _send(ws, cid: str, text: str) -> None:
    await ws.send(json.dumps(_frame("user_msg", text=text, conversation_id=cid, source="ui")))


async def _drain_to_done(ws, cid: str, timeout: float = 20) -> list[dict]:
    """Every frame up to and including `done` for cid."""
    frames: list[dict] = []
    while True:
        frame = await _recv(ws, timeout)
        frames.append(frame)
        if frame["type"] == "done" and frame.get("conversation_id") == cid:
            return frames


def _text(frames: list[dict], cid: str) -> str:
    return "".join(f["text"] for f in frames if f["type"] == "token" and f["conversation_id"] == cid)


async def _history(cid: str) -> list[dict]:
    g = await graph._ensure_graph()
    return (await g.aget_state({"configurable": {"thread_id": cid}})).values.get("messages", [])


# ------------------------------------------------- 3. Tier-1 loop round trip


async def check_tier1_loop(port: int, token: str) -> None:
    """The sentinel is mid-message, so route does NOT intercept: respond runs,
    the stub emits a real-shaped tool call, gate executes it, and the loop
    comes back to respond for a final text answer."""
    ws = await _connect_auth(port, token)
    try:
        cid = "tc-t1"
        await _send(ws, cid, 'please CALL_TOOL tc_read {"path": "x"}')
        frames = await _drain_to_done(ws, cid)
        assert ("tc_read", {"path": "x"}) in EXECUTED, EXECUTED
        assert _text(frames, cid), "loop ended without a final text answer"
        msgs = await _history(cid)
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant", "tool", "assistant"], roles
        assert msgs[1].get("tool_calls"), "assistant message lost its tool_calls"
        assert msgs[2]["tool_call_id"] == msgs[1]["tool_calls"][0]["id"], msgs[1:3]
        assert msgs[-1]["content"] and not msgs[-1].get("tool_calls"), msgs[-1]
    finally:
        await ws.close()
    print("[check 3] Tier-1 tool call: respond -> gate -> respond -> final answer; "
          "assistant keeps tool_calls, result comes back as a matching tool message: OK")


# ------------------------------------------- 4. Tier-3 approval mid-loop


async def check_tier3_mid_loop(port: int, token: str) -> None:
    """The interrupt must suspend from INSIDE the loop and, after approval,
    execute AND continue looping to a final answer -- not just end the turn."""
    ws = await _connect_auth(port, token)
    try:
        cid = "tc-t3"
        before = len(EXECUTED)
        await _send(ws, cid, 'go CALL_TOOL tc_send {"to": "a@b.c"}')
        req = await _recv_type(ws, "approval_request")
        assert req["tool"] == "tc_send" and req["tier"] == 3, req
        assert req["conversation_id"] == cid, req
        assert len(EXECUTED) == before, "tool ran before approval"

        await ws.send(json.dumps(_frame("approval_response", reply_to=req["approval_id"], decision="approve")))
        act = await _recv_type(ws, "activity")
        assert act["tier"] == 3, act
        frames = await _drain_to_done(ws, cid)
        assert ("tc_send", {"to": "a@b.c"}) in EXECUTED, EXECUTED
        # The loop CONTINUED: a final text answer arrived after the approval.
        assert _text(frames, cid), "resumed turn ended without a final answer"
        roles = [m["role"] for m in await _history(cid)]
        assert roles == ["user", "assistant", "tool", "assistant"], roles
        # The resumed half streams a real (billable) answer, so it owes the same
        # tail run_turn does -- before the loop landed a resume was gate->END
        # with no LLM call, so resume_turn had no spend accounting at all.
        # spend_update follows `done`, so it's read after the drain.
        spend = await _recv_type(ws, "spend_update", timeout=10)
        assert isinstance(spend["session_usd"], float), spend
    finally:
        await ws.close()
    print("[check 4] Tier-3 mid-loop: suspends with approval_request -> approve -> executes -> "
          "loop continues to a final answer: OK")


# --------------------------------- 5. multi-call round, Tier 3 in slot 2


async def check_multi_call_round(port: int, token: str) -> None:
    """One round, two calls, the Tier-3 second. Proves the queue drains one
    call per gate visit: the suspension partway through must not re-run the
    Tier-1 call that already succeeded, and each call gets its own tool message
    keyed to the right tool_call_id."""
    ws = await _connect_auth(port, token)
    try:
        cid = "tc-multi"
        reads = len([e for e in EXECUTED if e[0] == "tc_read"])
        await _send(ws, cid, 'both CALL_TOOL tc_read {"n": 1} CALL_TOOL tc_send {"to": "m@x"}')
        req = await _recv_type(ws, "approval_request")
        assert req["tool"] == "tc_send", req
        # First call already ran, exactly once, before the suspension.
        assert len([e for e in EXECUTED if e[0] == "tc_read"]) == reads + 1, EXECUTED

        await ws.send(json.dumps(_frame("approval_response", reply_to=req["approval_id"], decision="approve")))
        frames = await _drain_to_done(ws, cid)
        assert len([e for e in EXECUTED if e[0] == "tc_read"]) == reads + 1, "Tier-1 call re-ran on resume"
        assert ("tc_send", {"to": "m@x"}) in EXECUTED, EXECUTED
        assert _text(frames, cid), "no final answer after the multi-call round"

        msgs = await _history(cid)
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant", "tool", "tool", "assistant"], roles
        want = [c["id"] for c in msgs[1]["tool_calls"]]
        assert len(want) == 2, msgs[1]
        assert [m["tool_call_id"] for m in msgs[2:4]] == want, (msgs[2:4], want)
    finally:
        await ws.close()
    print("[check 5] multi-call round with a Tier-3 in slot 2: queue drains one per visit, "
          "no re-execution on resume, one tool message per call id: OK")


# --------------------------------------------------------- 6. round cap


async def check_round_cap(port: int, token: str) -> None:
    """CALL_TOOL_LOOP makes the stub ask for the tool every single round. The
    cap must terminate the turn with a real answer, not a truncated one."""
    ws = await _connect_auth(port, token)
    try:
        cid = "tc-cap"
        before = len([e for e in EXECUTED if e[0] == "tc_read"])
        await _send(ws, cid, 'spin CALL_TOOL_LOOP tc_read {"n": 9}')
        frames = await _drain_to_done(ws, cid, timeout=40)
        assert not [f for f in frames if f["type"] == "error"], frames[-3:]
        ran = len([e for e in EXECUTED if e[0] == "tc_read"]) - before
        assert ran == graph._MAX_TOOL_ROUNDS, (ran, graph._MAX_TOOL_ROUNDS)
        # The user gets a real answer from the final tools-free call.
        assert _text(frames, cid), "cap ended the turn without an answer"
        msgs = await _history(cid)
        assert msgs[-1]["role"] == "assistant" and not msgs[-1].get("tool_calls"), msgs[-1]
        assert len([m for m in msgs if m["role"] == "tool"]) == graph._MAX_TOOL_ROUNDS, msgs
    finally:
        await ws.close()
    print(f"[check 6] round cap: stops after {graph._MAX_TOOL_ROUNDS} rounds and still answers "
          "(final call made without tools), no error frame: OK")


async def main() -> None:
    check_accumulator()
    check_tool_specs()
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        await check_tier1_loop(port, token)
        await check_tier3_mid_loop(port, token)
        await check_multi_call_round(port, token)
        await check_round_cap(port, token)
    finally:
        server.close()
        await server.wait_closed()
        await graph.aclose()
    print("[brain tool-calling] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
