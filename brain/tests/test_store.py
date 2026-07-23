"""Runnable self-check for brain/store.py (Phase 2 Step 1).

No test framework -- plain assert. Run with:
    python brain/tests/test_store.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain import store


def check_fresh_connect_and_reconnect_no_redo_ddl(tmp: Path) -> None:
    db_path = tmp / "halo.db"
    conn = store.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == store.SCHEMA_VERSION, version

    # same-process reconnect: idempotent, returns the live connection, no re-run
    conn2 = store.connect(db_path)
    assert conn2 is conn

    # simulate a second Brain start against the same file: close, reconnect,
    # and prove the DDL guard (user_version) skips re-running CREATE TABLE,
    # and a previously-written row survives the restart.
    bid = store.add_belief("survives restart", "fact", "user")
    store.close()
    conn3 = store.connect(db_path)
    assert conn3 is not conn
    assert conn3.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    assert store.get_belief(bid) is not None
    print("[check 1] fresh connect creates schema; restart reuses DB without re-running DDL: OK")


def check_belief_crud() -> None:
    bid = store.add_belief("the sky is blue", "fact", "user")
    got = store.get_belief(bid)
    assert got is not None and got["text"] == "the sky is blue"
    assert got["status"] == "active"
    assert got["salience"] == 0.6

    store.update_belief(bid, "the sky is often blue")
    got = store.get_belief(bid)
    assert got["text"] == "the sky is often blue"

    store.set_belief_status(bid, "archived")
    got = store.get_belief(bid)
    assert got["status"] == "archived"

    active = store.list_beliefs(status="active")
    archived = store.list_beliefs(status="archived")
    assert all(b["belief_id"] != bid for b in active)
    assert any(b["belief_id"] == bid for b in archived)
    print("[check 2] belief CRUD: OK")


def check_supersede_provenance_matrix() -> None:
    user1 = store.add_belief("user said A", "preference", "user")
    user2 = store.add_belief("user said A2", "preference", "user")
    store.supersede(user1, user2)  # user <- user: ok
    assert store.get_belief(user1)["status"] == "superseded"
    assert store.get_belief(user1)["superseded_by"] == user2

    inf1 = store.add_belief("inferred B", "workflow", "inferred")
    inf2 = store.add_belief("inferred B2", "workflow", "inferred")
    store.supersede(inf1, inf2)  # inferred <- inferred: ok
    assert store.get_belief(inf1)["status"] == "superseded"

    user3 = store.add_belief("user said C", "decision", "user")
    inf3 = store.add_belief("inferred C2", "decision", "inferred")
    store.supersede(inf3, user3)  # inferred <- user: ok (user statement supersedes inference)
    assert store.get_belief(inf3)["status"] == "superseded"

    user4 = store.add_belief("user said D", "decision", "user")
    inf4 = store.add_belief("inferred D2", "decision", "inferred")
    try:
        store.supersede(user4, inf4)  # user <- inferred: must RAISE
        raise AssertionError("expected ValueError for user superseded by inferred")
    except ValueError:
        pass
    assert store.get_belief(user4)["status"] == "active"
    print("[check 3] supersede provenance matrix (user/inferred x4): OK")


def check_candidate_transaction_atomicity() -> None:
    old_inferred = store.add_belief("my editor is vim okay", "preference", "inferred")
    new_user, superseded = store.add_candidate_belief(
        "my editor is emacs okay", "preference", "user", supersede_id=old_inferred
    )
    assert superseded is True
    assert store.get_belief(old_inferred)["status"] == "superseded"
    assert store.get_belief(old_inferred)["superseded_by"] == new_user
    assert store.get_belief(new_user)["status"] == "active"

    old_user = store.add_belief("my terminal is wezterm okay", "preference", "user")
    new_inferred, superseded = store.add_candidate_belief(
        "my terminal is alacritty okay", "preference", "inferred", supersede_id=old_user
    )
    assert superseded is False
    assert store.get_belief(old_user)["status"] == "active"
    assert store.get_belief(old_user)["superseded_by"] is None
    assert store.get_belief(new_inferred)["status"] == "active"

    rollback_old = store.add_belief("my formatter is black okay", "preference", "inferred")
    before_ids = {row["belief_id"] for row in store.list_beliefs()}
    conn = store.connect()
    before_maps = conn.execute("SELECT COUNT(*) FROM belief_map").fetchone()[0]
    original_index = store._index_embedding

    def fail_after_supersession(*_args) -> None:
        raise RuntimeError("injected vector bookkeeping failure")

    store._index_embedding = fail_after_supersession
    try:
        try:
            store.add_candidate_belief(
                "my formatter is ruff okay", "preference", "user", supersede_id=rollback_old
            )
            raise AssertionError("expected injected candidate transaction failure")
        except RuntimeError as exc:
            assert "injected vector" in str(exc), exc
    finally:
        store._index_embedding = original_index

    assert {row["belief_id"] for row in store.list_beliefs()} == before_ids
    rollback_row = store.get_belief(rollback_old)
    assert rollback_row["status"] == "active" and rollback_row["superseded_by"] is None, rollback_row
    assert conn.execute("SELECT COUNT(*) FROM belief_map").fetchone()[0] == before_maps
    print("[check 4] candidate insert/provenance/supersession/vector bookkeeping is atomic: OK")


def check_vector_search_synthetic() -> None:
    """Deterministic, offline: exercises the real vec0 KNN query (serialize_float32,
    the MATCH ... AND k = ... AND status='active' ORDER BY distance shape) without
    depending on a real embedding model download."""
    if not store._vec_ok:
        print("[check 4] vector search: SKIPPED (sqlite-vec extension unavailable)")
        return

    fake_vectors: dict[str, list[float]] = {}

    def fake_embed(text: str) -> list[float]:
        return fake_vectors[text]

    orig_embed, orig_failed = store._embed, store._embed_failed
    store._embed = fake_embed
    store._embed_failed = False
    try:
        pnpm_vec = [0.0] * store.EMBED_DIM
        pnpm_vec[0] = 1.0
        friday_vec = [0.0] * store.EMBED_DIM
        friday_vec[1] = 1.0
        query_vec = [0.0] * store.EMBED_DIM
        query_vec[0] = 0.9
        query_vec[1] = 0.1

        pnpm_text = "the user prefers pnpm over npm"
        friday_text = "the deploy runs on fridays"
        fake_vectors[pnpm_text] = pnpm_vec
        fake_vectors[friday_text] = friday_vec
        fake_vectors["which package manager"] = query_vec

        pnpm_id = store.add_belief(pnpm_text, "preference", "user")
        store.add_belief(friday_text, "workflow", "user")

        results = store.search_beliefs("which package manager", k=5)
        ids_in_order = [r["belief_id"] for r in results]
        assert ids_in_order and ids_in_order[0] == pnpm_id, ids_in_order

        # re-index on update must not orphan the old belief_vec row (the delete
        # order bug: deleting belief_map before belief_vec left the subquery
        # empty, so old vectors piled up under fresh rowids).
        fake_vectors["pnpm updated"] = pnpm_vec
        store.update_belief(pnpm_id, "pnpm updated")
        conn = store.connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM belief_vec v JOIN belief_map m ON m.rowid=v.rowid WHERE m.belief_id=?",
            (pnpm_id,),
        ).fetchone()[0]
        assert count == 1, f"expected exactly one belief_vec row after re-index, got {count}"
    finally:
        store._embed = orig_embed
        store._embed_failed = orig_failed
    print("[check 4] vector search (synthetic embeddings) runs the real vec0 KNN query and ranks correctly: OK")


def check_bump_salience_caps_at_one() -> None:
    bid = store.add_belief("something", "fact", "user", salience=0.9)
    store.bump_salience([bid])
    assert store.get_belief(bid)["salience"] == 1.0
    store.bump_salience([bid])
    assert store.get_belief(bid)["salience"] == 1.0
    print("[check 5] bump_salience caps at 1.0: OK")


def check_decay_archives_old_not_fresh() -> None:
    now = datetime.now(timezone.utc)
    old_id = store.add_belief("stale fact", "fact", "user", salience=0.6)
    fresh_id = store.add_belief("fresh fact", "fact", "user", salience=0.6)

    conn = store.connect()
    old_ts = (now - timedelta(days=90)).isoformat()
    with conn:
        conn.execute("UPDATE belief SET last_used_at=?, created_at=? WHERE belief_id=?", (old_ts, old_ts, old_id))

    archived = store.decay(half_life_days=30, archive_below=0.2, now=now)
    assert old_id in archived, archived
    assert fresh_id not in archived, archived
    assert store.get_belief(old_id)["status"] == "archived"
    assert store.get_belief(fresh_id)["status"] == "active"
    print("[check 6] decay archives stale unused belief, spares fresh one: OK")


def check_undo_token_record_consume() -> None:
    action_id, token = store.record_action(
        tool="file_move", args_redacted={}, tier=2, lane=1, result="ok", undoable=True,
        inverse_json={"op": "move_back"},
    )
    assert token is not None
    got = store.get_action_by_undo_token(token)
    assert got is not None and got["action_id"] == action_id

    assert store.consume_undo_token(token) is True
    assert store.consume_undo_token(token) is False  # double-consume guarded
    assert store.release_undo_token(token) is True
    assert store.consume_undo_token(token) is True

    action_id2, no_token = store.record_action(
        tool="file_read", args_redacted={}, tier=1, lane=1, result="ok", undoable=False,
    )
    assert no_token is None

    recent = store.recent_actions(limit=10)
    assert any(a["action_id"] == action_id for a in recent)
    print("[check 7] undo token record/consume/double-consume + recent_actions: OK")


def check_operations_serialize_across_threads() -> None:
    """A store call must wait behind the module's operation lock."""
    entered = threading.Event()
    finished = threading.Event()

    def writer() -> None:
        entered.set()
        store.set_setting("serialized_writer", "done")
        finished.set()

    store._OP_LOCK.acquire()
    released = False
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            assert entered.wait(1), "writer thread did not start"
            assert not finished.wait(0.1), "store write bypassed the operation lock"
            store._OP_LOCK.release()
            released = True
            assert finished.wait(2), "store write did not resume after lock release"
            future.result()
    finally:
        if not released:
            store._OP_LOCK.release()
    assert store.get_setting("serialized_writer") == "done"
    print("[check 8] concurrent store operations serialize behind one re-entrant lock: OK")


