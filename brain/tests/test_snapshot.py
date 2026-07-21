"""Runnable self-check for Step 9: snapshot-on-connect, pending-approval
rehydration after a restart, history summarization, spend rollup.

No test framework -- plain asyncio + assert, matching test_gate.py. Run with:
    python brain/tests/test_snapshot.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-snapshot-")
os.environ["HALO_LLM_STUB"] = "1"
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_CHECKPOINT_DB"] = str(Path(_TMP) / "checkpoints.db")
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")

from brain import gate, graph, llm, store  # noqa: E402
from brain.ipc.contract import parse_ipc_message  # noqa: E402

EXECUTED: list[tuple[str, dict]] = []


def _tool(args):
    EXECUTED.append(("snap_t3", dict(args)))
    return "done"


gate.register("snap_t3", _tool, tier=3, summary="I want to send an email.")


class Collector:
    """Stands in for a connected client's send_fn, validating every frame
    against the real contract as it arrives."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def __call__(self, msg_type: str, payload: dict) -> None:
        frame = parse_ipc_message({"type": msg_type, "id": "x", "ts": "t", **payload})
        self.frames.append(frame)

    def of(self, msg_type: str) -> list[dict]:
        return [f for f in self.frames if f["type"] == msg_type]


async def _drain_broadcast(msg_type: str, payload: dict) -> None:
    pass


async def _call_tool(cid: str, tool: str, args: dict, broadcast=_drain_broadcast) -> None:
    import json

    await graph.run_turn(
        {"conversation_id": cid, "text": f"CALL_TOOL {tool} {json.dumps(args)}", "source": "ui"}, broadcast
    )


# ------------------------------------------------------------------ check 1


async def check_restart_rehydration() -> None:
    """The headline: an approval open when the Brain died must come back on
    reconnect AND still be resumable."""
    cid = "snap-restart"
    sent: list[tuple[str, dict]] = []

    async def broadcast(msg_type, payload):
        sent.append((msg_type, payload))

    await _call_tool(cid, "snap_t3", {"to": "x@y"}, broadcast)
    approval = next(p for t, p in sent if t == "approval_request")
    assert not EXECUTED, "tool ran without approval"

    # Simulate a real process restart: the checkpointer closes AND the
    # in-memory routing map dies with the process. Clearing it is what makes
    # this test actually exercise rehydration rather than the stale map.
    await graph.aclose()
    gate._pending.clear()
    gate._by_conversation.clear()
    assert gate.peek_conversation(approval["approval_id"]) is None

    snap = Collector()
    await graph.snapshot(snap)
    reqs = snap.of("approval_request")
    assert len(reqs) == 1 and reqs[0]["approval_id"] == approval["approval_id"], reqs

    # Second window (orb + workspace are separate webviews, each with its own
    # WS): rehydration already ran, so this only passes if snapshot emits from
    # gate's live pending map rather than from a rehydration delta.
    snap2 = Collector()
    await graph.snapshot(snap2)
    assert [r["approval_id"] for r in snap2.of("approval_request")] == [approval["approval_id"]], snap2.of(
        "approval_request"
    )

    # A card you can see but can't approve is worthless -- prove the resume.
    resumed = await graph.resume_turn(approval["approval_id"], "approve", None, broadcast)
    assert resumed is True
    assert ("snap_t3", {"to": "x@y"}) in EXECUTED, EXECUTED
    print(
        "[check 1] pending approval rehydrated from checkpoints after restart, "
        "still shown to a second window, and resumable: OK"
    )


# ------------------------------------------------------------------ check 2


async def check_snapshot_content() -> None:
    store.connect()
    store.upsert_task("t-live", state="running", lane=1, title="Organizing Downloads", step=2, steps_total=5)
    store.upsert_task("t-old", state="done", lane=1, title="Finished ages ago")
    store.add_belief("the user ships on Fridays", "preference", "user")
    store.add_spend(0.42)

    snap = Collector()
    await graph.snapshot(snap)

    types = [f["type"] for f in snap.frames]
    assert types[0] == "settings_state", types
    assert types[-1] == "spend_update", types  # always-last sentinel
    tasks = snap.of("task_state")
    assert [t["task_id"] for t in tasks] == ["t-live"], tasks  # finished tasks skipped
    assert tasks[0]["step"] == 2 and tasks[0]["title"] == "Organizing Downloads", tasks[0]
    assert any(b["text"] == "the user ships on Fridays" for b in snap.of("belief_state")), snap.of("belief_state")
    assert snap.of("activity"), "action backlog missing from snapshot"
    assert snap.of("spend_update")[0]["month_usd"] >= 0.42, snap.of("spend_update")
    print("[check 2] snapshot: settings + live tasks + beliefs + activity + spend, all contract-valid: OK")


