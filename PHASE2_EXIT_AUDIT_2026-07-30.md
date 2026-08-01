# H.A.L.O. Phase 2 Exit Audit

Date: 2026-07-30  
Audited branch: `token-cost-reduction`  
Audited revision: `39f470b98f9b7b20a140a392ac06993c163f01be`  
Comparison branch: `main` at `e28be8628c6575f60ddaffe4e9f43301675c782c`

## Executive decision

**NO-GO for declaring Phase 2 complete without caveats. NO-GO for beginning Phase 3 implementation yet.**

H.A.L.O. has a strong and genuinely useful Phase 2 interactive spine. Real chat, routing, checkpointing, permission approvals, Lane-1 file tools, memory, activity, undo, snapshots, spend tracking, authenticated process transport, and broad automated verification exist and work.

However, the project does not satisfy its own formal Phase 2 exit contract:

1. The durable long-running `TaskRuntime` is explicitly design-only and `task_op` remains unsupported in the real Brain.
2. The native acceptance checklist still has blocking unchecked scenarios.
3. Four security findings remain open, including two high-severity prompt/authority boundary failures.
4. Current local `project_roots` state points only to a stale temporary directory, with no user-facing repair surface.
5. Several correctness and resilience gaps would be multiplied by every Phase 3 subsystem.

The honest status is:

> **Phase 2 feature implementation is substantially complete for short interactive turns, but Phase 2 exit hardening is not complete.**

Phase 3 planning may continue. Phase 3 implementation should wait for the exit tranche in this report.

## Evidence reviewed

- All 193 files tracked at the audited revision.
- Product, architecture, technology, UI/UX, phase, verification, audit, and project-memory documents.
- Python Brain, gate, model loop, tools, document extraction, memory, storage, IPC, secrets, and tests.
- React transport, contract parser, reducer, chat, approvals, memory, settings, task surfaces, and tests.
- Tauri process supervisor, Windows lifecycle handling, capabilities, CSP, and packaging configuration.
- Voice stub and client tests.
- Live real-Brain browser workspace with the configured OpenRouter key.
- Sealed Codex Security standard repository scan.
- `./dev.ps1 -Verify` run on 2026-07-30.
- Git branch/integration state and dependency manifests.

The review used static trust-boundary tracing, automated gates, targeted read-only state checks, and non-destructive live interaction. No exploit command or resource-exhaustion payload was executed.

## What is proven working

The official repository gate passed in one complete run:

- 32 IPC message schemas synchronized across JSON, TypeScript, and Python.
- All Brain and Voice self-check scripts passed.
- UI self-checks passed.
- 12 Vitest files and 59 UI tests passed.
- TypeScript compilation and the Vite production build passed.
- 9 Rust tests passed, including Windows job-object child reaping.
- Phase 0 authenticated transport/restart smoke passed.
- Phase 1 mock protocol gate passed.
- Phase 2 real-Brain offline protocol gate passed.

The live real-provider review also confirmed:

- A normal real model reply streamed into the UI and completed.
- A reasoning-heavy turn produced reasoning tokens and materially higher cost, consistent with escalation.
- A destructive file request produced a Tier-3 approval.
- Denial prevented the requested file creation.
- A provider-side 504 was surfaced as a recoverable in-bubble error and restored the input.

These are meaningful achievements. The decision is not a rejection of the implementation; it is a refusal to stretch short-turn evidence over long-running Phase 3 guarantees.

## Phase 2 exit-criteria assessment