def check_spend_accumulation() -> None:
    store.add_spend(1.5)
    store.add_spend(2.25)
    total = store.month_spend()
    assert total >= 3.75, total
    print("[check 9] spend accumulates within the month: OK")


def check_settings_roundtrip() -> None:
    assert store.get_setting("does_not_exist", default="fallback") == "fallback"
    store.set_setting("openrouter_model", {"light": "gemma", "heavy": "deepseek"})
    assert store.get_setting("openrouter_model") == {"light": "gemma", "heavy": "deepseek"}
    store.set_setting("openrouter_model", {"light": "gemma2"})
    assert store.get_setting("openrouter_model") == {"light": "gemma2"}
    print("[check 10] settings round-trip with JSON values: OK")


def check_tasks() -> None:
    store.upsert_task("t1", state="running", lane=1, title="organize downloads", step=1, steps_total=5)
    store.upsert_task("t1", state="running", step=2)
    tasks = store.list_tasks()
    t1 = next(t for t in tasks if t["task_id"] == "t1")
    assert t1["step"] == 2 and t1["title"] == "organize downloads"

    store.upsert_task("t2", state="done", lane=1)
    running = store.list_tasks(states=["running"])
    assert any(t["task_id"] == "t1" for t in running)
    assert all(t["task_id"] != "t2" for t in running)
    print("[check 11] upsert_task merges fields, list_tasks filters by state: OK")


