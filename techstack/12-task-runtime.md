# Tech Stack: Task Runtime

Status: design only — see [systemdesign/12-task-runtime.md](../systemdesign/12-task-runtime.md). Implementation is Tranche B2 of [PHASE3_READINESS_AUDIT.md](../PHASE3_READINESS_AUDIT.md).

| Concern | Choice | Why |
|---|---|---|
| Runtime | Plain `asyncio` in the existing Brain process — bounded worker set + `asyncio.Event` cancel/pause flags | No new deps; the Brain is already asyncio-native; Phase 2's tool functions already run via `asyncio.to_thread` |
| Concurrency cap | `HALO_TASK_CONCURRENCY` env/setting, default 2, separate from `_REAL_TURN_CONCURRENCY` | Long tasks must never consume interactive turn slots |
| Durability | Intent/result rows in the existing SQLite activity log; reconcile-on-startup | Reuses Phase 2's undo/activity schema; no new store |
| Task output | New `task_log` IPC frame, coalesced ~250 ms/4 KB, drop-not-queue during snapshots | Keeps stdout streaming off the `activity` broadcast and under the deferred-queue cap |
| Subprocess control | `terminate()` then `kill()` after grace; Windows Job Object ownership already established in Phase 2 | Matches the ≤ ~2 s halt rule |

No new libraries. LangGraph checkpointing stays for conversations; tasks live outside graph nodes precisely so resume-re-runs can't replay side effects.
