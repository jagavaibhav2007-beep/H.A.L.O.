# Phase 3 Readiness Audit & Implementation Plan

**Date:** 2026-07-23 · **Author:** Claude Fable 5 (decision-maker), synthesizing three parallel subagent scans: (A) system-design ceilings, (B) whole-repo bug/UI-connection review, (C) open-source landscape research. Scope excludes everything the 2026-07-22 hardening audit ([AUDIT_PLAN.md](AUDIT_PLAN.md)) already closed.

---

## Verification status — 2026-08-01 (checked against the current `token-cost-reduction` tree)

Each Tranche item below was re-verified against actual code, not the plan's own claims. **Update — later on 2026-08-01, the three open Tranche B gaps (B3/B4/B5) were implemented and tested; see the "B-gap remediation" note after the Tranche B list. The Phase-3a gate (Tranche A + B) is now met, with two deliberate, documented sub-deferrals.** The original verification snapshot follows.

Original verdict: **Tranche A fully closed; Tranche B NOT — three of its five items partial/unstarted.**

**Tranche A — DONE (6/6).** A1 `mic`/`skill_op`/`lane_pin` in `_REAL_UNSUPPORTED_OPS`, correlated `operation_unsupported` (`server.py:478,788`), and `_REAL_DISPATCH_TYPES` enumerated + drift-tested in `test_server`. A2 `note_turn` via `memory._spawn` (`graph.py:600`). A3 v2 migration guards each `ALTER` behind a `PRAGMA table_info` check (`store.py`). A4 `_embed()` runs before `_OP_LOCK` (`store.py:348,378,439`). A5 429/`Retry-After` bounded retry + `_LLM_SEM = Semaphore(4)` shared by turns AND memory consolidation (both route through `llm.stream_chat`) (`llm.py:32,241,361-364`). A6 `contract_version` in `hello`/`hello_ack` with major-mismatch stop-retry, and the UI parser now log-and-drops unknown frames/fields instead of closing the socket (`useHaloConnection.ts:150-186`).

**Tranche B — PARTIAL (gate NOT met).**
- **B1 DONE** — `systemdesign/12-task-runtime.md` + `techstack/12-task-runtime.md` exist.
- **B2 DONE** — real `task_op` routes to `task_engine.handle_op` (`server.py:783-787`); `brain/brain/task_runtime.py` (467 LOC) has `TaskContext` with `cancelled`/`pause_requested` `asyncio.Event`s and real pause/resume/stop; `task_log` frame type is in both contract mirrors and is deferred-queue-throttled in `server.py`.
- **B3 PARTIAL** — per-thread checkpoint depth IS pruned (`graph.py:_prune_checkpoints`, `_CHECKPOINT_KEEP`), but `rehydrate_pending` still `aget_state`s *every thread ever created* on every connect (`graph.py:707`, ponytail-commented "add an index if it ever gets big"). The plan's own gate ("test: connect time flat as thread count grows") does not exist. The reconnect-livelock ceiling (finding #2) is therefore only half-closed.
- **B4 PARTIAL** — supervisor ladder now repeats at 30s forever instead of exhausting (`supervisor.rs:143-148`, ✓). But Voice still has NO in-process reconnect: `run()` returns cleanly on any `ConnectionClosed` and `main()` has no retry loop (`voice/__main__.py:57-108`), exactly the "~40s Brain crash-loop kills Voice for the session" gap the plan flagged. Only the supervisor half of B4 is done.
- **B5 NOT DONE** — no per-conversation turn cap in the reducer (Brain owns full history, but the UI keeps every turn in memory), and `pushActivity` still does `[...state.activities, activity]` — a full array copy per frame (`reducer.ts:288-291`), the exact O(n)-per-frame cost B5 was meant to remove. The activity ring is length-capped (`ACTIVITY_CAP = 10_000`) but not index-based.