| Criterion | Status | Evidence and gap |
|---|---|---|
| 1. Real chat and demonstrable light/heavy routing | **Partial** | Real streaming chat works. A reasoning-heavy live turn produced reasoning tokens and higher spend, but the selected model ID is not exposed in activity or UI, so the native checklist's “visibly escalates” requirement is not objectively closed. |
| 2. Durable, correcting, editable memory | **Partial** | Automated persistence, contradiction, decay, history, and edit/restore paths pass. The native panel edit/delete/restore checklist is still unchecked. Persisted memories and summaries are injected with `system` authority, creating a high-severity prompt-poisoning boundary failure. |
| 3. Tier 1/2/3 gate and approvals | **Core pass, security caveat** | Automated Tier 1/2/3, approve/deny/edit, suspension, and reconnect checks pass; live denial worked. Untrusted tool content can still steer approval-free Tier-2 writes, including repository-control files. |
| 4. Lane-1 local file operations | **Partial** | Read/create/edit/move/organize and command allowlisting pass automated checks. `dir_organize` is not a real cancellable task with stepped runtime progress. The current database's sole project root is stale, so ordinary project paths are unexpectedly Tier 3. |
| 5. Activity and honest undo | **Partial** | Single-operation and simple batch happy paths pass. Batch undo does not provide per-item integrity guarantees or an all-or-nothing result under partial failure, which is weaker than “one Undo restores every file.” |
| 6. Kill Brain mid-task and resume/reconcile | **Fail** | A pending approval rehydrates and resumes. A real running task does not: the task runtime is not implemented, `task_op` is unsupported, and no reconcile-first task worker exists. The test currently called “mid-task” exercises pending approval, not a running task side effect. |
| 7. Local-first and protected secrets | **Pass with hygiene work** | Production uses Windows keyring, no tracked secret was found, and loopback authentication is strong. Python dependencies are broad ranges without a lock/hashes, preventing an exact reproducible advisory baseline. |
| 8. Automated and native verification | **Automated pass; native fail/incomplete** | `./dev.ps1 -Verify` is green. The project's own `VERIFY.md` says completion also requires a native checklist with no blocking finding; multiple required items remain unchecked. |

## Blocking findings

### B0 — Implement the durable TaskRuntime

This is the primary architecture blocker.

`systemdesign/12-task-runtime.md` explicitly says the runtime is design-only and gates Phase 3a; every Phase 3 sub-phase inherits it. The real server still lists `task_op` and `lane_pin` in `_REAL_UNSUPPORTED_OPS`.

Without it, a long tool call:

- occupies an interactive turn slot;
- holds its conversation lock for the full wall-clock duration;
- has no `TaskContext` cancellation/progress/log channel;
- cannot reliably stop within two seconds;
- cannot reconcile a mid-side-effect Brain crash;
- cannot distinguish queued, running, paused, stopped, failed, and recovered work honestly.

Required closure:

1. Implement bounded task workers separate from interactive turn slots.
2. Add `TaskContext` with cancellation, pause capability, progress, and bounded logs.
3. Make real `task_op` work; return honest unsupported errors only for operations a specific task cannot perform.
4. Convert `dir_organize` and `doc_digest` first.
5. Add intent/result reconciliation so restart never blindly replays a non-idempotent side effect.
6. Test concurrent chat during a long task, stop within two seconds, and kill/restart during actual execution.

### B1 — Close the model/data/authority boundary before adding web and coding inputs

The sealed security scan reports:

1. **High:** indirect prompt injection can reach approval-free local command execution through repository-control writes and configuration-driven Git helpers.
2. **High:** persisted memory and generated summaries promote attacker-influenced content into future `system` instructions.
3. **Medium:** UI and Voice share mutation authority because role is client-declared and not server-enforced.
4. **Medium:** authenticated clients can create unbounded detached work and conversation-lock state.

Required closure:

- Treat file, document, memory, summary, browser, voice, and tool outputs as untrusted data.
- Bind mutations and external side effects to explicit current user intent, independently of model text.
- Make `.git/**`, `.gitattributes`, and equivalent control files Tier 3.
- Run Git with configuration-driven helpers disabled or inside an appropriate sandbox.
- Stop injecting generated memories/summaries with instruction authority.
- Add server-issued component identities, role-scoped credentials/capabilities, and inbound operation allowlists.
- Add bounded admission, frame limits, per-principal quotas, and idle conversation-state eviction.

These fixes must precede 3a and 3b, which deliberately ingest untrusted repositories and webpages. Role authorization must precede 3c.

### B2 — Repair current project-root state and provide a supported configuration path

The current local database contains:

```json
["C:\\Users\\vaibh\\AppData\\Local\\Temp\\tmpyocmrixx"]
```

When any `project_roots` value exists, the fallback Desktop/Documents/Downloads roots are completely replaced. No UI setting or migration repairs a stale/nonexistent root. In the live review this caused normal Desktop project files to require Tier-3 approval and blocked an honest native Lane-1 acceptance run.

Required closure:

- Reject, prune, or migrate nonexistent roots.
- Preserve safe defaults unless the user intentionally replaces them.
- Add a visible Settings surface for reviewing and repairing accessible roots.
- Isolate test databases/settings so automated runs cannot contaminate the real profile.
- Add startup and migration tests for stale roots.

### B3 — Make undo truthful under partial batch failure

The simple `dir_organize` round trip passes. The stronger exit promise does not:

- batch preconditions are not maintained per moved item;
- directory moves lack strong object identity;
- partial reversal can return normally;
- the original undo token can be consumed even when the full batch was not restored.

Required closure:

- Record a precondition/identity for every item.
- Validate all reversals before committing, or expose a clearly partial transaction with a recovery plan.
- Never label a partial batch undo as ordinary success.
- Retain or replace the undo token when recovery remains possible.
- Add collision, replacement, missing-file, and partial-failure tests.

### B4 — Repair reconnect and turn-correlation correctness

The UI currently uses arrival order as its reply correlator. `token`, `done`, and conversation errors do not identify the originating user turn. This is fragile when rapid follow-ups, reconnects, approvals, and future concurrent task output overlap.

The authoritative `conversation_history_state` is also discarded whenever local turns already exist. If the Brain committed a reply that the UI missed before disconnect, reconnect cannot repair the local projection.

Snapshot mode begins through a React effect after transport authentication; snapshot frames can arrive before that effect updates the store. `spend_update` also remains a legacy snapshot terminator despite the newer `snapshot_complete` boundary.

Required closure:

- Add a stable turn/request ID to user messages and every token/done/error frame.
- Reconcile server history against local turns instead of discarding it.
- Dispatch the authenticated/snapshot-start event synchronously at the transport boundary.
- Make only `snapshot_complete` terminate real snapshots; constrain any mock compatibility fallback.
- Add tests for rapid follow-up, disconnect after server commit, snapshot-first-frame ordering, and spend arriving mid-snapshot.

### B5 — Complete the required native acceptance pass

The following project-owned checks remain incomplete:

- visible heavy-model selection;
- real task-shaped Downloads organization with named progress and complete undo;
- real memory panel edit/delete/restore;
- actual kill-during-running-task reconciliation;
- key-missing native UI flow;
- broader human visual, keyboard-only, scaling, reduced-motion/transparency, NVDA, reconnect, and quit checks.

Automated protocol coverage cannot substitute for these because the requirements concern provider behavior, native rendering, assistive technology, and process lifecycle.

### B6 — Fix the supported browser-development launcher

`./dev.ps1 -Browser` failed in the current Codex Windows environment because `Start-Process` received an environment containing both `Path` and `PATH`, producing a duplicate-key exception. Cleanup then dereferenced a null `$brainProcess`, masking the original failure.

Required closure:

- Normalize case-insensitive environment keys before `Start-Process`, or launch without reconstructing the conflicting environment.
- Guard cleanup when process creation fails.
- Preserve and report the primary launch error.
- Add a launcher regression test for simultaneous `Path`/`PATH`.

## Important pre-Phase 3 hardening

These should be completed in the same exit tranche or immediately after its blockers:

- Bound checkpoint/transcript growth, conversation count, undo retention, summary retention, and reconnect work.
- Make memory consolidation crash-consistent and idempotent.
- Restore pending approval state if resume fails.
- Keep belief text and vector index updates consistent; reindex restored beliefs.
- Add per-task provider-call and spend budgets, especially for `doc_digest`.
- Preserve file metadata/ACL semantics or explicitly narrow the file-edit contract.
- Fix long-line file paging so no bytes are skipped.
- Make result caps byte-correct and always valid JSON.
- Repeat the supervisor's 30-second recovery rung instead of permanently exhausting.
- Bound shutdown waits and add graceful sidecar termination.
- Add Python dependency locking/hashes and an advisory scan.
- Upgrade PostCSS from 8.5.16 to a patched release. The current advisory is not reachable through untrusted runtime CSS, but a clean baseline should not carry it.
- Add CI; the repository currently has no `.github` workflow.