def check_invalidate_belief_rule_six() -> None:
    """invalidate_belief closes the validity window and enforces rule 6:
    an inferred caller can never invalidate a user-stated belief."""
    inf = store.add_belief("inferred claim", "workflow", "inferred")
    store.invalidate_belief(inf, by_inferred=True)  # inferred target + inferred caller: ok
    row = store.get_belief(inf)
    assert row["status"] == "superseded" and row["invalid_at"] is not None, row

    usr = store.add_belief("user claim", "preference", "user")
    try:
        store.invalidate_belief(usr, by_inferred=True)  # inferred caller vs user: must RAISE
        raise AssertionError("expected ValueError for inferred invalidating user belief")
    except ValueError:
        pass
    assert store.get_belief(usr)["status"] == "active", store.get_belief(usr)

    other = store.add_belief("newer user claim", "preference", "user")
    store.invalidate_belief(usr, superseded_by=other, by_inferred=False)  # user statement: ok
    row = store.get_belief(usr)
    assert row["status"] == "superseded" and row["superseded_by"] == other and row["invalid_at"] is not None, row
    print("[check 12] invalidate_belief closes the window; rule-6 refusal for inferred-vs-user: OK")


def check_conversation_meta_and_dirty() -> None:
    store.note_conversation_activity("cv", 5)
    meta = store.get_conversation_meta("cv")
    assert meta["message_count"] == 5 and meta["consolidation_cursor"] == 0, meta
    assert "cv" in [d["conversation_id"] for d in store.list_dirty_conversations()]  # 5 > 0

    store.set_consolidation_cursor("cv", 5)
    assert store.get_conversation_meta("cv")["consolidation_cursor"] == 5
    assert "cv" not in [d["conversation_id"] for d in store.list_dirty_conversations()]  # 5 == 5

    store.note_conversation_activity("cv", 8)  # more messages -> dirty again, cursor untouched
    meta = store.get_conversation_meta("cv")
    assert meta["consolidation_cursor"] == 5 and meta["message_count"] == 8, meta
    assert "cv" in [d["conversation_id"] for d in store.list_dirty_conversations()]
    print("[check 13] conversation_meta upsert + dirty listing (message_count > cursor): OK")


