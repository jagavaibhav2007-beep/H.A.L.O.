# System Design: Task Runtime

Status: **implemented in the Phase 2 exit-hardening tranche.** `dir_organize`,
`doc_digest`, `command_run`, and `script_run` are task-shaped tools; every Phase 3 sub-phase
(3a–3e) inherits this runtime.

## Problem

Before exit hardening, Phase 2 had exactly one unit of execution: the interactive chat turn. A long-running tool therefore:

- holds one of the 4 global turn slots (`_REAL_TURN_CONCURRENCY`, `server.py`) and the per-conversation lock for its full wall-clock time — four long tasks hang every new conversation in the app;
- cannot be cancelled mid-tool (`stop` is only checked between LLM stream deltas), violating the ≤ ~2 s halt rule in [11-ipc-contract.md](11-ipc-contract.md);
- answered `task_op` with `operation_unsupported`;
- has no channel for streamed output (coding-agent stdout has nowhere to go but `activity`).

## Design: two execution currencies

**Interactive turns** stay exactly as they are: short, serialized per conversation, bounded by the turn semaphore.

**Tasks** are a separate pool. A *task-shaped* tool (declared as such in the tool registry — coding-agent run, browser playbook, `dir_organize`, future GUI actions) does not execute inline in the turn. Instead the gate:

1. Runs the normal permission/tier check (unchanged — the gate stays the single choke point).
2. Records the **intent** row in the activity log (tool, args, `task_id`) *before* any side effect — this is the reconciliation anchor from 11-ipc-contract.md §Cancellation.
3. Submits the tool to the **TaskRuntime** and returns immediately. The turn's tool result is `"started task <task_id>"`, but task-start-only prose and empty assistant placeholders are suppressed. The turn ends and releases its slot and conversation lock within the normal short-turn budget while status surfaces carry the live feedback.

### TaskRuntime (brain-side, asyncio-native, no new deps)

- Module-level runtime in the Brain: a bounded set of task workers, cap separate from turn slots (`HALO_TASK_CONCURRENCY`, default 2; queued tasks report `task_state: waiting` — honest, never silent).
- Every task fn receives a **`TaskContext`**:
  - `task_id`, `lane`
  - `cancelled: asyncio.Event` — the cooperative-cancel flag. Subprocess tools implement stop as terminate-then-kill; step-loop tools check it between steps. `stop` must take effect ≤ ~2 s.
  - `pause_requested: asyncio.Event` — tools that *declare* pause support suspend at the next step boundary and emit `task_state: paused`; `task_op: pause` on a tool without pause support returns a correlated error instead of pretending.
  - `progress(step, steps_total, step_label)` → emits `task_state` (throttled).
  - `log(text)` → emits `task_log` (see below).
- Tasks submitted by one interactive turn share its `origin_turn_id`. The runtime seals that group after the graph has admitted all task calls, then waits at a barrier until every member is terminal. It enqueues exactly one internal continuation containing the ordered outcome set, so a folder request produces one connected assistant conclusion rather than one reply per file/task. Tasks started without an origin turn retain a direct single-task continuation.
- The continuation goes through normal serialized dispatch, so the LLM sees the real success, failure, or stopped outcomes and reports them honestly in-conversation. Internal outcome metadata is excluded from restored user transcripts and stripped before provider calls. The continuation uses the stable `task-group-{origin_turn_id}` turn correlation id.
- Crash/restart: on startup, any intent row without a result row is a torn task → **reconcile first** (read-only check per tool type: did the file move? is the subprocess gone?), then emit `task_state: failed` with `reason` or resume if the tool supports it. Never blindly re-run — task tools are not idempotent (this is the LangGraph resume-re-runs-pre-interrupt hazard; keeping side effects out of graph nodes and inside the runtime sidesteps it).

### `task_op` becomes real

`stop` → immediately emit authoritative `task_state: stopping`, set `cancelled`, and terminate-then-kill subprocesses. Completion emits the neutral terminal state `task_state: stopped`; cancellation is not presented as failure. If the task wins the race and completes first, its real `done` result remains authoritative. `pause`/`resume` → the events above, honest error when unsupported. Omitted `task_id` = all tasks (existing contract semantics).

Every live and snapshot `task_state` frame is a complete task snapshot rather
than a partial patch. That keeps reconnects, the Tasks page, and the compact
status strip convergent without UI-side inference.

### New outbound frame: `task_log`

| field | meaning |
|---|---|
| `task_id` | owning task |
| `seq` | monotonic per task — UI tolerates gaps |
| `text` | coalesced output chunk |

- Brain-side coalescing: flush at ~250 ms or 4 KB, whichever first — never one frame per stdout line.
- **Drop-not-queue**: like `stream_frame`, `task_log` is exempt from the deferred-broadcast queue during snapshots (a reconnecting client missed logs anyway; the activity log holds the durable record). This keeps high-rate task output from tripping the 1 MiB overflow disconnect.
- UI: bounded ring per task (e.g. last 500 chunks), rendered as a tail view in the tasks panel. Routed to UI role only — Voice never receives it; narration stays on `activity(narrate:true)`.

Contract impact: `task_log` was introduced as a backwards-compatible outbound
type. Contract 1.5 adds the `stopping` and `stopped` states while retaining the
same additive minor-version compatibility scheme.

## What this deliberately does not do

- No automatic task replay after restart. Task metadata, args, checkpoints,
  intent, and results are durable; waiting/running/paused work is reconciled to
  a truthful failed state, with partial undo when a checkpoint proves side
  effects. Add resumable replay only for a future tool that can prove it safe.
- No per-task priority/preemption. Two workers, FIFO. <!-- ponytail: FIFO cap-2; add priority when a real starvation case shows up -->
- No changes to Voice routing, the approval gate, or the interrupt-vs-approval rules in 11 — tasks sit *under* the existing gate, not beside it.

## Implemented evidence

1. `brain/brain/task_runtime.py` owns the cap-2 worker pool, durable state,
   reconciliation, cooperative pause/stop, progress, and coalesced logs.
2. `brain/tests/test_task_runtime.py` proves honest queueing, same-conversation
   concurrent chat, origin-turn aggregation, stop under two seconds, and restart reconciliation without
   replaying side effects.
3. `task_log` is mirrored across the 1.5 contract and retained as a bounded
   500-chunk UI tail.
4. `shared/phase2_check.py` exercises `doc_digest` through the authenticated
   WebSocket and verifies folder/glob batching, per-file progress/failure, its
   durable terminal result, and exactly one content-bearing group continuation.
5. `brain/tests/test_commands.py` exercises command approval, safe task-arg
   persistence, bounded logs/results, Job Object descendant cancellation,
   secret redaction, and verified PDF output through the authenticated socket.
