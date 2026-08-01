"""Phase 2 end-to-end protocol check for the real (non-mock) Brain.

This plain asyncio + assert script drives a real authenticated WebSocket
client against ``brain.server.start()``'s default (real, non-mock) dispatch —
the same graph/gate/store/memory stack Steps 1-9 built, with the offline
``HALO_LLM_STUB``/``HALO_EXTRACT_STUB`` seams so the gate never makes a paid
network call. Mirrors ``shared/phase1_check.py``'s structure and output
format, but against the real Brain instead of the scripted mock.

Maps to the Phase 2 exit criteria in phase-2-plan.md (lines 5-13):
  1. real chat turn (streamed token(s) + done)
  2. memory: durable write, survives a Brain restart, provenance-enforced
  3. tier behaviors: T1 silent, T2 + activity, T3 interrupt()/approve/deny/
     edit (edits are always re-classified, never assumed to keep or lower
     the tier), implicit-deny-on-interrupt
  4/5. Lane-1 file op + real undo round-trip on disk
  6-adjacent. reconnect snapshot converges, no duplicate entity ids
  7. no key, no stub -> a clean `error` naming Settings, never a crash
     (run in an isolated subprocess -- see check_key_missing_honesty)
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))

import atexit
import shutil

_TMP = tempfile.mkdtemp(prefix="halo-phase2-check-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)  # don't leak on every run
os.environ["HALO_LLM_STUB"] = "1"
os.environ["HALO_EXTRACT_STUB"] = "1"
# v2 memory: consolidation is idle-debounced. Shrink the idle window to ~50ms
# so the "loop until belief_state" checks see a consolidation promptly instead
# of waiting out the 1800s default.
os.environ["HALO_MEMORY_IDLE_S"] = "0.05"
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_CHECKPOINT_DB"] = str(Path(_TMP) / "checkpoints.db")
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")

import websockets  # noqa: E402

from brain.ipc.contract import parse_ipc_message  # noqa: E402
from brain import gate, graph, store  # noqa: E402
from brain.server import start  # noqa: E402

FILES_DIR = Path(tempfile.mkdtemp(prefix="halo-phase2-files-")).resolve()
atexit.register(shutil.rmtree, FILES_DIR, ignore_errors=True)  # don't leak on every run
store.connect()
store.set_setting("project_roots", [str(FILES_DIR)])

SNAPSHOT_TYPES = {
    "settings_state",
    "project_roots_state",
    "capabilities_state",
    "task_state",
    "approval_request",
    "activity",
    "belief_state",
    "spend_update",
    "snapshot_complete",
}

EXECUTED: list[tuple[str, dict]] = []


def _tool(name: str):
    def fn(args: dict) -> str:
        EXECUTED.append((name, dict(args)))
        return "done"

    return fn


gate.register("p2_t1", _tool("p2_t1"), tier=1, summary="I read something quietly.")
gate.register("p2_t2", _tool("p2_t2"), tier=2, summary="I moved a thing quietly.")
gate.register("p2_t3", _tool("p2_t3"), tier=3, summary="I want to send an email.")
# Arg-predicate rule (D4): a tool call is always re-classified from the args
# that will actually run, never assumed to inherit the tier it first showed
# at -- this is what makes "an edit can raise the tier" true in general.
gate.register(
    "p2_argtool", _tool("p2_argtool"),
    tier=lambda args: 3 if args.get("risky") else 2,
    summary="I want to run argtool.",
)


def _frame(msg_type: str, **payload: object) -> dict:
    return {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


async def _recv(ws, timeout: float = 10.0) -> dict:
    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
    parse_ipc_message(frame)
    return frame


async def _recv_type(ws, msg_type: str, timeout: float = 10.0) -> dict:
    """Next frame of msg_type, skipping token/spend_update noise (test_gate.py idiom)."""
    while True:
        frame = await _recv(ws, timeout)
        if frame["type"] == msg_type:
            return frame
        assert frame["type"] in ("spend_update", "token"), f"unexpected frame waiting for {msg_type}: {frame}"


async def _drain_snapshot(ws) -> list[dict]:
    frames: list[dict] = []
    while True:
        frame = await _recv(ws)
        assert frame["type"] in SNAPSHOT_TYPES, frame
        frames.append(frame)
        if frame["type"] == "snapshot_complete":
            return frames


async def _connect_ui(port: int, token: str, drain: bool = True):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(json.dumps(_frame("hello", token=token, role="ui")))
    ack = await _recv(ws)
    assert ack["type"] == "hello_ack", ack
    if drain:
        await _drain_snapshot(ws)
    return ws


async def _call_tool(ws, cid: str, tool: str, args: dict) -> None:
    text = f"CALL_TOOL {tool} {json.dumps(args)}"
    await ws.send(json.dumps(_frame("user_msg", text=text, conversation_id=cid, source="ui")))


async def _respond(ws, approval_id: str, decision: str, edited_args: dict | None = None) -> None:
    payload = {"reply_to": approval_id, "decision": decision}
    if edited_args is not None:
        payload["edited_args"] = edited_args
    await ws.send(json.dumps(_frame("approval_response", **payload)))


# --------------------------------------------------------------- criterion 1


async def check_real_chat_turn(port: int, token: str) -> None:
    ws = await _connect_ui(port, token, drain=False)
    try:
        # C4/D1 regression: token accounting rides spend_update through the REAL
        # Brain. The snapshot's terminal spend_update carries session_tokens (no
        # last_turn_tokens -- a fresh connect has no "last turn"). Values are 0
        # under the offline stub; the ACTUAL cumulative-input-token ceiling only
        # trips with a real key (a manual-test item), but the wiring the ceiling
        # and the Settings readout depend on is locked here so it can't silently
        # regress to the pre-Track-C state that undercounted spend ~4x.
        snap = await _drain_snapshot(ws)
        assert snap[-1]["type"] == "snapshot_complete", snap[-1]  # explicit end-of-snapshot marker
        spend = next(f for f in snap if f["type"] == "spend_update")
        assert isinstance(spend.get("session_tokens"), int), spend
        assert "last_turn_tokens" not in spend, "snapshot has no 'last turn' to report"

        await ws.send(json.dumps(_frame("user_msg", text="ping", conversation_id="p2-chat", source="ui")))
        tokens: list[str] = []
        while True:
            frame = await _recv(ws)
            if frame["type"] == "token":
                assert frame["conversation_id"] == "p2-chat", frame
                tokens.append(frame["text"])
            elif frame["type"] == "done":
                assert frame["conversation_id"] == "p2-chat", frame
                break
            else:
                assert frame["type"] == "spend_update", frame
        assert tokens and "ping" in "".join(tokens), tokens
        # The per-turn spend_update lands right after done and carries the just-
        # finished turn's last_turn_tokens (present even at 0 under the stub).
        after = await _recv(ws)
        assert after["type"] == "spend_update" and isinstance(after.get("last_turn_tokens"), int), after
    finally:
        await ws.close()
    print("PASS -- real chat turn: streams token(s) then done; spend_update carries session/last-turn tokens (criterion 1, C4)")


# --------------------------------------------------------------- criterion 3


async def check_tier_behaviors(port: int, token: str) -> None:
    ws = await _connect_ui(port, token)
    try:
        # Tier 1: runs without approval and emits a quiet, non-narrated activity
        # so the UI remains truthful about backend work.
        await _call_tool(ws, "p2-t1", "p2_t1", {"path": "x"})
        act = await _recv_type(ws, "activity")
        assert act["tier"] == 1 and act["lane"] == 1, act
        assert act["narrate"] is False and act["undoable"] is False, act
        frame = await _recv_type(ws, "done")
        assert frame["conversation_id"] == "p2-t1", frame
        assert ("p2_t1", {"path": "x"}) in EXECUTED
        rows = [a for a in store.recent_actions(200) if a["tool"] == "p2_t1"]
        assert rows and rows[0]["tier"] == 1 and rows[0]["result"] == "ok", rows
        print("PASS -- Tier 1: runs without approval, writes an action row, and emits quiet activity (criterion 3)")

        # Tier 2: runs + surfaces an activity.
        await _call_tool(ws, "p2-t2", "p2_t2", {})
        act = await _recv_type(ws, "activity")
        assert act["tier"] == 2, act
        done = await _recv_type(ws, "done")
        assert done["conversation_id"] == "p2-t2", done
        assert ("p2_t2", {}) in EXECUTED
        print("PASS -- Tier 2: runs + emits activity + done (criterion 3)")

        # Tier 3 approve: suspends via interrupt() -> approval_request -> executes.
        await _call_tool(ws, "p2-t3a", "p2_t3", {"to": "a@b.c"})
        req = await _recv_type(ws, "approval_request")
        assert req["tool"] == "p2_t3" and req["tier"] == 3, req
        assert not any(t == "p2_t3" for t, _ in EXECUTED), "tool ran before approval"
        await _respond(ws, req["approval_id"], "approve")
        await _recv_type(ws, "activity")
        done = await _recv_type(ws, "done")
        assert done["conversation_id"] == "p2-t3a", done
        assert ("p2_t3", {"to": "a@b.c"}) in EXECUTED
        print("PASS -- Tier 3 approve: approval_request -> execute -> done (criterion 3)")

        # Tier 3 deny: tool never runs, turn still closes honestly.
        before = len([e for e in EXECUTED if e[0] == "p2_t3"])
        await _call_tool(ws, "p2-t3d", "p2_t3", {"to": "x@y.z"})
        req = await _recv_type(ws, "approval_request")
        await _respond(ws, req["approval_id"], "deny")
        done = await _recv_type(ws, "done")
        assert done["conversation_id"] == "p2-t3d", done
        assert len([e for e in EXECUTED if e[0] == "p2_t3"]) == before, "tool ran despite deny"
        print("PASS -- Tier 3 deny: tool not executed, turn still closes honestly (criterion 3)")

        # Tier 3 edit: edited args take effect, and are ALWAYS re-classified --
        # a still-Tier-3 edit gets a fresh approval (never assumed pre-approved
        # under the new args); a Tier-2-reclassified edit executes immediately.
        # This is the property that makes "an edit can raise the tier, never
        # silently lower it" true: nothing here trusts the original grant.
        await _call_tool(ws, "p2-edit-stays3", "p2_argtool", {"risky": True, "v": 1})
        req1 = await _recv_type(ws, "approval_request")
        await _respond(ws, req1["approval_id"], "edit", {"risky": True, "v": 2})
        req2 = await _recv_type(ws, "approval_request")
        assert req2["approval_id"] != req1["approval_id"], req2
        assert req2["args_redacted"] == {"risky": True, "v": 2}, req2
        await _respond(ws, req2["approval_id"], "approve")
        await _recv_type(ws, "activity")
        done = await _recv_type(ws, "done")
        assert done["conversation_id"] == "p2-edit-stays3", done
        assert ("p2_argtool", {"risky": True, "v": 2}) in EXECUTED

        await _call_tool(ws, "p2-edit-lowers", "p2_argtool", {"risky": True, "v": 3})
        req3 = await _recv_type(ws, "approval_request")
        await _respond(ws, req3["approval_id"], "edit", {"risky": False, "v": 4})
        act = await _recv_type(ws, "activity")
        assert act["tier"] == 2, act
        done = await _recv_type(ws, "done")
        assert done["conversation_id"] == "p2-edit-lowers", done
        assert ("p2_argtool", {"risky": False, "v": 4}) in EXECUTED
        print("PASS -- Tier 3 edit: edited args take effect and are always re-classified from scratch (criterion 3)")

        # Interrupt while waiting_approval -> implicit deny (contract rule).
        await _call_tool(ws, "p2-stop", "p2_t3", {"to": "stop@me"})
        req = await _recv_type(ws, "approval_request")
        await ws.send(json.dumps(_frame("interrupt", conversation_id="p2-stop")))
        done = await _recv_type(ws, "done")
        assert done["conversation_id"] == "p2-stop", done
        assert not any(a == {"to": "stop@me"} for t, a in EXECUTED if t == "p2_t3"), "ran despite interrupt"
        await _respond(ws, req["approval_id"], "approve")  # late response: the approval is already gone
        err = await _recv_type(ws, "error")
        assert err["code"] == "approval_already_handled", err
        print("PASS -- interrupt while waiting_approval -> implicit deny, tool never ran (criterion 3, contract rule)")
    finally:
        await ws.close()


# --------------------------------------------------------- criteria 4, 5


async def check_file_undo_roundtrip(ws) -> None:
    p = FILES_DIR / "note.txt"
    await _call_tool(ws, "p2-file", "file_create", {"path": str(p), "content": "hello halo"})
    act = await _recv_type(ws, "activity")
    done = await _recv_type(ws, "done")
    assert done["conversation_id"] == "p2-file", done
    assert p.read_text(encoding="utf-8") == "hello halo", "file_create didn't write to disk"
    assert act["undoable"] is True and act.get("undo_token"), act

    await ws.send(json.dumps(_frame("undo", undo_token=act["undo_token"])))
    rev = await _recv_type(ws, "activity")
    assert rev["task_id"] == act["task_id"], (rev, act)
    assert not p.exists(), "undo didn't reverse the create on disk"
    print("PASS -- Lane-1 file op + undo round-trip: activity carries undo_token, undo reverses it on disk (criteria 4, 5)")


# ------------------------------------------------- doc_digest (Layer 2, 13-document-ingestion)


async def check_doc_digest(port: int, token: str) -> None:
    """Digest two files through the real gate and durable TaskRuntime."""
    d1 = FILES_DIR / "digest-a.md"
    d2 = FILES_DIR / "digest-b.md"
    d1.write_text("# Plan A\n\nShip the ingestion layer. Budget: $500.", encoding="utf-8")
    d2.write_text("# Plan B\n\nDefer to Phase 3. Headcount: 2.", encoding="utf-8")
    ws = await _connect_ui(port, token)
    task_id = None
    terminal = None
    done_count = 0
    activity = None
    try:
        await _call_tool(ws, "p2-digest", "doc_digest", {"paths": [str(d1), str(d2)]})
        while terminal is None or done_count < 2:
            frame = await _recv(ws, timeout=30)
            if frame["type"] == "task_state":
                task_id = task_id or frame["task_id"]
                assert frame["task_id"] == task_id, frame
                if frame["state"] in ("done", "failed"):
                    terminal = frame
            elif frame["type"] == "activity" and frame.get("task_id") == task_id:
                activity = frame
            elif frame["type"] == "done":
                assert frame["conversation_id"] == "p2-digest", frame
                done_count += 1
            else:
                assert frame["type"] in ("token", "spend_update", "task_log"), frame
    finally:
        await ws.close()

    assert terminal and terminal["state"] == "done", terminal
    assert activity and activity["tier"] == 1 and activity["narrate"] is False, activity
    task = store.get_task(task_id)
    assert task and task["state"] == "done" and task["result_json"], task
    assert "digest-a.md" in task["result_json"] and "digest-b.md" in task["result_json"], task

    g = await graph._ensure_graph()
    snap = await g.aget_state({"configurable": {"thread_id": "p2-digest"}})
    results = [
        m["content"] for m in snap.values.get("messages", [])
        if "Untrusted task result" in (m.get("content") or "")
    ]
    assert results, "no doc_digest tool result in the conversation"
    assert "digest-a.md" in results[0] and "digest-b.md" in results[0], results[0]
    tokens_est = len(results[0]) // 4
    assert tokens_est < 2500, f"conversation-visible digest too big: ~{tokens_est} tokens"
    # Under HALO_LLM_STUB the stub reply is prose, so every per-doc digest takes
    # the honest-degrade path -- and a degraded digest is deliberately NEVER
    # cached (it would pin the degraded answer under that sha forever, with no
    # invalidation short of bumping DIGEST_VERSION). So the E2E-correct
    # assertion here is zero rows: the real graph never manufactures a
    # non-degraded digest offline. The happy-path cache-write is exercised in
    # test_docs.py check 2, which stubs valid JSON in.
    rows = store.connect().execute("SELECT COUNT(*) AS n FROM digest_cache").fetchone()["n"]
    assert rows == 0, f"degraded digests must not be cached, found {rows} rows"
    print("PASS -- doc_digest: turn completes, conversation-visible result stays "
          f"~{tokens_est} tokens (<2500), degraded digests not cached (Layer 2, systemdesign/13)")


# --------------------------------------------------------------- criterion 2


async def check_memory_and_restart(server, token: str, port: int):
    """Write a durable belief, prove provenance rejection, then kill+respawn
    the Brain and confirm the belief comes back in the reconnect snapshot.
    Returns the (server, token, port) of the RESPAWNED Brain for later checks."""
    ws = await _connect_ui(port, token)
    try:
        await ws.send(json.dumps(_frame(
            "user_msg", text="remember: my shell is bash", conversation_id="p2-mem", source="ui"
        )))
        belief = None
        while belief is None:
            f = await _recv(ws, timeout=30)
            if f["type"] == "belief_state":
                belief = f
        assert belief["text"] == "my shell is bash" and belief["provenance"] == "user", belief

        # Provenance rejection (rule 6): an inferred contradiction cannot
        # supersede the user-stated fact above.
        await ws.send(json.dumps(_frame(
            "user_msg", text="remember(inferred): my shell is zsh", conversation_id="p2-mem", source="ui"
        )))
        while True:
            f = await _recv(ws, timeout=30)
            if f["type"] == "done":
                break
        row = next(b for b in store.list_beliefs() if b["text"] == "my shell is bash")
        assert row["status"] == "active" and row["superseded_by"] is None, row
        print("PASS -- provenance rejection: an inferred contradiction cannot supersede a user-stated belief (exit criterion 2)")
    finally:
        await ws.close()

    # Kill the Brain (server.close() + graph.aclose(), same idiom test_gate.py
    # and test_snapshot.py use to simulate a restart) and respawn it on a new
    # port against the SAME on-disk store/checkpoint files.
    server.close()
    await server.wait_closed()
    await graph.aclose()

    server2, token2 = await start()
    port2 = server2.sockets[0].getsockname()[1]
    ws2 = await _connect_ui(port2, token2, drain=False)
    beliefs_seen: list[dict] = []
    try:
        snap = await _drain_snapshot(ws2)
        beliefs_seen = [f for f in snap if f["type"] == "belief_state"]
    finally:
        await ws2.close()
    assert any(b["text"] == "my shell is bash" for b in beliefs_seen), beliefs_seen
    print("PASS -- memory survives a Brain restart: belief reappears in the reconnect snapshot (exit criterion 2)")
    return server2, token2, port2


# --------------------------------------------------------- criterion 6-adj.


async def check_snapshot_idempotence(port: int, token: str) -> None:
    first = await _connect_ui(port, token, drain=False)
    try:
        first_snap = await _drain_snapshot(first)
    finally:
        await first.close()
    second = await _connect_ui(port, token, drain=False)
    try:
        second_snap = await _drain_snapshot(second)
    finally:
        await second.close()

    def keys(frames: list[dict]) -> list[tuple]:
        return [
            (f["type"], f.get("task_id") or f.get("belief_id") or f.get("approval_id") or f.get("key"))
            for f in frames
            if f["type"] in ("task_state", "belief_state", "approval_request", "settings_state")
        ]

    k1, k2 = keys(first_snap), keys(second_snap)
    assert k1 == k2, (k1, k2)
    assert len(k1) == len(set(k1)), k1
    print("PASS -- reconnect snapshot converges, no duplicate entity ids (exit criterion 6-adjacent)")


# --------------------------------------------------------------- criterion 7


_KEY_MISSING_SCRIPT = '''
import asyncio, json, os, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"ROOT_PLACEHOLDER")
sys.path.insert(0, str(ROOT / "brain"))

tmp = tempfile.mkdtemp(prefix="halo-phase2-keymissing-")
os.environ["LOCALAPPDATA"] = tmp
os.environ["HALO_CHECKPOINT_DB"] = str(Path(tmp) / "checkpoints.db")
os.environ["HALO_KEYRING_DIR"] = str(Path(tmp) / "keys")  # empty dir -- key never set
os.environ.pop("HALO_LLM_STUB", None)  # the real (non-stub) path: no key, no network call reached

import websockets
from brain.server import start
from brain import graph


def frame(t, **p):
    return {"type": t, "id": str(uuid.uuid4()), "ts": datetime.now(timezone.utc).isoformat(), **p}


async def main():
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        ws = await websockets.connect(f"ws://127.0.0.1:{port}")
        try:
            await ws.send(json.dumps(frame("hello", token=token)))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert ack["type"] == "hello_ack", ack
            while json.loads(await asyncio.wait_for(ws.recv(), timeout=5))["type"] != "snapshot_complete":
                pass
            await ws.send(json.dumps(frame("user_msg", text="hello", conversation_id="km", source="ui")))
            err = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert err["type"] == "error", err
            assert err["code"] == "no_api_key", err
            assert "Settings" in err["message"], err
        finally:
            await ws.close()
    finally:
        server.close()
        await server.wait_closed()
        await graph.aclose()
    print("KEY_MISSING_OK")


asyncio.run(main())
'''


async def check_approval_survives_restart(server, token: str, port: int):
    """Exit criterion 6: kill the Brain while a Tier-3 approval is open, and
    on restart the work must reappear from its checkpoint AND still resume. A
    card you can see but can't approve is a task stuck forever."""
    ws = await _connect_ui(port, token)
    try:
        await _call_tool(ws, "p2-restart", "p2_t3", {"to": "survivor@halo"})
        req = await _recv_type(ws, "approval_request")
    finally:
        await ws.close()
    before = len([e for e in EXECUTED if e[0] == "p2_t3"])

    # Kill: the in-memory approval routing map dies with the process, so clear
    # it too -- otherwise this would pass off the stale map and prove nothing.
    server.close()
    await server.wait_closed()
    await graph.aclose()
    gate._pending.clear()
    gate._by_conversation.clear()

    server2, token2 = await start()
    port2 = server2.sockets[0].getsockname()[1]
    ws2 = await _connect_ui(port2, token2, drain=False)
    try:
        snap = await _drain_snapshot(ws2)
        reqs = [f for f in snap if f["type"] == "approval_request"]
        assert any(r["approval_id"] == req["approval_id"] for r in reqs), reqs
        await _respond(ws2, req["approval_id"], "approve")
        await _recv_type(ws2, "activity")
        await _recv_type(ws2, "done")
    finally:
        await ws2.close()
    assert len([e for e in EXECUTED if e[0] == "p2_t3"]) == before + 1, "resumed approval didn't execute"
    print("PASS -- approval open at kill time returns in the reconnect snapshot and still resumes (exit criterion 6)")
    return server2, token2, port2