# ------------------------------------------------------------------ check 3


async def check_snapshot_idempotent() -> None:
    """Two windows connect (orb + workspace) -- each gets the same snapshot,
    with no entity emitted twice inside either one."""
    first, second = Collector(), Collector()
    await graph.snapshot(first)
    await graph.snapshot(second)

    def keys(c: Collector) -> list[tuple]:
        return [
            (f["type"], f.get("task_id") or f.get("belief_id") or f.get("approval_id") or f.get("key"))
            for f in c.frames
            if f["type"] in ("task_state", "belief_state", "approval_request", "settings_state")
        ]

    assert keys(first) == keys(second), (keys(first), keys(second))
    assert len(keys(first)) == len(set(keys(first))), keys(first)
    print("[check 3] snapshot idempotent: second connect converges, no duplicate entity ids: OK")


# ------------------------------------------------------------------ check 4


async def check_summarization() -> None:
    store.connect()
    store.set_setting("history_token_budget", 40)  # ~160 chars -> reachable fast
    cid = "snap-long"
    seen: list[list[dict]] = []
    real_stream = llm.stream_chat

    def spy(messages, model, api_key, usage_out=None):
        seen.append(list(messages))
        return real_stream(messages, model, api_key, usage_out)

    llm.stream_chat = spy
    try:
        for i in range(8):
            await graph.run_turn(
                {"conversation_id": cid, "text": f"turn {i}: " + "padding words here " * 6, "source": "ui"},
                _drain_broadcast,
            )
        g = await graph._ensure_graph()
        state = (await g.aget_state({"configurable": {"thread_id": cid}})).values
        assert state.get("summary"), "never summarized past the budget"
        assert state["dropped_before"] > 0, state["dropped_before"]
        # Prompt is bounded: fewer messages than the raw transcript.
        assert len(seen[-1]) < len(state["messages"]), (len(seen[-1]), len(state["messages"]))
        # Turn still lands.
        assert state["messages"][-1]["role"] == "assistant", state["messages"][-1]

        # Failure keeps full history rather than losing it.
        before = dict(state)

        def boom(*a, **k):
            raise RuntimeError("summarizer down")

        llm.stream_chat = lambda messages, model, api_key, usage_out=None: (
            boom() if any("Condense this conversation" in (m.get("content") or "") for m in messages)
            else real_stream(messages, model, api_key, usage_out)
        )
        await graph.run_turn(
            {"conversation_id": cid, "text": "after the failure " + "more padding " * 8, "source": "ui"},
            _drain_broadcast,
        )
        after = (await g.aget_state({"configurable": {"thread_id": cid}})).values
        assert after["dropped_before"] == before["dropped_before"], "dropped messages on a failed summarization"
        assert after["summary"] == before["summary"], "clobbered summary on failure"
        assert len(after["messages"]) > len(before["messages"]), "turn didn't complete"
    finally:
        llm.stream_chat = real_stream
        store.set_setting("history_token_budget", 6000)
    print("[check 4] summarization: prompt bounded past threshold; failure keeps full history: OK")


# ------------------------------------------------------------------ check 5


async def check_spend_rollup() -> None:
    store.connect()
    before = store.month_spend()
    store.add_spend(0.07)  # stub turns cost 0.0, so seed the delta directly
    snap = Collector()
    await graph.snapshot(snap)
    assert abs(snap.of("spend_update")[0]["month_usd"] - (before + 0.07)) < 1e-9, snap.of("spend_update")
    print("[check 5] snapshot spend_update reflects the month rollup: OK")


async def main() -> None:
    try:
        await check_restart_rehydration()
        await check_snapshot_content()
        await check_snapshot_idempotent()
        await check_summarization()
        await check_spend_rollup()
    finally:
        await graph.aclose()
    print("[brain.snapshot] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
