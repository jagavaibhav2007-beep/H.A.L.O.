"""SQLite store for Halo's Brain (Phase 2 Step 1, D3).

One module, no ORM, raw SQL. Owns beliefs/actions/tasks/spend/settings.
LangGraph's checkpointer gets its own file (checkpoints.db) -- not here.

Callers (async code) must wrap calls in asyncio.to_thread; this module is
plain sync sqlite3 with check_same_thread=False. A module-level re-entrant
lock serializes complete operations and transaction boundaries across workers.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from functools import wraps
from threading import RLock
from datetime import datetime, timezone
from pathlib import Path

import sqlite_vec  # hard dep -- hoisted so it isn't re-imported inside three hot functions

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5
EMBED_DIM = 384

_conn: sqlite3.Connection | None = None
_embedder = None  # lazy fastembed.TextEmbedding singleton
_vec_ok = False  # whether belief_vec is usable this session
_embed_failed = False  # memoize a failed embedder init so we don't retry every call mid-turn
_OP_LOCK = RLock()  # one shared sqlite3 connection: serialize complete operations/transactions
_EMBED_LOCK = RLock()  # construction of the embedder singleton ONLY. A4 deliberately
                       # runs _embed outside _OP_LOCK, which by construction makes it
                       # concurrent -- unguarded, two turns each load a ~130MB model on
                       # cold start. Never widen this to _OP_LOCK: that would undo A4 and
                       # stall every store operation behind a first-call model download.


def _serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _OP_LOCK:
            return fn(*args, **kwargs)

    return wrapped


def _default_db_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return base / "Halo" / "halo.db"


@_serialized
def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Idempotent per-path: returns the existing connection if already open."""
    global _conn, _vec_ok
    if _conn is not None:
        return _conn

    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    extension_loaded = False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        extension_loaded = True
    except Exception as exc:
        logger.warning(
            "sqlite-vec unavailable (%s); belief search will fall back to recency",
            type(exc).__name__,
        )
        _vec_ok = False

    _run_migrations(conn)
    if extension_loaded:
        try:
            with conn:
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS belief_vec USING vec0(embedding float[{EMBED_DIM}])"
                )
            _vec_ok = True
        except Exception as exc:
            logger.warning(
                "could not create belief_vec virtual table (%s); search will fall back to recency",
                type(exc).__name__,
            )
            _vec_ok = False
    _conn = conn
    return conn


@_serialized
def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# [v2] Episodic + consolidation-cursor tables. Kept separate so both the
# fresh-create path and the v1->v2 upgrade path run the exact same DDL.
_V2_NEW_TABLES = """
    CREATE TABLE IF NOT EXISTS session_summary (
        summary_id TEXT PRIMARY KEY,
        conversation_id TEXT,
        text TEXT,
        key_points_json TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS conversation_meta (
        conversation_id TEXT PRIMARY KEY,
        consolidation_cursor INTEGER DEFAULT 0,
        message_count INTEGER DEFAULT 0,
        last_activity_at TEXT
    );
"""


# [v3] Per-document digest cache for doc_digest (Layer 2,
# systemdesign/13-document-ingestion.md). Keyed by content hash so an
# unchanged file's map call is skipped entirely. Pure CREATE IF NOT EXISTS,
# so both the fresh path and every upgrade path run it idempotently.
_V3_NEW_TABLES = """
    CREATE TABLE IF NOT EXISTS digest_cache (
        path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        digest_version INTEGER NOT NULL,
        digest_json TEXT NOT NULL,
        created_at TEXT,
        PRIMARY KEY (path, sha256, digest_version)
    );
"""


_TASK_TABLE = """
    CREATE TABLE IF NOT EXISTS task (
        task_id TEXT PRIMARY KEY,
        state TEXT,
        lane INTEGER,
        title TEXT,
        step INTEGER,
        steps_total INTEGER,
        step_label TEXT,
        reason TEXT,
        conversation_id TEXT,
        tool TEXT,
        args_json TEXT,
        supports_pause INTEGER NOT NULL DEFAULT 0,
        checkpoint_json TEXT,
        result_json TEXT,
        intent_action_id TEXT,
        started_at TEXT,
        updated_at TEXT
    );
"""


