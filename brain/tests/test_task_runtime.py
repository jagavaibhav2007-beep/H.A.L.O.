"""Runnable self-check for the durable Phase-2 TaskRuntime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-task-runtime-")
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_LLM_STUB"] = "1"

from brain import gate, store
from brain.task_runtime import TaskFailed, TaskRuntime, TaskStopped
from brain.tools import files  # registers dir_organize for reconciliation
import websockets

ROOT = Path(tempfile.mkdtemp(prefix="halo-task-files-")).resolve()
store.connect()
store.set_setting("project_roots", [str(ROOT)])


async def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")


async def check_pool_is_bounded_and_progress_is_durable() -> None:
    frames: list[tuple[str, dict]] = []
    releases = [asyncio.Event(), asyncio.Event()]
    started: list[str] = []

    async def broadcast(kind: str, payload: dict) -> None:
        frames.append((kind, payload))

    def make_fn(index: int):
        async def fn(_args: dict, ctx) -> dict:
            started.append(ctx.task_id)
            await ctx.log(f"worker-{index}")
            await ctx.progress(1, 1, "ready", checkpoint={"worker": index})
            await releases[index].wait()
            return {"worker": index}
        return fn

    runtime = TaskRuntime(broadcast, concurrency=1)
    for index in range(2):
        await runtime.submit(
            task_id=f"bounded-{index}", conversation_id="tasks", tool=f"fake-{index}",
            args={}, args_redacted={}, tier=1, lane=1, title=f"Fake {index}",
            steps_total=1, supports_pause=True, fn=make_fn(index),
        )
    await wait_until(lambda: started == ["bounded-0"])
    assert store.get_task("bounded-0")["state"] == "running"
    assert store.get_task("bounded-1")["state"] == "waiting"
    releases[0].set()
    await wait_until(lambda: started == ["bounded-0", "bounded-1"])
    releases[1].set()
    await wait_until(lambda: store.get_task("bounded-1")["state"] == "done")
    assert json.loads(store.get_task("bounded-0")["checkpoint_json"]) == {"worker": 0}
    await runtime.close()
    print("[check 1] cap-1 task pool queues honestly; progress/checkpoints/logs persist and emit: OK")


async def check_pause_resume_and_stop_within_two_seconds() -> None:
    frames: list[tuple[str, dict]] = []

    async def broadcast(kind: str, payload: dict) -> None:
        frames.append((kind, payload))

    async def stepping(_args: dict, ctx) -> dict:
        for step in range(100):
            await ctx.progress(step, 100, f"step {step}", checkpoint={"step": step})
            await asyncio.sleep(0.03)
        return {"done": True}

    runtime = TaskRuntime(broadcast, concurrency=1)
    await runtime.submit(
        task_id="controlled", conversation_id="tasks", tool="stepping",
        args={}, args_redacted={}, tier=1, lane=1, title="Controlled",
        steps_total=100, supports_pause=True, fn=stepping,
    )
    await wait_until(lambda: store.get_task("controlled")["state"] == "running")
    await runtime.handle_op({"id": "pause", "op": "pause", "task_id": "controlled"}, broadcast)
    await wait_until(lambda: store.get_task("controlled")["state"] == "paused")
    paused_step = store.get_task("controlled")["step"]
    await asyncio.sleep(0.12)
    assert store.get_task("controlled")["step"] == paused_step
    await runtime.handle_op({"id": "resume", "op": "resume", "task_id": "controlled"}, broadcast)
    await wait_until(lambda: store.get_task("controlled")["state"] == "running")
    started = time.monotonic()
    await runtime.handle_op({"id": "stop", "op": "stop", "task_id": "controlled"}, broadcast)
    await wait_until(lambda: store.get_task("controlled")["state"] == "stopped")
    assert time.monotonic() - started < 2.0
    assert store.get_task("controlled")["reason"] == "stopped"
    await runtime.close()
    print("[check 2] pause/resume is honest and cooperative stop lands under two seconds: OK")


async def check_stop_lifecycle_uses_complete_snapshots() -> None:
    """Catches a stop transition that loses title/progress or masquerades as failure."""
    frames: list[tuple[str, dict]] = []

    async def broadcast(kind: str, payload: dict) -> None:
        frames.append((kind, payload))

    async def waits(_args: dict, ctx) -> dict:
        await ctx.progress(3, 9, "invoice.pdf", checkpoint={"docs": ["a", "b", "c"]})
        await ctx.cancelled.wait()
        await ctx.checkpoint()
        raise AssertionError("checkpoint must raise TaskStopped")

    runtime = TaskRuntime(broadcast, concurrency=1)
    await runtime.submit(
        task_id="stateful-stop", conversation_id="tasks", tool="doc_digest",
        args={}, args_redacted={}, tier=1, lane=1, title="Digest 9 documents",
        steps_total=9, supports_pause=True, fn=waits,
    )
    await wait_until(lambda: store.get_task("stateful-stop")["step"] == 3)
    await runtime.handle_op(
        {"id": "stop", "op": "stop", "task_id": "stateful-stop"}, broadcast,
    )
    await wait_until(lambda: any(
        kind == "task_state"
        and payload["task_id"] == "stateful-stop"
        and payload["state"] == "stopped"
        for kind, payload in frames
    ))
    states = [
        payload for kind, payload in frames
        if kind == "task_state" and payload["task_id"] == "stateful-stop"
    ]
    assert [payload["state"] for payload in states][-2:] == ["stopping", "stopped"], states
    assert all(payload.get("title") == "Digest 9 documents" for payload in states[-2:]), states
    assert all(
        payload.get("step") == 3 and payload.get("steps_total") == 9
        for payload in states[-2:]
    ), states
    await runtime.close()
    print("[check 2b] stop emits complete stopping -> stopped snapshots: OK")


async def check_restart_reconciliation_preserves_partial_undo() -> None:
    src = ROOT / "before.txt"
    dst = ROOT / "sorted" / "before.txt"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("payload", encoding="utf-8")
    digest = hashlib.sha256(dst.read_bytes()).hexdigest()
    args = {"path": str(ROOT), "moves": [{"src": str(src), "dst": str(dst)}]}
    checkpoint = {"moves": [{"src": str(src), "dst": str(dst), "sha256": digest}], "skipped": [], "failed": []}
    store.upsert_task(
        "torn-organize", state="running", lane=1, title="Torn organize",
        conversation_id="tasks", tool="dir_organize", args_json=json.dumps(args),
        checkpoint_json=json.dumps(checkpoint), supports_pause=1,
    )
    frames: list[tuple[str, dict]] = []

    async def broadcast(kind: str, payload: dict) -> None:
        frames.append((kind, payload))

    runtime = TaskRuntime(broadcast, concurrency=1)
    assert await runtime.reconcile() == 1
    row = store.get_task("torn-organize")
    assert row["state"] == "failed" and "without replaying" in row["reason"]
    action = next(a for a in store.recent_actions(20) if a["task_id"] == "torn-organize" and a["undo_token"])
    await gate.handle_undo({"undo_token": action["undo_token"]}, broadcast)
    assert src.read_text(encoding="utf-8") == "payload" and not dst.exists()
    await runtime.close()
    print("[check 3] torn side effect is never replayed; reconciliation records a usable partial undo: OK")


async def check_organize_emits_one_durable_receipt_per_move() -> None:
    task_id = "organize-receipts"
    source = ROOT / "receipt-source"
    target = ROOT / "receipt-target"
    source.mkdir()
    paths = [source / "one.txt", source / "two.txt"]
    for path in paths:
        path.write_text(path.stem, encoding="utf-8")
    args = {
        "path": str(source),
        "moves": [
            {"src": str(path), "dst": str(target / path.name)}
            for path in paths
        ],
    }
    frames: list[tuple[str, dict]] = []

    async def broadcast(kind: str, payload: dict) -> None:
        frames.append((kind, payload))

    entry = gate.TOOLS["dir_organize"]
    runtime = TaskRuntime(broadcast, concurrency=1)
    await runtime.submit(
        task_id=task_id,
        conversation_id="tasks",
        tool="dir_organize",
        args=args,
        args_redacted=gate.redact("dir_organize", args),
        tier=gate.classify("dir_organize", args),
        lane=1,
        title="Organize receipt files",
        steps_total=len(paths),
        supports_pause=True,
        fn=entry["fn"],
        inverse_builder=entry["inverse"],
    )
    await wait_until(lambda: store.get_task(task_id)["state"] == "done")
    receipts = [
        action for action in store.recent_actions(100)
        if action["task_id"] == task_id and action["tool"] == "file_move"
    ]
    assert len(receipts) == len(paths), receipts
    assert all(not receipt["undoable"] for receipt in receipts), receipts
    move_frames = [
        payload for kind, payload in frames
        if kind == "activity" and payload["task_id"] == task_id and not payload["undoable"]
    ]
    assert len(move_frames) == len(paths), move_frames
    batch = next(
        action for action in store.recent_actions(100)
        if action["task_id"] == task_id and action["tool"] == "dir_organize" and action["undo_token"]
    )
    assert batch["undoable"] == 1
    await runtime.close()
    print("[check 4] organize emits one durable receipt per move and one batch undo: OK")


async def check_safe_persistence_and_structured_terminal_results() -> None:
    async def broadcast(_kind: str, _payload: dict) -> None:
        return None

    seen: list[dict] = []
    continuations: list[str] = []

    async def continuation(_conversation_id: str, text: str, _task_id: str) -> None:
        continuations.append(text)

    async def fail(args: dict, _ctx) -> dict:
        seen.append(args)
        raise TaskFailed("artifact invalid", {"artifacts": [{"status": "invalid"}]})

    async def stop(_args: dict, _ctx) -> dict:
        raise TaskStopped({"artifacts": [{"status": "partial"}]})

    runtime = TaskRuntime(broadcast, continuation, concurrency=1)
    raw = {"source": "secret source", "target": "report.pdf"}
    await runtime.submit(
        task_id="safe-failure", conversation_id="tasks", tool="command",
        args=raw, persisted_args={"source_sha256": "abc", "target": "report.pdf"},
        args_redacted={"target": "report.pdf"}, tier=3, lane=1,
        title="Command", steps_total=None, supports_pause=False, fn=fail,
    )
    await wait_until(lambda: store.get_task("safe-failure")["state"] == "failed")
    failed = store.get_task("safe-failure")
    assert seen == [raw]
    assert "secret source" not in failed["args_json"]
    assert json.loads(failed["result_json"])["artifacts"][0]["status"] == "invalid"
    await wait_until(lambda: len(continuations) >= 1)
    assert '"status": "invalid"' in continuations[-1]

    await runtime.submit(
        task_id="safe-stop", conversation_id="tasks", tool="command",
        args={}, args_redacted={}, tier=3, lane=1, title="Command",
        steps_total=None, supports_pause=False, fn=stop,
    )
    await wait_until(lambda: store.get_task("safe-stop")["state"] == "stopped")
    stopped = store.get_task("safe-stop")
    assert stopped["reason"] == "stopped"
    assert json.loads(stopped["result_json"])["artifacts"][0]["status"] == "partial"
    await wait_until(lambda: len(continuations) >= 2)
    assert '"status": "partial"' in continuations[-1]
    await runtime.close()
    print("[check 4b] raw args stay in memory; safe args and structured failure/stop results persist: OK")


def frame(kind: str, **payload) -> dict:
    return {
        "type": kind,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


async def check_server_detaches_task_from_same_conversation_and_real_stop() -> None:
    from brain import graph
    from brain.server import start

    async def long_task(_args: dict, ctx) -> dict:
        for step in range(10_000):
            await ctx.progress(step, 10_000, f"step {step}", checkpoint={"step": step})
            await asyncio.sleep(0.03)
        return {"done": True}

    gate.register(
        "long_task", long_task, tier=1, task=True, supports_pause=True,
        title="Long task", steps_total=10_000,
    )
    managed, token = await start()
    port = managed.sockets[0].getsockname()[1]
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    try:
        await ws.send(json.dumps(frame("hello", token=token)))
        assert json.loads(await ws.recv())["type"] == "hello_ack"
        while json.loads(await asyncio.wait_for(ws.recv(), timeout=5))["type"] != "snapshot_complete":
            pass

        cid = "detached-conversation"
        await ws.send(json.dumps(frame(
            "user_msg", text="CALL_TOOL long_task {}", conversation_id=cid, source="ui"
        )))
        task_id = None
        first_turn_done = False
        while not (task_id and first_turn_done):
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if event["type"] == "task_state" and event["state"] in {"waiting", "running"}:
                task_id = event["task_id"]
            if event["type"] == "done" and event.get("conversation_id") == cid:
                first_turn_done = True

        # The task is still running but owns neither the conversation lock nor
        # an interactive turn slot: a follow-up in the same conversation ends.
        await ws.send(json.dumps(frame(
            "user_msg", text="parallel chat", conversation_id=cid, source="ui"
        )))
        reply = ""
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if event["type"] == "token" and event.get("conversation_id") == cid:
                reply += event["text"]
            if event["type"] == "done" and event.get("conversation_id") == cid:
                break
        assert "parallel" in reply, reply

        started = time.monotonic()
        await ws.send(json.dumps(frame("task_op", task_id=task_id, op="stop")))
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if event["type"] == "task_state" and event["task_id"] == task_id and event["state"] == "stopped":
                assert event["reason"] == "stopped"
                break
        assert time.monotonic() - started < 2.0
    finally:
        await ws.close()
        managed.close()
        await managed.wait_closed()
        await graph.aclose()
        gate.TOOLS.pop("long_task", None)
    print("[check 5] real server detaches a task from same-conversation chat and task_op stop lands under 2s: OK")


async def main() -> None:
    await check_pool_is_bounded_and_progress_is_durable()
    await check_pause_resume_and_stop_within_two_seconds()
    await check_stop_lifecycle_uses_complete_snapshots()
    await check_restart_reconciliation_preserves_partial_undo()
    await check_organize_emits_one_durable_receipt_per_move()
    await check_safe_persistence_and_structured_terminal_results()
    await check_server_detaches_task_from_same_conversation_and_real_stop()
    print("[brain.task_runtime] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