**B-gap remediation — 2026-08-01 (later same day).** The three open B items are now implemented and tested:
- **B3 DONE (with a documented sub-deferral).** `rehydrate_pending` now deserializes only threads whose *latest* checkpoint carries an `__interrupt__` write, via the new `graph._threads_with_open_interrupt(saver)` helper — O(suspended threads) instead of O(all threads ever) on every connect. The predicate was verified empirically against the real `checkpoints.db` and a live interrupt→resume round-trip to equal `aget_state(...).interrupts` exactly (and `snap.interrupts` remains the payload authority, so the query can only narrow, never fabricate). Gate: new `test_graph` check 6 (scoping is flat regardless of total thread count; resumed threads drop out) + the existing `phase2_check` "approval open at kill time returns in the reconnect snapshot and still resumes" still passes. **Deliberately NOT done: whole-thread checkpoint archival** — per-thread depth is already capped (`_prune_checkpoints`, `_CHECKPOINT_KEEP=20`), there is no reliable "conversation is closed" signal, and deleting a dormant thread's checkpoints would destroy the resumable history the Brain is meant to own. Not worth the data-loss risk; the rehydrate-scoping fix removes the actual per-connect cost.
- **B4 DONE.** Voice now has an in-process reconnect ladder (`voice/__main__.py::_reconnect_loop`, 1s/5s/30s repeating) that re-reads `session.json` fresh every attempt so it follows the Brain to its new ephemeral port; a healthy connect resets the ladder, and process exit now means real failure only. `run()` is unchanged so the existing tests still hold. Gate: new `test_client` checks 5–6 (fresh re-read follows a new port; missing session tolerated without crashing).
- **B5 DONE for the turn cap; activity-ring rewrite deliberately dropped.** In-memory turns are capped per conversation at `MAX_TURNS=200` (`reducer.ts::pushTurn`, trim-from-front so the open streaming turn can never be dropped); history load stays uncapped by design. Gate: new `reducer.selfcheck` case crossing the cap + preserving the streaming turn. **The "index-based activity ring" half is intentionally not done: B2 already moved the high-rate producer (coding-agent stdout) to the separate, bounded `task_log`→`taskLogs` path, so `pushActivity`'s array copy is now human-paced and bounded at `ACTIVITY_CAP`. A mutable-ring rewrite would reintroduce exactly the `getSnapshot`-identity class of bug fixed earlier this session, for no measurable gain.** Its premise was removed by B2.

Verification of the remediation: `tsc` clean, Vitest 68/68, `reducer.selfcheck`, `test_graph`, `test_client`, `phase2_check`, and `check_contract_sync` (34 schemas) all green.

**Tranche C.** C1 DONE (faster-whisper + Kokoro are the doc picks across `techstack/00`, `techstack/02`, `systemdesign/02`). C2 DONE (SKILL.md format recorded in `systemdesign/08` + `techstack/08`). C3 (PyInstaller prototype) and C4 (`stream_frame` per-client subscription) not started — but both are scheduled "before 3c"/"with 3d" by the plan itself, so they are not overdue. Note `_frame_visible_to` (`server.py:135`) already has the routing hook C4 would extend.

**Stale evidence note:** every "shared/ipc-contract.json" reference in the tables below is out of date — commit `e41d77b` deleted that file and made `check_contract_sync.py` diff the Python and TypeScript `CONTRACT_SPEC` dicts against each other directly. The contract is now two hand-mirrored runtime files, no separate JSON. The findings themselves still hold; only the file path is wrong.

**Bottom line (updated 2026-08-01):** Tranche A and Tranche B are both done; the two intentional sub-deferrals (B3 whole-thread archival, B5 activity-ring) are documented above with reasoning and are not blockers. **The Phase-3a architectural gate is met.** Remaining before specific sub-phases: Tranche C3 (PyInstaller, before 3c) and C4 (`stream_frame` subscription, with 3d) — both correctly scheduled just-in-time, not now.

---

## Verdict

Phase 2 is a solid *interactive-chat* spine, but its architecture equates "the app" with **a handful of short turns against fresh databases**. Phase 3 breaks that equation on three axes — long-running tasks, high-rate streaming, and continuous schema evolution — and each axis has a concrete P1 ceiling today. None require a rewrite; all are targeted. Two live bugs and two latent ones were also found. The OSS scan's conclusion is that the homegrown core (memory, approval flow, supervisor) should be **kept** — the wins are pattern-borrowing and two stale doc-level tech picks, not new dependencies.

**Decision: do not start Phase 3a until Tranche A and Tranche B below are done.** Tranche C runs alongside or just-in-time per sub-phase.

---

## Consolidated findings (ranked, deduplicated)

### P1 — will break Phase 3 outright