def _run_migrations(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    with conn:
        if version < 1:
            # Fresh DB: create everything directly at the v2 shape (belief
            # carries valid_at/invalid_at from the start).
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS belief (
                    belief_id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    kind TEXT,
                    provenance TEXT NOT NULL CHECK (provenance IN ('user','inferred')),
                    salience REAL NOT NULL DEFAULT 0.6,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','superseded')),
                    superseded_by TEXT,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    valid_at TEXT,
                    invalid_at TEXT
                );

                CREATE TABLE IF NOT EXISTS belief_map (
                    rowid INTEGER PRIMARY KEY,
                    belief_id TEXT UNIQUE NOT NULL
                );

                CREATE TABLE IF NOT EXISTS action (
                    action_id TEXT PRIMARY KEY,
                    tool TEXT,
                    args_redacted TEXT,
                    tier INTEGER,
                    lane INTEGER,
                    result TEXT,
                    undoable INTEGER NOT NULL DEFAULT 0,
                    undo_token TEXT UNIQUE,
                    inverse_json TEXT,
                    consumed INTEGER NOT NULL DEFAULT 0,
                    task_id TEXT,
                    ts TEXT NOT NULL
                );
                """
                + _TASK_TABLE
                + """
                CREATE TABLE IF NOT EXISTS spend (
                    day TEXT PRIMARY KEY,
                    usd REAL NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
                + _V2_NEW_TABLES
            )
        elif version < 2:
            # Upgrade an existing v1 DB in place. valid_at backfills to
            # created_at; superseded rows are dead so they get invalid_at too
            # (archived rows keep invalid_at NULL -- "not auto-injected", not
            # "false"). Idempotent: executescript below implicitly commits, so
            # a crash between here and PRAGMA user_version=2 must not re-raise
            # "duplicate column name" on the next startup's retry.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(belief)").fetchall()}
            if "valid_at" not in cols:
                conn.execute("ALTER TABLE belief ADD COLUMN valid_at TEXT")
            if "invalid_at" not in cols:
                conn.execute("ALTER TABLE belief ADD COLUMN invalid_at TEXT")
            conn.execute("UPDATE belief SET valid_at = created_at")
            conn.execute("UPDATE belief SET invalid_at = created_at WHERE status = 'superseded'")
            conn.executescript(_V2_NEW_TABLES)
        if version < 3:
            # v2 -> v3 (and every earlier path): additive table, idempotent.
            conn.executescript(_V3_NEW_TABLES)
        if version < 4:
            # v4: index the raw activity log's sort key. recent_actions orders
            # by ts DESC on every connect (x2 webviews) and prune_actions needs
            # the same order -- both full-scanned the table without this.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_action_ts ON action(ts DESC)")
        if version < 5:
            # Durable TaskRuntime metadata. Additive and idempotent across a
            # restart between an ALTER and the user_version commit. Some early
            # v1 databases predate the task table entirely, so create the full
            # current shape before inspecting/altering columns.
            conn.executescript(_TASK_TABLE)
            task_cols = {r[1] for r in conn.execute("PRAGMA table_info(task)").fetchall()}
            additions = {
                "conversation_id": "TEXT",
                "tool": "TEXT",
                "args_json": "TEXT",
                "supports_pause": "INTEGER NOT NULL DEFAULT 0",
                "checkpoint_json": "TEXT",
                "result_json": "TEXT",
                "intent_action_id": "TEXT",
                "started_at": "TEXT",
            }
            for name, ddl in additions.items():
                if name not in task_cols:
                    conn.execute(f"ALTER TABLE task ADD COLUMN {name} {ddl}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_state ON task(state, updated_at DESC)")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _embed(text: str) -> list[float] | None:
    """Lazily init fastembed and embed one string. Returns None on any failure
    (offline/first-download-fails) -- memory degrades, never breaks (rule 5)."""
    global _embedder, _embed_failed
    if not _vec_ok or _embed_failed:
        return None
    try:
        if _embedder is None:
            # Double-checked: the fast path stays lock-free, and the loser of
            # the race re-reads the singleton the winner published.
            with _EMBED_LOCK:
                if _embedder is None:
                    from fastembed import TextEmbedding

                    _embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        vec = next(iter(_embedder.embed([text])))
        return [float(x) for x in vec]
    except Exception as exc:
        # ponytail: memoize the failure so a mid-turn offline blip doesn't retry
        # a slow download on every belief write -- next Brain start tries again.
        _embed_failed = True
        # This is an expected degraded mode (for example, first launch while
        # offline), not a crash.  Do not dump provider/cache paths from the
        # exception into normal logs; the exception class is enough to make
        # the fallback diagnosable without exposing a user's local path.
        logger.warning(
            "embedding unavailable (%s); belief search will fall back to recency",
            type(exc).__name__,
        )
        return None


def _index_embedding(conn: sqlite3.Connection, belief_id: str, vec: list[float] | None) -> None:
    """SQL-only: caller precomputes `vec` via _embed() before taking _OP_LOCK
    (A4) so a slow/first-time embed (model load/download) doesn't block every
    other store operation."""
    if vec is None:
        return
    # Caller owns the transaction, so belief text + vector index commit or
    # roll back together.
    conn.execute(
        "DELETE FROM belief_vec WHERE rowid IN (SELECT rowid FROM belief_map WHERE belief_id=?)", (belief_id,)
    )
    conn.execute("DELETE FROM belief_map WHERE belief_id=?", (belief_id,))
    cur = conn.execute("INSERT INTO belief_map(belief_id) VALUES (?)", (belief_id,))
    rowid = cur.lastrowid
    conn.execute(
        "INSERT INTO belief_vec(rowid, embedding) VALUES (?, ?)", (rowid, sqlite_vec.serialize_float32(vec))
    )


def _unindex(conn: sqlite3.Connection, belief_id: str) -> None:
    """SQL-only (caller owns the transaction and _OP_LOCK): drop a belief's
    vector-index rows when it leaves the live set. vec0 returns the k nearest
    rows and status='active' is a *post*-filter, so a dead belief left in the
    index consumes a k-slot a live belief needed -- leave enough dead rows and
    search returns nothing. Every status change out of 'active' must call this."""
    if _vec_ok:
        conn.execute(
            "DELETE FROM belief_vec WHERE rowid IN (SELECT rowid FROM belief_map WHERE belief_id=?)",
            (belief_id,),
        )
    conn.execute("DELETE FROM belief_map WHERE belief_id=?", (belief_id,))


# ---------------------------------------------------------------- beliefs --


def add_candidate_belief(
    text: str,
    kind: str,
    provenance: str,
    *,
    supersede_id: str | None = None,
    salience: float = 0.6,
) -> tuple[str, bool]:
    """Atomically insert an extracted belief and optionally supersede one.

    The belief row, provenance decision, supersession relationship, and vector
    bookkeeping share one transaction. An inferred candidate is retained but
    cannot displace a user-stated belief, matching the existing rule.
    """
    # A4: embed BEFORE the lock -- first call may load/download the fastembed
    # model, which must not stall every other store-touching turn.
    vec = _embed(text)
    with _OP_LOCK:
        conn = connect()
        belief_id = str(uuid.uuid4())
        now = _now()
        superseded = False
        with conn:
            old = None
            if supersede_id is not None:
                old = conn.execute(
                    "SELECT provenance, status FROM belief WHERE belief_id=?", (supersede_id,)
                ).fetchone()
                if old is None:
                    raise ValueError("add_candidate_belief: unknown supersede_id")
                if old["status"] != "active":
                    raise ValueError("add_candidate_belief: belief to supersede is not active")

            conn.execute(
                "INSERT INTO belief(belief_id, text, kind, provenance, salience, status, created_at, last_used_at, valid_at) "
                "VALUES (?,?,?,?,?,'active',?,?,?)",
                (belief_id, text, kind, provenance, salience, now, now, now),
            )

            if old is not None and not (old["provenance"] == "user" and provenance == "inferred"):
                cur = conn.execute(
                    "UPDATE belief SET status='superseded', superseded_by=?, invalid_at=? "
                    "WHERE belief_id=? AND status='active'",
                    (belief_id, now, supersede_id),
                )
                if cur.rowcount != 1:
                    raise ValueError("add_candidate_belief: belief changed before supersession")
                _unindex(conn, supersede_id)  # superseded belief leaves the live set
                superseded = True

            # Kept last so a vector/index failure rolls back both the candidate
            # and any supersession update made above.
            _index_embedding(conn, belief_id, vec)
    return belief_id, superseded


@_serialized
def get_belief(belief_id: str) -> dict | None:
    conn = connect()
    row = conn.execute("SELECT * FROM belief WHERE belief_id=?", (belief_id,)).fetchone()
    return dict(row) if row else None


@_serialized
def list_beliefs(status: str | None = None) -> list[dict]:
    conn = connect()
    if status is None:
        rows = conn.execute("SELECT * FROM belief ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM belief WHERE status=? ORDER BY created_at DESC", (status,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_belief(belief_id: str, text: str, provenance: str | None = None) -> None:
    # A4: embed before the lock, matching add_candidate_belief.
    vec = _embed(text)
    with _OP_LOCK:
        conn = connect()
        with conn:
            if provenance is None:
                conn.execute("UPDATE belief SET text=? WHERE belief_id=?", (text, belief_id))
            else:
                conn.execute(
                    "UPDATE belief SET text=?, provenance=? WHERE belief_id=?", (text, provenance, belief_id)
                )
            _index_embedding(conn, belief_id, vec)


@_serialized
def set_belief_status(belief_id: str, status: str) -> None:
    conn = connect()
    with conn:
        conn.execute("UPDATE belief SET status=? WHERE belief_id=?", (status, belief_id))
        if status != "active":
            _unindex(conn, belief_id)


@_serialized
def purge_belief(belief_id: str) -> bool:
    """Permanently remove one archived belief and its vector index entry.

    Active and superseded beliefs remain part of the recoverable history and
    cannot be purged through the panel action.
    """
    conn = connect()
    with conn:
        row = conn.execute(
            "SELECT status FROM belief WHERE belief_id=?", (belief_id,)
        ).fetchone()
        if row is None:
            return False
        if row["status"] != "archived":
            raise ValueError("purge_belief: only archived beliefs can be permanently deleted")
        conn.execute(
            "UPDATE belief SET superseded_by=NULL WHERE superseded_by=?", (belief_id,)
        )
        _unindex(conn, belief_id)
        deleted = conn.execute(
            "DELETE FROM belief WHERE belief_id=?", (belief_id,)
        ).rowcount
    return deleted == 1


@_serialized
def restore_belief(belief_id: str) -> list[dict]:
    """Restore an archived or superseded belief without creating two live
    versions of the same supersession chain.

    The target row keeps its identity so the UI's correlated pending control
    can confirm against it. If a later active descendant exists, that row is
    superseded by the restored version in the same transaction. The restored
    validity window starts now and is always open.
    """
    conn = connect()
    now = _now()
    changed_ids: list[str] = []
    with conn:
        target = conn.execute("SELECT * FROM belief WHERE belief_id=?", (belief_id,)).fetchone()
        if target is None:
            raise ValueError("restore_belief: unknown belief_id")

        successor_id = target["superseded_by"] if target["status"] == "superseded" else None
        visited = {belief_id}
        active_successor = None
        while successor_id and successor_id not in visited:
            visited.add(successor_id)
            successor = conn.execute(
                "SELECT * FROM belief WHERE belief_id=?", (successor_id,)
            ).fetchone()
            if successor is None:
                break
            if successor["status"] == "active":
                active_successor = successor
                break
            successor_id = successor["superseded_by"]

        if active_successor is not None:
            conn.execute(
                "UPDATE belief SET status='superseded', superseded_by=?, invalid_at=? WHERE belief_id=?",
                (belief_id, now, active_successor["belief_id"]),
            )
            _unindex(conn, active_successor["belief_id"])  # successor leaves the live set
            changed_ids.append(active_successor["belief_id"])

        conn.execute(
            "UPDATE belief SET status='active', superseded_by=NULL, invalid_at=NULL, valid_at=? "
            "WHERE belief_id=?",
            (now, belief_id),
        )
        changed_ids.append(belief_id)

    return [get_belief(changed_id) for changed_id in changed_ids]


def search_beliefs(query_text: str, k: int = 15, live_only: bool = True) -> list[dict]:
    """Vector similarity over active beliefs; falls back to recency if the
    embedder/vec table is unavailable (memory degrades, never breaks).

    live_only additionally excludes rows with a closed validity window
    (invalid_at set). ponytail: for active rows invalid_at is always NULL, so
    this is belt-and-suspenders today; it becomes load-bearing if invalidation
    ever detaches from the status enum."""
    # A4: embed before the lock, matching add_candidate_belief.
    vec = _embed(query_text)
    with _OP_LOCK:
        conn = connect()
        live = " AND b.invalid_at IS NULL" if live_only else ""
        if vec is not None:
            try:
                rows = conn.execute(
                    f"""
                    SELECT b.*, v.distance AS distance FROM belief_vec v
                    JOIN belief_map m ON m.rowid = v.rowid
                    JOIN belief b ON b.belief_id = m.belief_id
                    WHERE v.embedding MATCH ? AND k = ? AND b.status='active'{live}
                    ORDER BY distance
                    """,
                    (sqlite_vec.serialize_float32(vec), k),
                ).fetchall()
                # Empty is NOT a valid answer here: it means the k nearest vectors
                # were all dead rows (or every active belief written offline is
                # unindexed), not that there are no live beliefs. Fall through to
                # the recency floor rather than returning [] and injecting no memory.
                if rows:
                    return [dict(r) for r in rows]
            except Exception:
                logger.warning("vector search failed; falling back to recency", exc_info=True)

        live_flat = " AND invalid_at IS NULL" if live_only else ""
        rows = conn.execute(
            f"SELECT * FROM belief WHERE status='active'{live_flat} ORDER BY last_used_at DESC LIMIT ?", (k,)
        ).fetchall()
        return [dict(r) for r in rows]


@_serialized
def live_beliefs(limit: int = 50) -> list[dict]:
    """Snapshot hydration set: live (invalid_at IS NULL), active, salience-ranked,
    capped. Deterministic order (stable tiebreak) so two reconnects converge."""
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM belief WHERE invalid_at IS NULL AND status='active' "
        "ORDER BY salience DESC, last_used_at DESC, belief_id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def bump_salience(belief_ids: list[str]) -> None:
    conn = connect()
    now = _now()
    with conn:
        for bid in belief_ids:
            conn.execute(
                "UPDATE belief SET salience=MIN(1.0, salience + 0.2), last_used_at=? WHERE belief_id=?",
                (now, bid),
            )


@_serialized
def decay(half_life_days: float = 30, archive_below: float = 0.2, now: datetime | None = None) -> list[str]:
    """salience *= 0.5 ** (days_unused / half_life); archives below threshold."""
    conn = connect()
    now = now or datetime.now(timezone.utc)
    archived: list[str] = []
    rows = conn.execute("SELECT * FROM belief WHERE status='active'").fetchall()
    with conn:
        for row in rows:
            last_used = datetime.fromisoformat(row["last_used_at"])
            days_unused = (now - last_used).total_seconds() / 86400
            new_salience = row["salience"] * (0.5 ** (days_unused / half_life_days))
            if new_salience < archive_below:
                conn.execute(
                    "UPDATE belief SET salience=?, status='archived' WHERE belief_id=?",
                    (new_salience, row["belief_id"]),
                )
                _unindex(conn, row["belief_id"])  # archived belief leaves the live set
                archived.append(row["belief_id"])
            else:
                conn.execute(
                    "UPDATE belief SET salience=? WHERE belief_id=?", (new_salience, row["belief_id"])
                )
    return archived


# ------------------------------------------------- conversation meta + episodic --


@_serialized
def get_conversation_meta(conversation_id: str) -> dict | None:
    conn = connect()
    row = conn.execute(
        "SELECT * FROM conversation_meta WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    return dict(row) if row else None


@_serialized
def note_conversation_activity(conversation_id: str, message_count: int) -> None:
    """Upsert the conversation's live message count + last-activity stamp. The
    consolidation cursor is left untouched (advanced only by a successful pass)."""
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO conversation_meta(conversation_id, consolidation_cursor, message_count, last_activity_at) "
            "VALUES (?, 0, ?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET message_count=excluded.message_count, "
            "last_activity_at=excluded.last_activity_at",
            (conversation_id, message_count, _now()),
        )


@_serialized
def set_consolidation_cursor(conversation_id: str, cursor: int) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO conversation_meta(conversation_id, consolidation_cursor, message_count, last_activity_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET consolidation_cursor=excluded.consolidation_cursor",
            (conversation_id, cursor, cursor, _now()),
        )


@_serialized
def list_dirty_conversations() -> list[dict]:
    """Conversations with un-consolidated messages (message_count > cursor)."""
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM conversation_meta WHERE message_count > consolidation_cursor"
    ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def add_session_summary(conversation_id: str, text: str, key_points: dict) -> str:
    conn = connect()
    summary_id = str(uuid.uuid4())
    with conn:
        conn.execute(
            "INSERT INTO session_summary(summary_id, conversation_id, text, key_points_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (summary_id, conversation_id, text, json.dumps(key_points or {}), _now()),
        )
    return summary_id


@_serialized
def latest_session_summary(conversation_id: str) -> dict | None:
    conn = connect()
    row = conn.execute(
        "SELECT * FROM session_summary WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------ digest cache --


@_serialized
def get_digest(path: str, sha256: str, digest_version: int) -> dict | None:
    conn = connect()
    row = conn.execute(
        "SELECT digest_json FROM digest_cache WHERE path=? AND sha256=? AND digest_version=?",
        (path, sha256, digest_version),
    ).fetchone()
    return json.loads(row["digest_json"]) if row else None


@_serialized
def put_digest(path: str, sha256: str, digest_version: int, digest: dict) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO digest_cache(path, sha256, digest_version, digest_json, created_at) "
            "VALUES (?,?,?,?,?)",
            (path, sha256, digest_version, json.dumps(digest, ensure_ascii=False), _now()),
        )
        # The file's content moved on, so its older-sha rows are unreachable by
        # any future lookup -- drop them here rather than accumulate one row per
        # save for the life of the file.
        conn.execute("DELETE FROM digest_cache WHERE path=? AND sha256<>?", (path, sha256))


# ----------------------------------------------------------------- actions --


@_serialized
def record_action(
    tool: str,
    args_redacted: dict,
    tier: int,
    lane: int,
    result: str,
    undoable: bool,
    inverse_json: dict | None = None,
    task_id: str | None = None,
) -> tuple[str, str | None]:
    conn = connect()
    action_id = str(uuid.uuid4())
    undo_token = str(uuid.uuid4()) if undoable else None
    with conn:
        conn.execute(
            "INSERT INTO action(action_id, tool, args_redacted, tier, lane, result, undoable, undo_token, "
            "inverse_json, consumed, task_id, ts) VALUES (?,?,?,?,?,?,?,?,?,0,?,?)",
            (
                action_id,
                tool,
                json.dumps(args_redacted),
                tier,
                lane,
                result,
                int(undoable),
                undo_token,
                json.dumps(inverse_json) if inverse_json is not None else None,
                task_id,
                _now(),
            ),
        )
    return action_id, undo_token


@_serialized
def get_action_by_undo_token(token: str) -> dict | None:
    conn = connect()
    row = conn.execute("SELECT * FROM action WHERE undo_token=?", (token,)).fetchone()
    return dict(row) if row else None


@_serialized
def consume_undo_token(token: str) -> bool:
    """Idempotent: False if the token is missing or already consumed."""
    conn = connect()
    with conn:
        cur = conn.execute(
            "UPDATE action SET consumed=1 WHERE undo_token=? AND consumed=0", (token,)
        )
        return cur.rowcount > 0


@_serialized
def release_undo_token(token: str) -> bool:
    """Release a claimed token after its filesystem inverse failed."""
    conn = connect()
    with conn:
        cur = conn.execute(
            "UPDATE action SET consumed=0 WHERE undo_token=? AND consumed=1", (token,)
        )
        return cur.rowcount > 0


@_serialized
def recent_actions(limit: int = 100) -> list[dict]:
    conn = connect()
    rows = conn.execute("SELECT * FROM action ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# The raw activity log is spec'd as a rolling window (systemdesign/03-memory.md),
# not an archive: it powers the feed + undo, and nothing reads deeper than
# recent_actions' 100. 2000 leaves weeks of audit/search history while bounding a
# table whose file_create inverses each hold the full text of a created file.
_ACTION_KEEP = 2000


@_serialized
def prune_actions(keep: int = _ACTION_KEEP) -> int:
    """Trim the activity log to its newest `keep` rows; returns rows deleted.

    An unconsumed undo token is live user-reversible state, not retention
    garbage: those rows survive the sweep at any age, or an old-but-still-
    offered "Undo" would silently stop working."""
    conn = connect()
    with conn:
        cur = conn.execute(
            # Parens matter: AND binds tighter than OR, so without them this
            # deletes every consumed row at any age.
            "DELETE FROM action WHERE (undo_token IS NULL OR consumed = 1) "
            "AND action_id NOT IN (SELECT action_id FROM action ORDER BY ts DESC LIMIT ?)",
            (keep,),
        )
    return cur.rowcount


# ------------------------------------------------------------------- tasks --


@_serialized
def upsert_task(task_id: str, **fields) -> None:
    conn = connect()
    fields = {**fields, "task_id": task_id, "updated_at": _now()}
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields if k != "task_id")
    with conn:
        conn.execute(
            f"INSERT INTO task({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(task_id) DO UPDATE SET {updates}",
            tuple(fields.values()),
        )


@_serialized
def list_tasks(states: list[str] | None = None) -> list[dict]:
    conn = connect()
    if not states:
        rows = conn.execute("SELECT * FROM task ORDER BY updated_at DESC").fetchall()
    else:
        placeholders = ",".join("?" for _ in states)
        rows = conn.execute(
            f"SELECT * FROM task WHERE state IN ({placeholders}) ORDER BY updated_at DESC", tuple(states)
        ).fetchall()
    return [dict(r) for r in rows]


@_serialized
def get_task(task_id: str) -> dict | None:
    row = connect().execute("SELECT * FROM task WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------- spend --


@_serialized
def add_spend(usd: float) -> None:
    conn = connect()
    day = datetime.now(timezone.utc).date().isoformat()
    with conn:
        conn.execute(
            "INSERT INTO spend(day, usd) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd",
            (day, usd),
        )


@_serialized
def month_spend() -> float:
    conn = connect()
    month_prefix = datetime.now(timezone.utc).date().isoformat()[:7]
    row = conn.execute(
        "SELECT COALESCE(SUM(usd), 0) AS total FROM spend WHERE day LIKE ?", (f"{month_prefix}%",)
    ).fetchone()
    return float(row["total"])


# ---------------------------------------------------------------- settings --


@_serialized
def get_setting(key: str, default=None):
    conn = connect()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row["value"])


@_serialized
def set_setting(key: str, value) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
