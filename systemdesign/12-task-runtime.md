# System Design: Task Runtime

Status: **design (Tranche B1 of [PHASE3_READINESS_AUDIT.md](../PHASE3_READINESS_AUDIT.md)) — not yet implemented.** Implementation is B2 and gates Phase 3a. Every Phase 3 sub-phase (3a–3e) inherits this shape.

## Problem

Phase 2 has exactly one unit of execution: the interactive chat turn. A "long-running task" today is just a long tool call inside a turn, which means it:

- holds one of the 4 global turn slots (`_REAL_TURN_CONCURRENCY`, `server.py`) and the per-conversation lock for its full wall-clock time — four long tasks hang every new conversation in the app;
- cannot be cancelled mid-tool (`stop` is only checked between LLM stream deltas), violating the ≤ ~2 s halt rule in [11-ipc-contract.md](11-ipc-contract.md);
- answers `task_op`/`lane_pin` with `operation_unsupported`;
- has no channel for streamed output (coding-agent stdout has nowhere to go but `activity`).

## Design: two execution currencies

**Interactive turns** stay exactly as they are: short, serialized per conversation, bounded by the turn semaphore.

**Tasks** are a separate pool. A *task-shaped* tool (declared as such in the tool registry — coding-agent run, browser playbook, `dir_organize`, future GUI actions) does not execute inline in the turn. Instead the gate:

1. Runs the normal permission/tier check (unchanged — the gate stays the single choke point).
2. Records the **intent** row in the activity log (tool, args, `task_id`) *before* any side effect — this is the reconciliation anchor from 11-ipc-contract.md §Cancellation.
3. Submits the tool to the **TaskRuntime** and returns immediately. The turn's tool result is `"started task <task_id>"`; the model wraps up, the turn ends, slot and conversation lock release within the normal short-turn budget.

### TaskRuntime (brain-side, asyncio-native, no new deps)

- Module-level runtime in the Brain: a bounded set of task workers, cap separate from turn slots (`HALO_TASK_CONCURRENCY`, default 2; queued tasks report `task_state: waiting` — honest, never silent).
- Every task fn receives a **`TaskContext`**:
  - `task_id`, `lane`
  - `cancelled: asyncio.Event` — the cooperative-cancel flag. Subprocess tools implement stop as terminate-then-kill; step-loop tools check it between steps. `stop` must take effect ≤ ~2 s.
  - `pause_requested: asyncio.Event` — tools that *declare* pause support suspend at the next step boundary and emit `task_state: paused`; `task_op: pause` on a tool without pause support returns a correlated error instead of pretending.
  - `progress(step, steps_total, step_label)` → emits `task_state` (throttled).
  - `log(text)` → emits `task_log` (see below).
- On completion/failure the runtime writes the **result** row next to the intent row, then enqueues an internal continuation message on the owning conversation. That continuation goes through the normal serialized dispatch, so the LLM sees the real outcome (success, diff summary, or honest failure output) and reports to the user in-conversation. No new LangGraph machinery: the continuation is just a turn.
- Crash/restart: on startup, any intent row without a result row is a torn task → **reconcile first** (read-only check per tool type: did the file move? is the subprocess gone?), then emit `task_state: failed` with `reason` or resume if the tool supports it. Never blindly re-run — task tools are not idempotent (this is the LangGraph resume-re-runs-pre-interrupt hazard; keeping side effects out of graph nodes and inside the runtime sidesteps it).

### `task_op` becomes real

`stop` → set `cancelled`, terminate-then-kill for subprocesses, `task_state: failed reason:"stopped"` (or `done` if the tool completed a clean partial). `pause`/`resume` → the events above, honest error when unsupported. Omitted `task_id` = all tasks (existing contract semantics).

### New outbound frame: `task_log`

| field | meaning |
|---|---|
| `task_id` | owning task |
| `seq` | monotonic per task — UI tolerates gaps |
| `text` | coalesced output chunk |

- Brain-side coalescing: flush at ~250 ms or 4 KB, whichever first — never one frame per stdout line.
- **Drop-not-queue**: like `stream_frame`, `task_log` is exempt from the deferred-broadcast queue during snapshots (a reconnecting client missed logs anyway; the activity log holds the durable record). This keeps high-rate task output from tripping the 1 MiB overflow disconnect.
- UI: bounded ring per task (e.g. last 500 chunks), rendered as a tail view in the tasks panel. Routed to UI role only — Voice never receives it; narration stays on `activity(narrate:true)`.

Contract impact: one new outbound type + no changes to existing frames → `contract_version` minor bump (1.0 → 1.1), which the A6 versioning scheme admits without breaking older clients.

## What this deliberately does not do

- No task persistence beyond the intent/result activity rows — a queued-but-unstarted task does not survive a Brain restart (it never had side effects; the reconciler reports it honestly). Add durable queueing only if a real Phase 3 need appears.
- No per-task priority/preemption. Two workers, FIFO. <!-- ponytail: FIFO cap-2; add priority when a real starvation case shows up -->
- No changes to Voice routing, the approval gate, or the interrupt-vs-approval rules in 11 — tasks sit *under* the existing gate, not beside it.

## Implementation order (B2)

1. Contract: add `task_log`, bump minor version, mirror all three files, extend mock + `phase1_check` if a scripted scenario needs it.
2. TaskRuntime + TaskContext in the Brain; convert `dir_organize` as the first task-shaped tool (closes the AUDIT_PLAN deferral: cooperative cancel + per-step progress).
3. Real `task_op` dispatch; remove it from `_REAL_UNSUPPORTED_OPS`.
4. UI: task log tail in the tasks panel; `task_log` handling in the reducer (bounded ring).
5. Gate: `phase2_check` section — start a long task, chat concurrently in the same conversation, assert `stop` lands ≤ 2 s, kill the Brain mid-task and assert reconciliation reports honestly.
