# Migration Log
_Database schema changes — newest first._

## v1 (2026-07-19) — initial schema, `brain/brain/store.py`
`%LOCALAPPDATA%\Halo\halo.db`, WAL mode, guarded by `PRAGMA user_version`.
Tables: `belief`, `belief_map`/`belief_vec` (sqlite-vec vec0, 384-dim, keyed by rowid),
`action`, `task`, `spend`, `settings`. Separate from LangGraph's `checkpoints.db` (D3).