def check_session_summary_crud() -> None:
    sid = store.add_session_summary("cv", "session: hello world", {"key_points": ["a"]})
    assert sid
    latest = store.latest_session_summary("cv")
    assert latest["text"] == "session: hello world" and latest["summary_id"] == sid, latest
    assert store.list_session_summaries("cv"), "summary not listed"
    assert store.latest_session_summary("no-such-conversation") is None
    print("[check 14] session_summary add/list/latest round-trip: OK")


def check_migration_v1_to_v2(tmp: Path) -> None:
    """Hand-build a v1-shaped DB (user_version=1, no valid_at/invalid_at, no new
    tables), then connect() must ALTER + backfill + create the v2 tables."""
    import sqlite3

    v1_path = tmp / "v1.db"
    now = datetime.now(timezone.utc).isoformat()
    raw = sqlite3.connect(str(v1_path))
    raw.executescript(
        """
        CREATE TABLE belief (
            belief_id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT,
            provenance TEXT NOT NULL CHECK (provenance IN ('user','inferred')),
            salience REAL NOT NULL DEFAULT 0.6,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','superseded')),
            superseded_by TEXT, created_at TEXT NOT NULL, last_used_at TEXT NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO belief(belief_id,text,kind,provenance,salience,status,created_at,last_used_at) "
        "VALUES ('a','active fact','preference','user',0.6,'active',?,?)",
        (now, now),
    )
    raw.execute(
        "INSERT INTO belief(belief_id,text,kind,provenance,salience,status,superseded_by,created_at,last_used_at) "
        "VALUES ('b','dead fact','preference','inferred',0.6,'superseded','a',?,?)",
        (now, now),
    )
    raw.execute("PRAGMA user_version=1")
    raw.commit()
    raw.close()

    store.close()  # release any live module connection before opening the v1 file
    conn = store.connect(v1_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION == 2
        cols = {r[1] for r in conn.execute("PRAGMA table_info(belief)").fetchall()}
        assert "valid_at" in cols and "invalid_at" in cols, cols

        a, b = store.get_belief("a"), store.get_belief("b")
        assert a["valid_at"] == a["created_at"] and a["invalid_at"] is None, a  # active: no window close
        assert b["valid_at"] == b["created_at"] and b["invalid_at"] == b["created_at"], b  # superseded: dead

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "session_summary" in tables and "conversation_meta" in tables, tables
    finally:
        store.close()
    print("[check 16] v1->v2 migration ALTERs belief, backfills valid_at/invalid_at, creates new tables: OK")


def check_migration_v1_to_v2_half_migrated_is_idempotent(tmp: Path) -> None:
    """A3: simulate a crash mid-migration (columns already ALTERed, but
    user_version still 1 because executescript's implicit commit landed
    before PRAGMA user_version=2 ran). connect() must succeed, not raise
    'duplicate column name'."""
    import sqlite3

    v1_path = tmp / "v1_half.db"
    now = datetime.now(timezone.utc).isoformat()
    raw = sqlite3.connect(str(v1_path))
    raw.executescript(
        """
        CREATE TABLE belief (
            belief_id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT,
            provenance TEXT NOT NULL CHECK (provenance IN ('user','inferred')),
            salience REAL NOT NULL DEFAULT 0.6,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','superseded')),
            superseded_by TEXT, created_at TEXT NOT NULL, last_used_at TEXT NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO belief(belief_id,text,kind,provenance,salience,status,created_at,last_used_at) "
        "VALUES ('a','active fact','preference','user',0.6,'active',?,?)",
        (now, now),
    )
    # Half-migrated: columns already added (as the real ALTERs would leave
    # them), but user_version deliberately left at 1 -- the exact state a
    # crash right after executescript's implicit commit would produce.
    raw.execute("ALTER TABLE belief ADD COLUMN valid_at TEXT")
    raw.execute("ALTER TABLE belief ADD COLUMN invalid_at TEXT")
    raw.execute("UPDATE belief SET valid_at = created_at")
    raw.execute("PRAGMA user_version=1")
    raw.commit()
    raw.close()

    store.close()
    conn = store.connect(v1_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION == 2
        assert store.get_belief("a") is not None
    finally:
        store.close()
    print("[check 17] re-entering the v1->v2 migration on a half-migrated DB does not raise: OK")


def check_embed_computed_outside_lock() -> None:
    """A4: _embed() must run BEFORE _OP_LOCK is acquired, so a slow/first-time
    embed call (model load/download) can't block other store operations."""
    embed_started = threading.Event()
    release_embed = threading.Event()

    def slow_embed(text: str):
        embed_started.set()
        assert release_embed.wait(2), "embed blocked waiting on the store lock"
        return None

    orig_embed = store._embed
    store._embed = slow_embed
    store._OP_LOCK.acquire()
    released = False
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(store.add_belief, "lock ordering probe", "fact", "user")
            assert embed_started.wait(1), "_embed was not called while _OP_LOCK was held elsewhere"
            release_embed.set()
            store._OP_LOCK.release()
            released = True
            future.result(timeout=2)
    finally:
        store._embed = orig_embed
        if not released:
            store._OP_LOCK.release()
    print("[check 15] _embed runs before _OP_LOCK is taken (A4): OK")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        check_fresh_connect_and_reconnect_no_redo_ddl(tmp_path)
        check_belief_crud()
        check_supersede_provenance_matrix()
        check_candidate_transaction_atomicity()
        check_vector_search_synthetic()
        check_bump_salience_caps_at_one()
        check_decay_archives_old_not_fresh()
        check_undo_token_record_consume()
        check_operations_serialize_across_threads()
        check_spend_accumulation()
        check_settings_roundtrip()
        check_tasks()
        check_invalidate_belief_rule_six()
        check_conversation_meta_and_dirty()
        check_session_summary_crud()
        check_embed_computed_outside_lock()  # before the migration checks reconnect elsewhere
        check_migration_v1_to_v2(tmp_path)  # last: reopens store on a separate v1 file
        check_migration_v1_to_v2_half_migrated_is_idempotent(tmp_path)
        store.close()
    print("[brain.store] self-check OK")


if __name__ == "__main__":
    main()
