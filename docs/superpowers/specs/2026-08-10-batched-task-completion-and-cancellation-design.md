# Batched Task Completion, Cancellation, and Work Feedback

**Status:** Design approved on 2026-08-10; awaiting written-spec review.

## Objective

Make long-running document work behave as one coherent user request. When Halo is
asked to inspect the PDFs in a folder, it must expose useful progress while it is
working, allow a stop request to take effect promptly, and produce exactly one
connected final response after the batch reaches a terminal outcome.

The same completion grouping and cancellation state model applies to other
task-shaped tools. The folder-aware input is specific to `doc_digest`.

## Current Failure Modes

1. Every detached task independently starts an internal continuation turn. If the
   model creates one `doc_digest` task per PDF, each task completion produces a
   separate assistant response.
2. `doc_digest` extracts a document with `asyncio.to_thread`. A running thread
   cannot be terminated, so a stop request is not observed until extraction
   returns.
3. Detached work ends the initial interactive turn quickly. Chat's existing
   thinking dots therefore disappear while the background task is still active,
   and the remaining task indicators are mostly static.
4. The task lifecycle has no truthful intermediate cancellation state and maps a
   user-requested stop to generic `failed`.
5. Real task-state broadcasts are partial, while the UI reducer replaces the
   stored task object. A state transition can therefore erase the card's title,
   progress, or step label.

## Considered Approaches

### Prompt and schema changes only

Add `path` plus `glob` to `doc_digest` and tell the model to use one call. This is
small, but it cannot guarantee one response because model tool selection remains
probabilistic.

### Request-scoped task groups (selected)

Make folder ingestion easy to express as one task and group every background task
spawned by the same user turn. Task results accumulate inside the group and one
internal continuation is released only after the group is sealed and all of its
tasks are terminal. This creates a deterministic one-request/one-conclusion
boundary without building a separate orchestration service.

### Dedicated ingestion service

Move document ingestion into a standalone job/orchestration subsystem. This would
support richer pipelines but duplicates the existing durable task runtime and is
not justified by the current problem.

## Backend Design

### Folder-aware document batches

`doc_digest` will accept exactly one of these input shapes:

- `paths`: the existing explicit list of document paths.
- `path` plus an optional `glob`: a folder and a pattern such as `*.pdf` or
  `**/*.pdf`. The default is `*` (direct child files only); recursion must be
  requested explicitly with `**/`.

Folder expansion happens once before task submission. Matches are resolved through
the existing project-root confinement, filtered to files, normalized, de-duplicated,
and sorted deterministically. An empty match is an honest validation error. A batch
is capped at 64 files; inputs above the cap fail before any extraction or model
spending begins and name the cap in the error.

The task processes the resolved list as one unit. Per-file outcomes are checkpoints
and progress events, never assistant messages. One bad, encrypted, scanned, or
otherwise unreadable PDF becomes a structured per-file failure and does not abort
the remaining files. The final reduce step receives all successful digests plus the
failure metadata so the final answer can distinguish findings from omissions.

### Origin-turn task groups

Each user message already has a stable `turn_id`. When the graph detaches a
task-shaped tool, it passes that ID to `TaskRuntime` as `origin_turn_id`.

The runtime keeps an in-memory group keyed by `(conversation_id, origin_turn_id)`:

- registered task IDs;
- bounded terminal outcomes;
- whether the originating graph turn is sealed;
- whether its single continuation has already been dispatched.

The graph seals the group only when the originating turn finishes. A task may
finish before sealing; its result waits in the group. Once the group is sealed and
all registered tasks are terminal, the runtime sends one internal continuation
containing a bounded JSON array of task titles, tools, statuses, reasons, and
results. Successes and failures are reported together. A group containing one task
uses the same path, preserving today's one-task behavior while removing the
per-task continuation race.

The originating turn may emit task-state frames but does not emit a content-bearing
"task started" assistant reply. The status strip and Tasks page carry that live
acknowledgement. The aggregate internal continuation is the only content-bearing
assistant response for the request, so the user never receives a start message,
several per-file completions, and then a separate summary.

Groups are intentionally live-process coordination, not a new replay mechanism.
After a Brain restart, existing task reconciliation remains authoritative: torn
tasks become truthful terminal cards and side effects are never replayed. Durable
cross-restart continuation replay is outside this change.

### Cancellation lifecycle

The shared task state gains two values:

- `stopping`: the Brain accepted the stop request and cancellation is in progress.
- `stopped`: the task ended because the user stopped it. This is terminal and is
  visually neutral, not an execution failure.

On `task_op:stop`, the runtime persists and broadcasts `stopping` immediately,
then signals the task context. Duplicate controls remain disabled while this state
is active. Cooperative task code still checks the cancellation event at every
checkpoint.

PDF extraction moves from an unkillable worker thread to an isolated worker
process. The parent races worker completion against cancellation and a 60-second
per-file extraction deadline. On stop it terminates the worker, escalates to kill
if needed, reaps it, and raises `TaskStopped`. The terminal `stopped` snapshot must
be durable and broadcast within two seconds of the stop request. Document text is
read-only, so termination cannot leave a partial filesystem mutation.