## Release readiness is a separate NO-GO

Release packaging was explicitly deferred from Phase 2, so it is not evidence that the interactive spine failed. It is still a hard blocker to distributing H.A.L.O.:

- the supervisor's bundled-sidecar lookup is present;
- the PyInstaller build that produces Brain/Voice executables is not present;
- `bundle.resources` does not ship those executables;
- a release build therefore falls back to Python plus a source checkout and cannot operate as a normal installed desktop product;
- installer/signing/update/CI release flows are absent.

This work can proceed in parallel with later Phase 3 sub-phases, but must be complete before any public or dependable local release.

## Documentation and integration state

Several current documents say Phase 2 is complete. That claim is too broad relative to:

- `phase-2-plan.md` criterion 6;
- `systemdesign/12-task-runtime.md` status;
- `PHASE3_READINESS_AUDIT.md`'s explicit “do not start” decision;
- `VERIFY.md`'s native completion rule.

After the exit tranche, update these documents together so “complete” has one meaning.

The audited implementation is also four commits ahead of `main`, with 114 changed files, approximately 8,021 insertions, and 728 deletions. Phase 2 should not be considered integrated until the branch is reviewed, merged, and reverified at the resulting merge commit.

## Required exit tranche and gates

### Tranche 1 — authority and task foundations

- Implement TaskRuntime and real task controls.
- Fix both high-severity prompt/authority findings.
- Add component authorization and bounded admission.
- Protect repository-control files and constrain Git.

Gate:

- security regression tests;
- long task + concurrent chat;
- stop within two seconds;
- kill during execution + reconcile;
- no model/tool output can authorize a mutation by itself.

### Tranche 2 — correctness and native operability

- Repair project-root configuration/migration.
- Make batch undo truthful.
- Add turn correlation and reconnect history reconciliation.
- Fix snapshot ordering and the browser launcher.

Gate:

- adversarial batch-undo suite;
- stale-root migration test;
- rapid-turn/reconnect/snapshot race tests;
- `./dev.ps1 -Browser` works in the Codex environment.

### Tranche 3 — acceptance and integration

- Complete every Phase 2 native checklist item.
- Run keyboard/NVDA/visual/scaling/reconnect/quit checks.
- Lock and scan dependencies; update PostCSS.
- Review and merge to `main`.
- Run the full gate at the merge commit.

Final Phase 2 tick requires:

1. `./dev.ps1 -Verify` green.
2. No open high-severity security finding.
3. No medium finding that becomes directly reachable in the first Phase 3 sub-phase.
4. Real long-task stop/restart/reconcile evidence.
5. Native checklist completed with no blocking finding.
6. Clean integrated revision on `main`.

## Progress estimate

These are feature-weighted judgment ranges, not line-count metrics:

- Phase 0: complete.
- Phase 1: complete for its mocked-shell scope.
- Phase 2 implementation: roughly **80–90%** complete.
- Phase 2 formal exit: **not complete**; the remaining 10–20% is disproportionately high-risk architecture, security, and native acceptance work.
- Whole H.A.L.O. roadmap: roughly **35–45%** complete. Phase 3 contains the heaviest capabilities—coding orchestration, browser, real voice, GUI control, and self-improvement—and release packaging remains.

The project is not “almost finished,” but it is past the prototype stage. The correct next move is one focused Phase 2 exit/hardening tranche, not a rewrite and not Phase 3 feature work.

## Final recommendation

Keep the current architecture. Do not replace the Tauri parent, authenticated loopback process model, central gate, SQLite memory, LangGraph checkpointing, or existing UI shell.

Close the task, authority, reconciliation, and native-verification gaps above; merge and reverify; then begin Phase 3a as the first independently shippable heavy subsystem.