| # | Finding | Evidence | Phase-3 failure mode |
|---|---------|----------|----------------------|
| 1 | **No task runtime.** A long tool call is "one graph turn": it holds one of 4 global turn slots (`brain/brain/server.py:40`) *and* the conversation lock for its full wall-clock time; tools get no cancellation token (`brain/brain/gate.py:231-296` — `stop` is only checked between LLM stream deltas); `task_op`/`lane_pin` return `operation_unsupported`. | server.py:374-404, gate.py, graph.py:207-233 | Two 30-min coding runs + a browser playbook consume 3 of 4 slots; a 4th long task hangs **every new conversation in the app** with no error. "Stop" violates the contract's ≤2 s halt rule. Compounded by the LangGraph resume semantics: code before an `interrupt()` may re-run on resume — cheap for Phase 2 file ops, **not idempotent** for 3a subprocesses / 3b browser actions. |
| 2 | **Snapshot cost grows with all-time data and collides with the 1 MiB deferred-broadcast cap → reconnect livelock.** `rehydrate_pending` deserializes state for *every thread ever created* on *every connect* (`brain/brain/graph.py:504-531`); `checkpoints.db` is never pruned (~quadratic per-thread growth); while a snapshot streams, broadcasts queue against 256 frames/1 MiB with overflow = forced disconnect (server.py:38-39,143-158); UI retries on a fixed 1 s timer. | graph.py:99-104, 504-531; server.py; useHaloConnection.ts:126-136 | Phase 3d streams `stream_frame` at ~2 fps (~130 KB JPEG) **by spec** → deferred queue overflows in ~4 s → any webview reconnecting during a live stream is dropped mid-snapshot, retries at 1 Hz, overflows again: it can never rejoin, and each loop re-runs the full rehydrate scan. |
| 3 | **No contract versioning + UI parser hard-closes on any unknown type *or field*** (`ui/src/ipc/contract.ts:366-416`); any parse throw → `ws.close()` → 1 s retry forever (useHaloConnection.ts:106-124); `hello`/`hello_ack` carry no version. | contract.ts, useHaloConnection.ts, shared/ipc-contract.json | Every Phase 3 sub-phase adds frame types while the Brain restarts independently of a stale webview bundle (the stable launcher serves stale JS by design). One new outbound frame = infinite reconnect storm rendering as "reconnecting…" flicker, with the Brain re-running the P1-2 snapshot once per second per webview. |

### Live bugs (fix now regardless of Phase 3) — **both fixed, 2026-07-23**