Completed per-file digests remain in the task checkpoint. A stopped batch reports
how many files completed and which partial results were retained; it does not run
the final reduce call after cancellation.

### Authoritative task-state frames

Every real `task_state` broadcast becomes a complete snapshot assembled from the
persisted task row. Live updates and reconnect snapshots therefore share the same
shape. Optional values that no longer apply are cleared deliberately, rather than
being accidentally retained client-side or erased by a partial frame.

This applies to waiting, running, waiting-for-approval, paused, stopping, stopped,
done, and failed transitions. The existing mock task registry already follows this
full-snapshot pattern and must add the two new states without diverging.

## UI and Interaction Design

### Persistent work feedback

Interactive LLM streaming keeps the existing chat thinking indicator. Detached
work is represented in the always-visible status strip and the Tasks page:

- a running task uses a subtle rotating working glyph and a moving progress sheen;
- a queued task uses an indeterminate but non-destructive treatment;
- the label includes the current step, for example `Digesting 3 of 9 · invoice.pdf`;
- `prefers-reduced-motion: reduce` removes rotation and translation while retaining
  the state label and determinate progress width.

Animation communicates live state; it is not decorative. Status text remains
available to assistive technology through a polite live region without announcing
every low-level log chunk.

### Stop feedback

Clicking Stop sends one operation and immediately changes the local control label
to `Stopping…`. The first authoritative `stopping` frame updates both the task card
and the global status strip while retaining title and progress. Pause, resume, lane,
and repeated stop controls are disabled during cancellation.

The terminal card says `Stopped after X of Y` and, when applicable, notes that
partial results were retained. `stopped` cards keep their history but expose no
task controls. A genuine cancellation failure is correlated to the task operation,
re-enables the valid controls, and explains what remains active.

`failed` remains reserved for execution failures and keeps the existing destructive
error treatment. `stopped` uses a muted neutral treatment so a successful user
decision is not presented as a system error.

## Error Handling

- Invalid folders, patterns, empty matches, and over-cap batches fail before work.
- Per-file extraction failures are structured and accumulated; they do not abort
  siblings.
- A reduce failure produces one final failure response that includes the completed
  per-file digest count and does not claim a full synthesis.
- A stop during extraction, mapping, progress emission, or reduction converges on
  `stopped`; it cannot later transition to `done`.
- A task finishing concurrently with Stop checks cancellation before committing
  success, so the accepted stop wins unless success was already durable.
- Group continuation dispatch is guarded for at-most-once behavior.
- Result arrays and continuation text are bounded with the existing result-capping
  rules so a large batch cannot flood chat history.

## Contract and Documentation Changes

The mirrored IPC contract gains `stopping` and `stopped` task states and advances
its minor version. Both Python and TypeScript mirrors, validation self-checks, mock
behavior, task-runtime design, document-ingestion design, task UX documentation,
verification notes, and durable project memory move together.

No new frame type is required.

## Test Strategy

Backend tests must first reproduce and then guard these behaviors:

1. Two detached tasks with different completion times under one origin turn produce
   no early continuation and exactly one continuation after the group is sealed and
   terminal.
2. A mixed success/failure group produces one bounded aggregate continuation.
3. Folder/glob expansion is deterministic, confined, de-duplicated, empty-safe, and
   cap-safe.
4. Multiple PDFs in one `doc_digest` task emit per-file progress but only one task
   outcome.
5. Stop during an intentionally stalled extraction terminates and reaps its worker,
   reaches `stopped` within two seconds, preserves completed checkpoints, and never
   emits `done` or runs reduce.
6. Every live task transition broadcasts a complete snapshot.
7. Authenticated WebSocket coverage proves the UI-visible sequence
   `running -> stopping -> stopped` and one final conversation response per request.

UI tests must cover:

1. Running, stopping, stopped, failed, and partial-progress card copy and controls.
2. Stop's pending state resolving only on authoritative terminal state or a
   correlated operation error.
3. The global status strip remaining visible throughout detached work.
4. Accessible state announcements and reduced-motion behavior.
5. No regression to interactive chat's send/stop control.

Rendered QA uses the real browser workspace where practical and exercises:

`folder request -> active animated status -> Tasks page progress -> Stop -> stopped card`

It checks desktop plus one narrow viewport, console health, clipping, focus,
duplicate controls, and the relevant animation/reduced-motion styles. Native
verification remains necessary for the Windows worker-process termination path.

## Scope Boundary

The implementation may fix adjacent defects discovered in the task, ingestion,
progress, cancellation, and correlated-error surfaces when they directly affect
this workflow. It will not redesign unrelated navigation, memory, approvals,
settings, voice, or Phase-3 coding-adapter features.

## Definition of Done

- A folder PDF request concludes with one connected assistant response.
- Individual PDF failures are named in that response without aborting the batch.
- Visible working feedback persists for the lifetime of detached work.
- Stop produces immediate truthful feedback and a durable terminal state within the
  cancellation budget, including during PDF extraction.
- Task transitions retain their full title/progress metadata.
- Contract sync, focused Python suites, UI tests/typecheck/build, rendered QA, and
  the relevant repository verification gates are green, or any unavailable native
  check is reported explicitly.
