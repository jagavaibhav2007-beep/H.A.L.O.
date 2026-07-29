"""Runnable self-check for Phase 2 Step 6 (activity log & undo, D7).

No test framework -- plain asyncio + assert, matching test_gate.py. Run with:
    python brain/tests/test_undo.py

Drives the real server in-process as a fake UI client with the LLM stub and
all data dirs in a temp dir; the fake tools do REAL filesystem work in a
tempfile.TemporaryDirectory so undo is proven on disk, not on flags.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-undo-")
os.environ["HALO_LLM_STUB"] = "1"
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_CHECKPOINT_DB"] = str(Path(_TMP) / "checkpoints.db")
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")

import websockets

from brain import gate, graph, store
from brain.server import start

FILES = Path(tempfile.mkdtemp(prefix="halo-undo-files-"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _move_inverse(args, result):
    dst = Path(args["dst"])
    return {
        "tool": "file_move",
        "args": {"src": args["dst"], "dst": args["src"]},
        "precondition": {"path": args["dst"], "sha256": _sha(dst)},
    }


def _create_inverse(args, result):
    p = Path(args["path"])
    return {
        "tool": "file_delete",
        "args": {"path": args["path"]},
        "precondition": {"path": args["path"], "sha256": _sha(p)},
    }


gate.register(
    "file_move", lambda args: shutil.move(args["src"], args["dst"]),
    tier=2, summary="I moved a file.", inverse=_move_inverse,
)
gate.register(
    "file_create", lambda args: Path(args["path"]).write_text(args["content"], encoding="utf-8"),
    tier=2, summary="I created a file.", inverse=_create_inverse,
)
gate.register("file_delete", lambda args: Path(args["path"]).unlink(), tier=2, summary="I deleted a file.")
gate.register("shout", lambda args: None, tier=2, summary="I shouted into the void.")  # no inverse


def _frame(msg_type: str, **payload) -> dict:
    return {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


async def _recv(ws, timeout: float = 10) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def _recv_type(ws, msg_type: str, timeout: float = 10) -> dict:
    while True:
        frame = await _recv(ws, timeout)
        if frame["type"] == msg_type:
            return frame
        assert frame["type"] in ("spend_update", "snapshot_complete", "token"), f"unexpected frame waiting for {msg_type}: {frame}"


async def _connect_auth(port: int, token: str, drain_backlog: bool = True):
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(json.dumps(_frame("hello", token=token)))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
    assert ack["type"] == "hello_ack", ack
    settings = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
    assert settings["type"] == "settings_state", settings
    if drain_backlog:
        # Step 9: drain the rest of the snapshot via its `snapshot_complete` marker.
        while json.loads(await asyncio.wait_for(ws.recv(), timeout=5))["type"] != "snapshot_complete":
            pass
    return ws


async def _call_tool(ws, cid: str, tool: str, args: dict) -> None:
    text = f"CALL_TOOL {tool} {json.dumps(args)}"
    await ws.send(json.dumps(_frame("user_msg", text=text, conversation_id=cid, source="ui")))


async def _run_undoable(ws, cid: str, tool: str, args: dict) -> dict:
    """Run a Tier-2 tool through a turn; return its activity frame."""
    await _call_tool(ws, cid, tool, args)
    act = await _recv_type(ws, "activity")
    done = await _recv_type(ws, "done")
    assert done["conversation_id"] == cid, done
    return act


async def check_move_and_undo(ws) -> None:
    src, dst = FILES / "a.txt", FILES / "moved" / "a.txt"
    dst.parent.mkdir()
    src.write_text("hello", encoding="utf-8")
    act = await _run_undoable(ws, "u-move", "file_move", {"src": str(src), "dst": str(dst)})
    assert act["undoable"] is True and act["undo_token"], act
    assert act["tier"] == 2, act
    assert dst.is_file() and not src.exists(), "move didn't happen on disk"

    await ws.send(json.dumps(_frame("undo", undo_token=act["undo_token"])))
    rev = await _recv_type(ws, "activity")
    assert rev["task_id"] == act["task_id"], (rev, act)  # reversal threads under the same task
    assert src.is_file() and src.read_text(encoding="utf-8") == "hello", "undo didn't move the file back"
    assert not dst.exists(), "undo left a copy behind"
    print("[check 1] undoable move: activity carries undo_token; undo moves the file back, reversal shares task_id: OK")


async def check_non_reversible(ws) -> None:
    act = await _run_undoable(ws, "u-shout", "shout", {})
    assert act["undoable"] is False, act
    assert "undo_token" not in act, act
    print("[check 2] tool with no inverse: undoable:false, no undo_token: OK")


async def check_double_undo(ws) -> None:
    src, dst = FILES / "b.txt", FILES / "b-moved.txt"
    src.write_text("bee", encoding="utf-8")
    act = await _run_undoable(ws, "u-double", "file_move", {"src": str(src), "dst": str(dst)})
    token = act["undo_token"]

    await ws.send(json.dumps(_frame("undo", undo_token=token)))
    await _recv_type(ws, "activity")  # first undo succeeds
    assert src.is_file() and not dst.exists()

    await ws.send(json.dumps(_frame("undo", undo_token=token)))
    err = await _recv_type(ws, "error")
    assert err["code"] == "undo_already_done" and err["recoverable"] is True, err
    assert src.is_file() and not dst.exists(), "second undo ran the inverse again"
    print("[check 3] double undo: second gets recoverable 'already undone' error, inverse ran exactly once: OK")


async def check_unknown_token(ws) -> None:
    await ws.send(json.dumps(_frame("undo", undo_token="no-such-token")))
    err = await _recv_type(ws, "error")
    assert err["code"] == "undo_unknown" and err["recoverable"] is True, err
    print("[check 4] unknown token: recoverable error, no crash: OK")


async def check_precondition_failure(ws) -> None:
    src, dst = FILES / "c.txt", FILES / "c-moved.txt"
    src.write_text("original", encoding="utf-8")
    act = await _run_undoable(ws, "u-pre", "file_move", {"src": str(src), "dst": str(dst)})

    dst.write_text("tampered since", encoding="utf-8")  # external change
    await ws.send(json.dumps(_frame("undo", undo_token=act["undo_token"])))
    err = await _recv_type(ws, "error")
    assert err["code"] == "undo_precondition_failed" and err["recoverable"] is True, err
    assert dst.read_text(encoding="utf-8") == "tampered since", "undo overwrote a changed file"
    assert not src.exists(), "undo forced the move back"

    # Connection stays usable after the refused undo.
    await ws.send(json.dumps(_frame("user_msg", text="still here?", conversation_id="u-alive", source="ui")))
    while True:
        frame = await _recv(ws)
        if frame["type"] == "done" and frame["conversation_id"] == "u-alive":
            break
        assert frame["type"] in ("token", "spend_update"), frame
    print("[check 5] precondition failure: recoverable error, no forced overwrite, connection usable: OK")


async def check_failed_inverse_releases_token(ws) -> None:
    token = store.record_action(
        tool="broken_inverse_source",
        args_redacted={},
        tier=2,
        lane=1,
        result="ok",
        undoable=True,
        inverse_json={"tool": "no_such_inverse", "args": {}},
        task_id="u-failed",
    )[1]
    assert token

    await ws.send(json.dumps(_frame("undo", undo_token=token)))
    err = await _recv_type(ws, "error")
    assert err["code"] == "undo_failed" and err["recoverable"] is True, err
    assert store.get_action_by_undo_token(token)["consumed"] == 0
    try:
        frame = await _recv(ws, timeout=0.2)
        assert frame["type"] != "activity", frame
    except asyncio.TimeoutError:
        pass
    print("[check 6] failed inverse reports error, emits no success activity, and releases token: OK")


async def check_connect_backlog(ws, port: int, token: str) -> None:
    # One fresh undoable action whose token is never consumed.
    live = await _run_undoable(ws, "u-log", "file_create", {"path": str(FILES / "d.txt"), "content": "dee"})
    live_token = live["undo_token"]

    store.connect()
    expected = len(store.recent_actions(100))
    ws2 = await _connect_auth(port, token, drain_backlog=False)
    try:
        backlog = []
        while len(backlog) < expected:
            frame = await _recv(ws2, timeout=2)
            if frame["type"] == "activity":
                backlog.append(frame)
                continue
            assert frame["type"] == "capabilities_state", frame
        assert backlog, "no backlog replayed"
        # Newest lands last (feed appends): the file_create is the most recent action.
        assert backlog[-1].get("undo_token") == live_token and backlog[-1]["undoable"] is True, backlog[-1]
        tokens = [f.get("undo_token") for f in backlog if "undo_token" in f]
        assert tokens.count(live_token) == 1
        # Every consumed/non-undoable row replays without a token and with the flag honest.
        for f in backlog:
            if "undo_token" not in f:
                assert f["undoable"] is False, f
        # The consumed move tokens from earlier checks must NOT reappear.
        consumed = [a["undo_token"] for a in store.recent_actions(100) if a["consumed"]]
        assert consumed and not (set(consumed) & set(tokens)), (consumed, tokens)
    finally:
        await ws2.close()
    print("[check 7] connect-time backlog: recent actions replayed oldest-first; live token re-armed, consumed ones not: OK")


async def main() -> None:
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    ws = await _connect_auth(port, token)
    try:
        await check_move_and_undo(ws)
        await check_non_reversible(ws)
        await check_double_undo(ws)
        await check_unknown_token(ws)
        await check_precondition_failure(ws)
        await check_failed_inverse_releases_token(ws)
        await check_connect_backlog(ws, port, token)
    finally:
        await ws.close()
        server.close()
        await server.wait_closed()
        await graph.aclose()
    print("[brain.undo] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