async def check_key_missing_honesty() -> None:
    """Isolated sub-check (own subprocess/interpreter): with no HALO_LLM_STUB
    and no key ever stored, a chat turn must return a clean `error` naming
    Settings, never a crash. Run out-of-process so it can't be contaminated by
    HALO_LLM_STUB/keys already set up for the rest of this file's checks."""
    script = _KEY_MISSING_SCRIPT.replace("ROOT_PLACEHOLDER", str(ROOT))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    assert proc.returncode == 0, f"key-missing sub-check failed:\n{out.decode(errors='replace')}\n{err.decode(errors='replace')}"
    assert b"KEY_MISSING_OK" in out, out.decode(errors="replace")
    print("PASS -- key-missing honesty (isolated subprocess, no stub/no key): clean error naming Settings, no crash (exit criterion 7)")


async def main() -> None:
    print("=== Halo Phase 2 real-Brain E2E check ===")
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        await check_real_chat_turn(port, token)
        await check_tier_behaviors(port, token)
        ws = await _connect_ui(port, token)
        try:
            await check_file_undo_roundtrip(ws)
        finally:
            await ws.close()
        await check_doc_digest(port, token)
        server, token, port = await check_memory_and_restart(server, token, port)
        server, token, port = await check_approval_survives_restart(server, token, port)
        await check_snapshot_idempotence(port, token)
    finally:
        server.close()
        await server.wait_closed()
        await graph.aclose()
        store.close()

    await check_key_missing_honesty()

    print("=== Halo Phase 2 real-Brain E2E: ALL CHECKS PASS ===")


if __name__ == "__main__":
    asyncio.run(main())
