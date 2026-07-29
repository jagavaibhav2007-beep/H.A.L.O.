# Phase 3 Readiness Audit & Implementation Plan

**Date:** 2026-07-23 · **Author:** Claude Fable 5 (decision-maker), synthesizing three parallel subagent scans: (A) system-design ceilings, (B) whole-repo bug/UI-connection review, (C) open-source landscape research. Scope excludes everything the 2026-07-22 hardening audit ([AUDIT_PLAN.md](AUDIT_PLAN.md)) already closed.

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
