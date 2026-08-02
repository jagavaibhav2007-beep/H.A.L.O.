# Migration Log
_Database schema changes — newest first._

## v5 (2026-07-31, undated in a prior session — backfilled 2026-08-01) — durable TaskRuntime metadata, `brain/brain/store.py`
`PRAGMA user_version` 4 → 5. Additive only, `if version < 5:` guard; the `_TASK_TABLE` script runs unconditionally first because some v1-era databases predate the `task` table entirely, so the full current shape is created before the column-presence check runs:
- `_TASK_TABLE` ensures the base `task` table exists on any pre-task-table DB.
- New columns on `task` (each guarded by a `PRAGMA table_info` presence check, so re-running the migration on an already-migrated DB is a no-op): `conversation_id TEXT`, `tool TEXT`, `args_json TEXT`, `supports_pause INTEGER NOT NULL DEFAULT 0`, `checkpoint_json TEXT`, `result_json TEXT`, `intent_action_id TEXT`, `started_at TEXT` — the durable-task-runtime fields needed to resume/pause/checkpoint a long-running task across a Brain restart (systemdesign/12-task-runtime.md).
- `CREATE INDEX IF NOT EXISTS idx_task_state ON task(state, updated_at DESC)`.
This entry was missing from the log — landed in a prior session's "durable task runtime" work (commit `48a8392`) without a MigrationLog write-up; backfilled here after CLAUDE.md's "currently at v3" claim was found stale against the actual `SCHEMA_VERSION = 5` in code during a 2026-08-01 audit.

## v4 (2026-07-29) — action-log retention index, `brain/brain/store.py`
`PRAGMA user_version` 3 → 4. Additive only, one more `if version < 4:` guard appended to the existing flat cumulative-guard block inside `_run_migrations`'s single `with conn:` transaction — preserves the documented single-hop invariant (a v1 DB goes straight to `SCHEMA_VERSION` in one transaction, never landing on an intermediate version):
- `CREATE INDEX IF NOT EXISTS idx_action_ts ON action(ts DESC)` — `recent_actions` and the new `prune_actions` retention sweep (DEEPSCAN_AUDIT.md Tranche 1: the `action` table had no retention despite being spec'd as a rolling window) were full-scanning on every connect × 2 webviews without it.
No new tables; the retention sweep itself is application logic (`store.prune_actions`), not a schema object — it exempts rows with an unconsumed `undo_token` so a live undo is never swept.

## v3 (2026-07-24) — doc_digest per-document cache, `brain/brain/store.py`
`PRAGMA user_version` 2 → 3. Additive only, idempotent (`CREATE TABLE IF NOT EXISTS`, runs on both the fresh-create and every upgrade path):
- New `digest_cache(path, sha256, digest_version, digest_json, created_at)`, `PRIMARY KEY (path, sha256, digest_version)` — one cached JSON digest per (file content, digest schema version). `doc_digest` (Layer 2, systemdesign/13-document-ingestion.md) checks it before its per-doc map LLM call; an unchanged file digests for ~0. Bumping `DIGEST_VERSION` in `brain/brain/tools/docs.py` invalidates all cached digests without a schema change.

## v2 (2026-07-23) — memory v2 (consolidation + episodic + bi-temporal), `brain/brain/store.py`
`PRAGMA user_version` 1 → 2. Incremental upgrade of an existing v1 DB (fresh DBs create at v2 shape directly):
- `belief` gains `valid_at TEXT`, `invalid_at TEXT` (Graphiti bi-temporal pattern). Backfill: `valid_at = created_at` for all rows; `invalid_at = created_at` for `status='superseded'` rows (dead), left NULL for active/archived. Retrieval + UI hydration filter `invalid_at IS NULL AND status='active'`; `status` stays the derived value for the frozen IPC contract. New supersessions use the single transactional `add_candidate_belief` path, which sets `invalid_at=now` on the closed row.
- New `session_summary(summary_id, conversation_id, text, key_points_json, created_at)` — one compact episodic summary per consolidated session segment. Recency-only retrieval for now (no summary vector index yet).
- New `conversation_meta(conversation_id, consolidation_cursor, message_count, last_activity_at)` — crash-safe consolidation cursor; a conversation is "dirty" when `message_count > consolidation_cursor`.
Backward-compatible: v1 rows keep their data; only additive columns + two new tables.

## v1 (2026-07-19) — initial schema, `brain/brain/store.py`
`%LOCALAPPDATA%\Halo\halo.db`, WAL mode, guarded by `PRAGMA user_version`.
Tables: `belief`, `belief_map`/`belief_vec` (sqlite-vec vec0, 384-dim, keyed by rowid),
`action`, `task`, `spend`, `settings`. Separate from LangGraph's `checkpoints.db` (D3).
