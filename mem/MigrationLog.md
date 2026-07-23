# Migration Log
_Database schema changes — newest first._

## v2 (2026-07-23) — memory v2 (consolidation + episodic + bi-temporal), `brain/brain/store.py`
`PRAGMA user_version` 1 → 2. Incremental upgrade of an existing v1 DB (fresh DBs create at v2 shape directly):
- `belief` gains `valid_at TEXT`, `invalid_at TEXT` (Graphiti bi-temporal pattern). Backfill: `valid_at = created_at` for all rows; `invalid_at = created_at` for `status='superseded'` rows (dead), left NULL for active/archived. Retrieval + UI hydration filter `invalid_at IS NULL AND status='active'`; `status` stays the derived value for the frozen IPC contract. New supersessions (`add_candidate_belief`, `supersede`, `invalidate_belief`) set `invalid_at=now` on the closed row.
- New `session_summary(summary_id, conversation_id, text, key_points_json, created_at)` — one compact episodic summary per consolidated session segment. Recency-only retrieval for now (no summary vector index yet).
- New `conversation_meta(conversation_id, consolidation_cursor, message_count, last_activity_at)` — crash-safe consolidation cursor; a conversation is "dirty" when `message_count > consolidation_cursor`.
Backward-compatible: v1 rows keep their data; only additive columns + two new tables.

## v1 (2026-07-19) — initial schema, `brain/brain/store.py`
`%LOCALAPPDATA%\Halo\halo.db`, WAL mode, guarded by `PRAGMA user_version`.
Tables: `belief`, `belief_map`/`belief_vec` (sqlite-vec vec0, 384-dim, keyed by rowid),
`action`, `task`, `spend`, `settings`. Separate from LangGraph's `checkpoints.db` (D3).