Bug #4 is closed: `mic` is now listed in `_REAL_UNSUPPORTED_OPS` and answered with a
correlated `operation_unsupported` error ("Voice input is not available in the real
Brain yet"), so the affordance is honest instead of a silent no-op. Bug #5 is closed:
`graph.py:575` spawns `note_turn` through `memory._spawn`, the repo's retained-task
helper, so it can no longer be GC'd mid-flight. The rows below are kept for the
record.

| # | Finding | Evidence |
|---|---------|----------|
| 4 | **Mic mute/unmute is a silent no-op against the real Brain.** ChatView renders the mic toggle unconditionally; real dispatch drops `mic` (falls through "validated-but-unhandled"); `voice_state` is emitted **only** by the mock. No confirmation, no error, no visual change — a misleading privacy affordance. | ui/src/chat/ChatView.tsx:166-175; brain/brain/server.py:500-541; reducer.ts:107-121 |
| 5 | **`note_turn` spawned as a bare unretained `asyncio.create_task`** (`brain/brain/graph.py:469`) — the one task in the codebase bypassing the repo's own finite-ownership rule (AUDIT_PLAN / mem/Bugs.md); a GC'd task silently skips marking a conversation dirty (memory extraction lost, no error). | graph.py:469 vs. memory.py:67-72 `_spawn` |

### P2 — architectural debt that Phase 3 multiplies

| # | Finding | Evidence |
|---|---------|----------|
| 6 | Embedding inference (and first-run model download) executes **inside** the global store lock — consolidation blocks every store-touching turn per candidate. | brain/brain/store.py:34, 209-234 |
| 7 | No streaming task-output frame type — 3a's coding-agent stdout has nowhere to go except flooding `activity` (broadcast to all clients, O(n) reducer copy per line) or abusing `token`. | shared/ipc-contract.json outbound set |
| 8 | UI state: `pushActivity` copies a 10 k-entry array per frame; each `token` delta re-maps all `turns`; `turns` is unbounded. | ui/src/state/reducer.ts:174-178, 261-269 |
| 9 | Sidecar recovery cliff: backoff ladder exhausts permanently (1 s/5 s/30 s → dead); Voice exits on any disconnect with no in-process retry, so a ~40 s Brain crash-loop kills Voice for the rest of the session; no restart affordance. | ui/src-tauri/src/supervisor.rs:141-148, 203-207; voice/voice/__main__.py:63-66, 99-101 |
| 10 | LLM path: no 429/`Retry-After` handling (immediate `turn_failed`); memory-consolidation LLM calls bypass the turn semaphore entirely — under Phase 3 parallelism, background pressure fails live turns exactly when the app is busiest. | brain/brain/llm.py:260-270; brain/brain/memory.py |
| 11 | `stream_frame` (base64 JPEG) rides the shared JSON broadcast to every UI client, including windows that never render it; no subscription bit, no binary path. Direct feedstock of #2's overflow. | brain/brain/server.py:93-167; contract |
| 12 | Latent: `skill_op` has the identical real-dispatch gap as `mic` (currently unreachable — SkillsView renders no buttons without `skill_state` — but will silently recur when skills go live). Root cause: nothing asserts the **real** dispatch covers every UI-sendable type; only the mock is checked. | server.py; ui/src/skills/SkillsView.tsx:54-61 |
| 13 | Latent: v1→v2 migration is non-atomic across a crash — `executescript` implicitly commits mid-migration; a crash before `PRAGMA user_version=2` leaves a DB that crashes Brain startup with `duplicate column name` on every subsequent boot. | brain/brain/store.py:193-204 |

---

## Open-source decisions (from scan C — verdicts are mine)

**Adopt** (all verified maintained, permissive licenses, checked 2026-07-23):
- **faster-whisper** (MIT) for local STT and **Kokoro** (Apache-2.0) for local TTS in Phase 3c — the current doc picks (cloud Whisper via OpenRouter + Deepgram) contradict the PRD's local-first principle. Pipecat/openWakeWord stay as planned. Piper is **out** (GPL relicense, upstream archived).
- **browser-use** for 3b as already planned — pin the version at 3b start (API churn).

**Borrow patterns, no dependency:**
- **Anthropic SKILL.md format** (folder + YAML frontmatter + markdown + bundled scripts) as the on-disk shape for 3e-generated skills *and* 3b browser playbooks — one format decision, made before either writes its first artifact, ecosystem-portable.
- **UFO²** (Microsoft, MIT): hybrid UIA-tree-first → vision-fallback pipeline and its Picture-in-Picture virtual-desktop isolation — a concrete Lane 3 sandbox design needing no Win Pro/VirtualBox. Read before writing 3d's plan.
- **PyInstaller spec-file packaging** patterns from Jan and the `dieharders/example-tauri-v2-python-server-sidecar` reference (never `--onefile`; fastembed's ONNX runtime and sqlite-vec's loadable extension are exactly the hidden-binary-dep failure modes). Keep our supervisor (it's *more* capable than tauri-plugin-shell); borrow only the target-triple binary convention.
- **LangGraph durable-execution discipline**: audit that every side-effectful tool call sits after its gate check in the same node or carries an idempotency key — folded into Tranche B's task-runtime work.

**Skip:** mem0 / Letta / Zep-Graphiti / LangMem as dependencies — the homegrown memory store already implements their core loop locally with provenance and panel round-trips they don't model. Optional cheap borrow: Graphiti-style `valid_from`/`invalid_at` validity windows on beliefs (schema v2 already added `valid_at`/`invalid_at` — no action). OmniParser deferred until 3d proves UIA-first insufficient (AGPL model weights, GPU hosting).

---

## Implementation plan

### Tranche A — correctness fixes (small diffs, do immediately, ~1 session)

| Step | Change | Files | Gate |
|------|--------|-------|------|
| A1 | Route `mic` (and pre-emptively `skill_op`) through `_send_unsupported_operation`; add both to the `operation_kind` enum in all three contract mirrors. Alternatively/additionally: hide the mic toggle until Voice is real. | server.py, shared/ipc-contract.json, contract.ts, contract.py, ChatView.tsx | `check_contract_sync.py`; new test: real dispatch answers every UI-sendable inbound type (kills the #12 bug class permanently) |
| A2 | Retain `note_turn`: `memory._spawn(...)` instead of bare `create_task`. | graph.py:469 | existing memory tests |
| A3 | Make the v2 migration idempotent (check `PRAGMA table_info(belief)` before `ALTER`) so a mid-migration crash degrades instead of bricking startup. | store.py:193-204 | test: re-run migration on a half-migrated DB |
| A4 | Compute embeddings **before** taking `_OP_LOCK`; lock only the SQL. | store.py | test_store.py |
| A5 | LLM hardening: honor 429 `Retry-After` with one bounded retry; add a small global semaphore across **all** outbound LLM calls (turns + memory consolidation). | llm.py, memory.py | phase2_check stays green |
| A6 | Contract versioning: add `contract_version` to `hello`/`hello_ack` (refuse loudly + stop retrying on major mismatch); change the UI parser to **log-and-drop** unknown frame types/fields instead of closing the socket. | shared/ipc-contract.json, contract.ts, contract.py, useHaloConnection.ts | contract selfchecks; manual skew test (send an unknown frame, assert no disconnect) |

### Tranche B — pre-Phase-3 architecture (the gate for starting 3a)

| Step | Change | Files | Gate |
|------|--------|-------|------|
| B1 | **Design the task runtime first, as a design doc** (`systemdesign/` update): separate task workers from the 4 interactive turn slots; a `TaskContext` passed into tools carrying cooperative-cancel + progress callback; a new throttled `task_log` frame (`task_id` + `seq`, drop-not-queue semantics); real `task_op` handling (pause/resume/stop ≤2 s). Includes the idempotency audit of code-before-`interrupt()`. This one design is inherited by all of 3a–3e — get it reviewed before coding. | systemdesign/04-tasks*, 11-ipc-contract.md | design review |
| B2 | Implement the task runtime per B1. Subsumes AUDIT_PLAN's deferred `dir_organize` cancellation. | server.py, gate.py, graph.py, contract mirrors, reducer.ts | new phase2_check section: long task + concurrent chat + stop ≤2 s |
| B3 | Checkpoint hygiene: index/limit `rehydrate_pending` to threads with live interrupts; add retention (prune/archive closed threads' checkpoints). | graph.py, store/migration | test: connect time flat as thread count grows |
| B4 | Sidecar resilience: last ladder rung repeats at 30 s instead of exhausting; Voice gets an in-process reconnect loop (mirroring `useHaloConnection`) so process exit means real failure only. | supervisor.rs, voice/__main__.py | smoke_test kill/respawn scenarios |
| B5 | UI state bounds: cap in-memory turns per conversation (Brain owns full history); make the activity ring index-based instead of full-array-copy per frame. | reducer.ts, store.ts | reducer.selfcheck.ts extended |

### Tranche C — Phase-3-adjacent prep (parallel / just-in-time)

| Step | Change | When |
|------|--------|------|
| C1 | Update `techstack/00-stack-summary.md` + `02-voice` + `systemdesign/02-voice.md`: faster-whisper (local STT) + Kokoro (local TTS), cloud as fallback. | now (doc-only) |
| C2 | Decide SKILL.md as the on-disk format for skills and browser playbooks; record in `systemdesign/08` + `techstack/08`. | before 3b/3e |
| C3 | PyInstaller packaging prototype for the Brain (custom `.spec`, target-triple naming; prove fastembed/sqlite-vec load frozen). | before 3c adds model weights |
| C4 | `stream_frame` delivery design: per-client subscription bit (extend `_frame_visible_to`) or a binary side-channel; never through the broadcast fan-out. Read UFO² for 3d architecture. | with 3d planning |

## Sequencing rationale

- Tranche A is pure risk-removal with tiny diffs — no reason to defer any of it.
- B1/B2 (task runtime) dominates everything: findings #1, #7, and the idempotency risk are one design problem, and 3b–3e all inherit its shape. Designing it *after* 3a code exists would bake in the wrong contract shapes under the frozen-contract discipline.
- #2 splits deliberately: the cheap server-side hygiene (B3) lands pre-Phase-3; the streaming-specific half (C4) belongs with 3d, the only producer of that traffic.
- Doc updates (C1, C2) cost nothing now and prevent building 3c/3e against stale picks.

## Explicitly not actioned

- Memory-framework migration (mem0/Letta/Zep) — homegrown store is an asset, not debt.
- OmniParser, Claude Agent SDK, LangGraph Platform — premature; revisit per sub-phase.
- Findings already closed in AUDIT_PLAN.md (send bounds, SQLite interleaving, turn admission, Job Objects, rule-3 correlation) — verified not re-reported.
