"""Runnable self-check for brain/memory.py (Phase 2 Step 8).

No test framework -- plain asyncio + assert. Run with:
    python brain/tests/test_memory.py

Extraction runs under HALO_EXTRACT_STUB ("remember: ..." lines become facts)
so the checks are offline and deterministic; the store may run either vector
or recency-fallback search -- every assertion holds in both modes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-memory-")
os.environ["HALO_LLM_STUB"] = "1"
os.environ["HALO_EXTRACT_STUB"] = "1"
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_CHECKPOINT_DB"] = str(Path(_TMP) / "checkpoints.db")
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")

import websockets

from brain import graph, memory, store
from brain.ipc.contract import parse_ipc_message
from brain.server import start


class Sink:
    """Fake broadcast/send: contract-validates every frame, then records it."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def __call__(self, msg_type: str, payload: dict) -> None:
        parse_ipc_message(
            {"type": msg_type, "id": str(uuid.uuid4()), "ts": datetime.now(timezone.utc).isoformat(), **payload}
        )
        self.frames.append((msg_type, payload))

    def of(self, msg_type: str) -> list[dict]:
        return [p for t, p in self.frames if t == msg_type]


def _frame(msg_type: str, **payload) -> dict:
    return {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


def _belief_by_text(text: str) -> dict | None:
    return next((b for b in store.list_beliefs() if b["text"] == text), None)


async def _extract(text: str, sink: Sink) -> None:
    await memory.extract("t-mem", [{"role": "user", "content": text}], "stub-key", sink)


async def check_extraction_e2e(port: int, token: str) -> None:
    """Real server turn: remember-line -> belief row + broadcast frame; a
    trivial turn extracts nothing."""
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(json.dumps(_frame("hello", token=token)))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ack["type"] == "hello_ack", ack
    settings = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert settings["type"] == "settings_state", settings
    try:
        # Trivial turn first (also proves retrieval on an empty store is a no-op).
        await ws.send(json.dumps(_frame("user_msg", text="hi", conversation_id="c1", source="ui")))
        while True:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if f["type"] == "spend_update":
                break
        try:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
            assert f["type"] != "belief_state", f"trivial turn extracted a belief: {f}"
        except asyncio.TimeoutError:
            pass

        await ws.send(
            json.dumps(_frame("user_msg", text="remember: I use pnpm for everything", conversation_id="c1", source="ui"))
        )
        belief = None
        while belief is None:
            f = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if f["type"] == "belief_state":
                belief = f
        assert belief["text"] == "I use pnpm for everything", belief
        assert belief["provenance"] == "user", belief
        assert belief["status"] == "active", belief
        row = _belief_by_text("I use pnpm for everything")
        assert row is not None and row["provenance"] == "user", row
    finally:
        await ws.close()
    print("[check 1] turn extraction -> belief row + belief_state; trivial turn extracts nothing: OK")


async def check_dedupe() -> None:
    sink = Sink()
    before = _belief_by_text("I use pnpm for everything")["salience"]
    await _extract("remember: I use pnpm for everything", sink)
    rows = [b for b in store.list_beliefs() if b["text"] == "I use pnpm for everything"]
    assert len(rows) == 1, rows  # no duplicate row
    assert abs(rows[0]["salience"] - min(1.0, before + 0.2)) < 1e-9, (before, rows[0]["salience"])
    assert len(sink.of("belief_state")) == 1, sink.frames
    print("[check 2] duplicate fact -> one row, salience bumped, single delta: OK")


async def check_provenance_matrix() -> None:
    sink = Sink()
    await _extract("remember: my shell is bash okay", sink)
    a = _belief_by_text("my shell is bash okay")
    assert a and a["provenance"] == "user" and a["status"] == "active", a

    # Inferred contradiction: supersede REFUSED, both rows live, user one active.
    await _extract("remember(inferred): my shell is zsh okay", sink)
    a = _belief_by_text("my shell is bash okay")
    b = _belief_by_text("my shell is zsh okay")
    assert a["status"] == "active" and a["superseded_by"] is None, a
    assert b is not None and b["provenance"] == "inferred" and b["status"] == "active", b
    assert not sink.of("activity"), sink.frames  # no narration on a refused supersede

    # User contradiction: supersede succeeds with a visible chain + narration.
    await _extract("remember: my shell is fish okay", sink)
    c = _belief_by_text("my shell is fish okay")
    assert c and c["provenance"] == "user" and c["status"] == "active", c
    superseded = [x for x in (_belief_by_text("my shell is bash okay"), _belief_by_text("my shell is zsh okay"))
                  if x["status"] == "superseded"]
    assert len(superseded) == 1 and superseded[0]["superseded_by"] == c["belief_id"], superseded
    acts = sink.of("activity")
    assert acts and acts[-1]["narrate"] is True and "updated what I remember" in acts[-1]["text"], acts
    print("[check 3] provenance matrix: inferred refused, user supersedes with chain + narration: OK")


async def check_retrieval() -> None:
    # Small store: retrieve returns seeded beliefs, bumps salience, caps at 1.0.
    rows = await asyncio.to_thread(memory.retrieve, "what shell and package manager do I use")
    assert rows, "retrieve returned nothing"
    texts = {r["text"] for r in rows}
    assert "I use pnpm for everything" in texts, texts
    for r in rows:
        fresh = store.get_belief(r["belief_id"])
        assert abs(fresh["salience"] - min(1.0, r["salience"] + 0.2)) < 1e-9, (r["salience"], fresh["salience"])
    for _ in range(3):
        await asyncio.to_thread(memory.retrieve, "what shell and package manager do I use")
    pnpm = _belief_by_text("I use pnpm for everything")
    assert pnpm["salience"] == 1.0, pnpm  # capped, never above

    # Budget: 30 long beliefs -> the injected set fits ~1000 tokens (len//4).
    for i in range(30):
        store.add_belief(f"long belief {i}: " + ("detail " * 55), "project", "inferred")
    out = await asyncio.to_thread(memory.retrieve, "long belief details")
    assert out, "retrieve returned nothing from a full store"
    assert sum(len(r["text"]) // 4 + 1 for r in out) <= 1000, sum(len(r["text"]) // 4 + 1 for r in out)
    assert len(out) < 15, len(out)  # budget trimmed below the k cap
    print("[check 4] retrieval: seeded beliefs returned, +0.2 bump capped at 1.0, ~1k-token budget: OK")


async def check_decay() -> None:
    victim = _belief_by_text("long belief 0: " + ("detail " * 55))
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE belief SET last_used_at=? WHERE belief_id=?", (old, victim["belief_id"]))
    sink = Sink()
    archived = await memory.run_decay(sink)
    assert victim["belief_id"] in archived, archived
    assert store.get_belief(victim["belief_id"])["status"] == "archived"
    deltas = [p for p in sink.of("belief_state") if p["belief_id"] == victim["belief_id"]]
    assert deltas and deltas[0]["status"] == "archived", sink.frames
    print("[check 5] decay: 120-days-unused belief archives with a belief_state delta: OK")


async def check_memory_edit() -> None:
    sink = Sink()
    target = _belief_by_text("my shell is zsh okay") or _belief_by_text("my shell is bash okay")
    bid = target["belief_id"]

    await memory.handle_memory_edit({"belief_id": bid, "op": "edit", "text": "my shell is nushell now"}, sink)
    frame = sink.of("belief_state")[-1]
    assert frame["text"] == "my shell is nushell now" and frame["provenance"] == "user", frame
    assert store.get_belief(bid)["provenance"] == "user"

    await memory.handle_memory_edit({"belief_id": bid, "op": "delete"}, sink)
    assert sink.of("belief_state")[-1]["status"] == "archived", sink.frames
    await memory.handle_memory_edit({"belief_id": bid, "op": "restore"}, sink)
    assert sink.of("belief_state")[-1]["status"] == "active", sink.frames

    await memory.handle_memory_edit({"belief_id": "nope", "op": "delete"}, sink)
    err = sink.of("error")[-1]
    assert err["code"] == "belief_not_found" and err["recoverable"] is True, err
    print("[check 6] memory_edit: edit (provenance->user) / delete / restore round-trip; unknown id errors: OK")


async def check_push_beliefs() -> None:
    sink = Sink()
    await memory.push_beliefs(sink)
    frames = sink.of("belief_state")
    assert len(frames) == min(len(store.list_beliefs()), 100), len(frames)
    assert any(f.get("superseded_by") for f in frames), "supersede chain missing from hydration"
    assert any(f["status"] == "archived" for f in frames), "archived belief missing from hydration"
    print("[check 7] push_beliefs replays every belief as contract-valid frames: OK")


async def main() -> None:
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        await check_extraction_e2e(port, token)
        await check_dedupe()
        await check_provenance_matrix()
        await check_retrieval()
        await check_decay()
        await check_memory_edit()
        await check_push_beliefs()
    finally:
        server.close()
        await server.wait_closed()
        await graph.aclose()
        store.close()
    print("[brain.memory] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
