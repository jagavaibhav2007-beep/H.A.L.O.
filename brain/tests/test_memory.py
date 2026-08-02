"""Runnable self-check for brain/memory.py (v2 consolidation write path).

No test framework -- plain asyncio + assert. Run with:
    python brain/tests/test_memory.py

Consolidation runs under HALO_EXTRACT_STUB ("remember: ..." / "remember(inferred): ..."
lines in user messages become facts) so the checks are offline and deterministic.
The server end-to-end idle-trigger path is covered by shared/phase2_check.py; here
we drive `consolidate_span` directly for deterministic AUDN/cursor/summary coverage.
"""

from __future__ import annotations

import asyncio
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

from brain import memory, store
from brain.ipc.contract import parse_ipc_message


class Sink:
    """Fake broadcast: contract-validates every frame, then records it."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def __call__(self, msg_type: str, payload: dict) -> None:
        parse_ipc_message(
            {"type": msg_type, "id": str(uuid.uuid4()), "ts": datetime.now(timezone.utc).isoformat(), **payload}
        )
        self.frames.append((msg_type, payload))

    def of(self, msg_type: str) -> list[dict]:
        return [p for t, p in self.frames if t == msg_type]


def _belief_by_text(text: str) -> dict | None:
    return next((b for b in store.list_beliefs() if b["text"] == text), None)


def _span(*user_texts: str) -> list[dict]:
    """A message list of alternating user/assistant turns."""
    msgs: list[dict] = []
    for t in user_texts:
        msgs.append({"role": "user", "content": t})
        msgs.append({"role": "assistant", "content": "ok"})
    return msgs


async def _consolidate(cid: str, messages: list[dict], sink: Sink) -> None:
    await memory.consolidate_span(cid, messages, "stub-key", sink)


# ---------------------------------------------------------------------------


async def check_add_and_summary() -> None:
    cid = "c-add"
    sink = Sink()
    messages = _span("remember: my shell is bash")
    await _consolidate(cid, messages, sink)

    rows = [b for b in store.list_beliefs() if b["text"] == "my shell is bash"]
    assert len(rows) == 1 and rows[0]["provenance"] == "user" and rows[0]["status"] == "active", rows
    frames = sink.of("belief_state")
    assert len(frames) == 1 and frames[0]["text"] == "my shell is bash", frames
    # cursor advanced to the full span length
    assert store.get_conversation_meta(cid)["consolidation_cursor"] == len(messages)
    # a session_summary row was written
    summaries = store.list_session_summaries(cid)
    assert summaries and summaries[0]["text"].startswith("session:"), summaries
    print("[check 1] consolidate: new fact -> ADD + belief_state + summary row + cursor advance: OK")


async def check_cursor_idempotence_and_dedup() -> None:
    cid = "c-add"  # already consolidated in check 1 (cursor == len)
    messages = _span("remember: my shell is bash")

    # Re-consolidating with an advanced cursor: empty span -> silent no-op.
    sink = Sink()
    await _consolidate(cid, messages, sink)
    assert not sink.frames, sink.frames

    # Force a re-run of the SAME span (the retry / startup-recovery scenario):
    # reset the cursor and re-consolidate -> the candidate is a duplicate, so
    # AUDN decides NOOP (bump salience), never a second row.
    before = _belief_by_text("my shell is bash")
    store.set_consolidation_cursor(cid, 0)
    sink2 = Sink()
    await _consolidate(cid, messages, sink2)
    rows = [b for b in store.list_beliefs() if b["text"] == "my shell is bash"]
    assert len(rows) == 1, rows  # NO duplicate row
    after = _belief_by_text("my shell is bash")
    assert after["salience"] == min(1.0, before["salience"] + 0.2), (before, after)
    assert len(sink2.of("belief_state")) == 1, sink2.frames
    print("[check 2] cursor idempotence: empty span no-op; reconsolidated span dedups (NOOP+bump), no dup row: OK")


async def check_audn_provenance_matrix() -> None:
    cid = "c-audn"
    # 1) user fact -> ADD
    await _consolidate(cid, _span("remember: my editor is vim"), Sink())
    vim = _belief_by_text("my editor is vim")
    assert vim and vim["provenance"] == "user" and vim["status"] == "active", vim

    # 2) inferred contradiction -> INVALIDATE proposal, but store refuses
    #    (rule 6): both rows stay live, user one active, no narration.
    sink = Sink()
    await _consolidate(cid, _span("remember: my editor is vim", "remember(inferred): my editor is emacs"), sink)
    vim = _belief_by_text("my editor is vim")
    emacs = _belief_by_text("my editor is emacs")
    assert vim["status"] == "active" and vim["superseded_by"] is None, vim
    assert emacs and emacs["provenance"] == "inferred" and emacs["status"] == "active", emacs
    assert not sink.of("activity"), sink.frames  # refused supersede -> no narration

    # 3) user contradiction -> supersede succeeds with a visible chain + narration.
    sink2 = Sink()
    await _consolidate(
        cid,
        _span("remember: my editor is vim", "remember(inferred): my editor is emacs", "remember: my editor is nano"),
        sink2,
    )
    nano = _belief_by_text("my editor is nano")
    assert nano and nano["provenance"] == "user" and nano["status"] == "active", nano
    superseded = [
        x for x in (_belief_by_text("my editor is vim"), _belief_by_text("my editor is emacs"))
        if x["status"] == "superseded"
    ]
    assert len(superseded) == 1 and superseded[0]["superseded_by"] == nano["belief_id"], superseded
    assert superseded[0]["invalid_at"] is not None, superseded  # v2: validity window closed
    acts = sink2.of("activity")
    assert acts and acts[-1]["narrate"] is True and "updated what I remember" in acts[-1]["text"], acts
    print("[check 3] AUDN matrix: ADD; inferred contradiction refused (rule 6); user contradiction supersedes: OK")


async def check_failure_does_not_advance_cursor() -> None:
    cid = "c-fail"
    messages = _span("remember: I like tea")
    original = store.add_session_summary

    def boom(*_a, **_k):
        raise RuntimeError("injected summary-write failure")

    store.add_session_summary = boom
    sink = Sink()
    try:
        await _consolidate(cid, messages, sink)  # never raises (rule 5)
    finally:
        store.add_session_summary = original

    meta = store.get_conversation_meta(cid)
    assert (meta or {}).get("consolidation_cursor", 0) == 0, meta  # NOT advanced

    # Retry succeeds: the belief written on the failed pass is a duplicate now,
    # so no second row, and the cursor finally advances.
    sink2 = Sink()
    await _consolidate(cid, messages, sink2)
    rows = [b for b in store.list_beliefs() if b["text"] == "I like tea"]
    assert len(rows) == 1, rows
    assert store.get_conversation_meta(cid)["consolidation_cursor"] == len(messages)
    print("[check 4] failed consolidation leaves cursor unadvanced; retry succeeds without duplicating: OK")


async def check_flush_all() -> None:
    cid = "c-flush"
    messages = _span("remember: I use vscode")
    sink = Sink()
    memory._dirty[cid] = {"messages": messages, "api_key": "stub-key", "broadcast": sink}
    await memory.flush_all()
    assert _belief_by_text("I use vscode") is not None
    assert cid not in memory._dirty, "flush_all should clear dirty state"
    assert not memory._idle_tasks, "flush_all should cancel idle timers"
    print("[check 5] flush_all consolidates every dirty conversation and clears state: OK")


async def check_retrieval_and_episodic() -> None:
    rows = await asyncio.to_thread(memory.retrieve, "which editor do I use")
    assert rows, "retrieve returned nothing"
    for r in rows:
        fresh = store.get_belief(r["belief_id"])
        assert abs(fresh["salience"] - min(1.0, r["salience"] + 0.2)) < 1e-9, (r["salience"], fresh["salience"])

    # Episodic: a prior session summary for the conversation is available.
    prepend = memory.episodic_prepend("c-audn")
    assert prepend and prepend.startswith("session:"), prepend
    assert memory.episodic_prepend("no-such-conversation") is None

    # Budget: many long beliefs -> the injected set fits ~1000 tokens.
    for i in range(30):
        store.add_belief(f"long belief {i}: " + ("detail " * 55), "project", "inferred")
    out = await asyncio.to_thread(memory.retrieve, "long belief details")
    assert out and sum(len(r["text"]) // 4 + 1 for r in out) <= 1000, out
    print("[check 6] retrieval bumps salience + budget-caps; episodic prepend returns latest summary: OK")


async def check_partial_index_does_not_hide_live_beliefs() -> None:
    """Regression for 'the agent only recalled one fact while the Memory panel
    showed twelve'. Distinct from check_dead_rows_do_not_starve_search in
    test_store.py: that one covers a *fully* dead k-window, which search_beliefs
    already answers with its empty-result fallthrough. This covers the PARTIAL
    case -- a couple of indexed live rows plus unindexed live ones -- where the
    vector result is non-empty, the fallthrough never fires, and the unindexed
    beliefs were silently dropped from the prompt.

    Live-DB shape this reproduces: 12 active beliefs, only 2 indexed, 12 dead
    rows still occupying the index."""
    if not store._vec_ok:
        print("[check 6b] partial index: SKIPPED (sqlite-vec unavailable)")
        return

    fake: dict[str, list[float]] = {}
    orig_embed, orig_failed = store._embed, store._embed_failed
    saved_conn = store._conn
    store._conn = None
    store._embed = lambda text: fake.get(text)  # None -> written but never indexed
    store._embed_failed = False
    with tempfile.TemporaryDirectory() as isolated:
        try:
            store.connect(Path(isolated) / "partial.db")

            def vec(axis: int) -> list[float]:
                v = [0.0] * store.EMBED_DIM
                v[axis] = 1.0
                return v

            # Dead rows crowding the index, all near the query (they are what
            # the k-window would otherwise spend itself on).
            for i in range(6):
                name = f"dead {i}"
                fake[name] = vec(0)
                store.set_belief_status(store.add_belief(name, "fact", "user"), "archived")

            # Two indexed live beliefs -- enough to make the vector result
            # non-empty, which is what defeats the empty-result fallthrough.
            for i in range(2):
                name = f"indexed live {i}"
                fake[name] = vec(0)
                store.add_belief(name, "fact", "user")

            # Live beliefs written while the embedder was unavailable: stored,
            # never indexed, and nothing ever backfills them.
            unindexed = [f"unindexed live {i}" for i in range(5)]
            for name in unindexed:  # no fake[] entry -> _embed returns None
                store.add_belief(name, "preference", "user")

            fake["what do you know about me"] = vec(0)
            got = {r["text"] for r in await asyncio.to_thread(memory.retrieve, "what do you know about me")}

            missing = [t for t in unindexed if t not in got]
            assert not missing, f"unindexed live beliefs never reached the prompt: {missing}"
            # The model's view must match the panel's view (both are live_beliefs).
            panel = {r["text"] for r in store.live_beliefs(50)}
            assert panel <= got, f"panel shows beliefs the model never saw: {panel - got}"
            assert "dead 0" not in got, "archived belief leaked into the prompt"
        finally:
            store.close()
            store._embed, store._embed_failed = orig_embed, orig_failed
            store._conn = saved_conn
    print("[check 6b] partial index: unindexed live beliefs still reach the prompt, dead ones do not: OK")


async def check_decay() -> None:
    victim = store.add_belief("stale editor note", "preference", "inferred")
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE belief SET last_used_at=? WHERE belief_id=?", (old, victim))
    sink = Sink()
    archived = await memory.run_decay(sink)
    assert victim in archived, archived
    assert store.get_belief(victim)["status"] == "archived"
    deltas = [p for p in sink.of("belief_state") if p["belief_id"] == victim]
    assert deltas and deltas[0]["status"] == "archived", sink.frames
    print("[check 7] decay: 120-days-unused belief archives with a belief_state delta: OK")


async def check_memory_edit() -> None:
    sink = Sink()
    bid = _belief_by_text("my shell is bash")["belief_id"]
    await memory.handle_memory_edit({"belief_id": bid, "op": "edit", "text": "my shell is nushell now"}, sink)
    old = store.get_belief(bid)
    replacement = _belief_by_text("my shell is nushell now")
    assert old["status"] == "superseded" and old["superseded_by"] == replacement["belief_id"], old
    assert replacement["status"] == "active" and replacement["provenance"] == "user", replacement
    edit_frames = sink.of("belief_state")[-2:]
    assert {frame["belief_id"] for frame in edit_frames} == {bid, replacement["belief_id"]}, edit_frames

    # Restoring an earlier version makes it live again and invalidates the
    # version that had replaced it. No active row may keep invalid_at set.
    await memory.handle_memory_edit({"belief_id": bid, "op": "restore"}, sink)
    restored = store.get_belief(bid)
    replaced = store.get_belief(replacement["belief_id"])
    assert restored["status"] == "active" and restored["invalid_at"] is None, restored
    assert restored["superseded_by"] is None, restored
    assert replaced["status"] == "superseded" and replaced["superseded_by"] == bid, replaced

    await memory.handle_memory_edit({"belief_id": bid, "op": "delete"}, sink)
    assert sink.of("belief_state")[-1]["status"] == "archived", sink.frames
    await memory.handle_memory_edit({"belief_id": bid, "op": "restore"}, sink)
    restored = store.get_belief(bid)
    assert restored["status"] == "active" and restored["invalid_at"] is None, restored

    await memory.handle_memory_edit({"belief_id": bid, "op": "delete"}, sink)
    await memory.handle_memory_edit({"belief_id": bid, "op": "purge"}, sink)
    assert store.get_belief(bid) is None
    assert sink.of("belief_deleted")[-1]["belief_id"] == bid, sink.frames

    await memory.handle_memory_edit({"belief_id": "nope", "op": "delete"}, sink)
    err = sink.of("error")[-1]
    assert err["code"] == "belief_not_found" and err["recoverable"] is True, err
    print("[check 8] memory_edit: transactional supersession + safe restore/delete round-trip; unknown id errors: OK")


async def check_push_beliefs_live_and_capped() -> None:
    # A superseded belief (from check 3) and an archived one must NOT hydrate.
    superseded = next(b for b in store.list_beliefs() if b["status"] == "superseded")
    archived = store.add_belief("archived note", "preference", "inferred")
    store.set_belief_status(archived, "archived")

    sink = Sink()
    await memory.push_beliefs(sink)
    frames = sink.of("belief_state")
    ids = {f["belief_id"] for f in frames}
    assert all(f["status"] == "active" for f in frames), frames
    assert superseded["belief_id"] not in ids, "superseded belief leaked into hydration"
    assert archived not in ids, "archived belief leaked into hydration"

    history = Sink()
    await memory.push_history(history)
    history_ids = {f["belief_id"] for f in history.of("belief_state")}
    assert superseded["belief_id"] in history_ids and archived in history_ids, history.frames
    states = history.of("memory_history_state")
    assert states[0]["complete"] is False and states[-1]["complete"] is True, history.frames

    # Cap at 50: seed well past 50 live beliefs, confirm the snapshot is bounded.
    for i in range(60):
        store.add_belief(f"cap belief {i}", "preference", "user")
    sink2 = Sink()
    await memory.push_beliefs(sink2)
    assert len(sink2.of("belief_state")) == 50, len(sink2.of("belief_state"))
    print("[check 9] push_beliefs hydrates live+active only, capped at 50: OK")


async def check_note_turn_triggers() -> None:
    # note_turn is the memory-v2 entry point (fire-and-forget from the graph) and
    # the A2 fix path; consolidate_span coverage above bypasses it. Arm-but-never-fire
    # the idle clock so we test arming/re-arming/pressure, not the timer firing.
    prev_idle = os.environ.get("HALO_MEMORY_IDLE_S")
    os.environ["HALO_MEMORY_IDLE_S"] = "3600"
    try:
        # (a) a turn marks the conversation dirty and arms an idle timer that is
        # RETAINED in _bg_tasks -- a bare asyncio.create_task (the A2 regression)
        # would not be tracked and could be GC'd before it fires.
        cid = "c-note"
        sink = Sink()
        await memory.note_turn(cid, _span("remember: I use nvim"), "stub-key", sink)
        assert cid in memory._dirty, "note_turn did not mark the conversation dirty"
        first = memory._idle_tasks.get(cid)
        assert first is not None and not first.done(), "note_turn did not arm an idle timer"
        assert first in memory._bg_tasks, "idle timer not retained in _bg_tasks (A2: bare create_task)"

        # (b) a second turn re-arms: the prior timer is cancelled and replaced (no leak).
        await memory.note_turn(cid, _span("remember: I use nvim", "remember: I like tea"), "stub-key", sink)
        second = memory._idle_tasks.get(cid)
        assert second is not None and second is not first, "note_turn did not replace the idle timer"
        # The idle coro swallows CancelledError and returns, so a cancelled timer
        # settles as .done() (not .cancelled()). gather returns promptly only because
        # cancel() was requested -- an un-cancelled timer would still be sleeping.
        await asyncio.gather(first, return_exceptions=True)
        assert first.done(), "prior idle timer not cancelled on re-arm"

        # (c) pressure (Letta): a large un-consolidated span consolidates NOW.
        # One bulk assistant message pushes est past _PRESSURE_TOKENS while
        # leaving exactly one extractable fact.
        second.cancel()
        memory._idle_tasks.pop(cid, None)
        pcid = "c-pressure"
        bulk = {"role": "assistant", "content": "x" * 33000}  # ~8.2k est tokens > _PRESSURE_TOKENS
        before = set(memory._bg_tasks)
        await memory.note_turn(pcid, _span("remember: I love pizza") + [bulk], "stub-key", Sink())
        # It must ALSO arm the idle timer: the pressure pass can fail (LLM down),
        # and the branch used to return early, leaving nothing pending at all --
        # the span then waited for a further turn to get a second chance.
        retry = memory._idle_tasks.get(pcid)
        assert retry is not None, "pressure path armed no retry: a failed pass would be dropped"
        # Don't gather the retry timer itself -- it is deliberately sleeping
        # HALO_MEMORY_IDLE_S (an hour here); only the consolidation is awaited.
        retry.cancel()
        memory._idle_tasks.pop(pcid, None)
        await asyncio.gather(
            *[t for t in memory._bg_tasks if t not in before], return_exceptions=True
        )
        assert _belief_by_text("I love pizza") is not None, "pressure consolidation did not persist the fact"

        # (d) growth bound: a consolidated conversation must not stay in _dirty.
        # The entry pins the whole message list AND the live broadcast closure
        # (which holds the websocket) for the process lifetime otherwise.
        assert pcid not in memory._dirty, "_dirty was never popped after a successful consolidation"
    finally:
        for t in list(memory._idle_tasks.values()):
            t.cancel()
        memory._idle_tasks.clear()
        memory._dirty.clear()
        if prev_idle is None:
            os.environ.pop("HALO_MEMORY_IDLE_S", None)
        else:
            os.environ["HALO_MEMORY_IDLE_S"] = prev_idle
    print("[check 10] note_turn: marks dirty + arms a retained idle timer, re-arm cancels the old one, pressure consolidates now: OK")


async def main() -> None:
    store.connect()
    try:
        await check_add_and_summary()
        await check_cursor_idempotence_and_dedup()
        await check_audn_provenance_matrix()
        await check_failure_does_not_advance_cursor()
        await check_flush_all()
        await check_retrieval_and_episodic()
        await check_partial_index_does_not_hide_live_beliefs()
        await check_decay()
        await check_memory_edit()
        await check_push_beliefs_live_and_capped()
        await check_note_turn_triggers()
    finally:
        store.close()
    print("[brain.memory] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
