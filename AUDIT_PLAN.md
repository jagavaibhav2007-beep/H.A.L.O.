# Repository Audit Evidence Ledger

Date: 2026-07-22
Status: automated audit gate complete; app-scoped native lifecycle checks complete; human visual/a11y and real-key walkthroughs remain explicitly deferred.

## Scope and method

The audit covered all implemented Phase 0-2 surfaces: Tauri/Rust process ownership and windows, React/TypeScript transport/state/UI, Python Brain streaming/control/gate/files/SQLite memory, Voice authentication, IPC schema/mirrors, performance bounds, security, accessibility, tests, and operator docs. Unimplemented Phase 3 features were excluded.

Six independent read-only reviews covered native lifecycle, Brain/model flow, gate/store/files, frontend transport/state, UI/UX/accessibility, and verification/contract/Voice. A finding entered this ledger only with a concrete source path and reproducible wrong behavior. No secrets or personal file contents are recorded.

## Architecture map

| Boundary | Owner and audited responsibility |
| --- | --- |
| Native parent | `ui/src-tauri`: owns orb/workspace windows, global shortcut/tray, Brain/Voice child supervision, restart policy, persistent sidecar state, and Windows Job Object cleanup. |
| Frontend | `ui/src`: React views over a pure reducer/Zustand wrapper; `useHaloConnection` is transport-only; exact-confirmation UI locks consume typed IPC state/errors. |
| IPC | `shared/ipc-contract.json`: canonical 25-message schema; complete runtime mirrors live in TypeScript and Python; `check_contract_sync.py` compares directions, required/optional fields, types, and enums. |
| Brain | `brain/brain/server.py` authenticates/routes/snapshots and owns request lifetimes; `graph.py` owns model turns/checkpoints/interrupts; `gate.py` is the sole permission/undo choke point. |
| Persistence/tools | `store.py` serializes SQLite operations and transactions; `memory.py` owns belief policy; `tools/files.py` confines local filesystem/allowlisted command access. |
| Voice | `voice/voice`: authenticated reconnecting idle client today; real audio remains Phase 3. |

## Completed audit passes

1. Native lifecycle, process ownership, window state, hotkey/tray honesty.
2. Brain authentication, routing, concurrency, detached tasks, send/snapshot bounds.
3. LangGraph streaming, provider errors, interrupts, tool loops, history budget, HTTP pooling.
4. Permission classification, redaction, approval resume, undo races and preconditions.
5. SQLite/migrations/vector recovery, memory correction/decay/provenance atomicity.
6. Filesystem traversal/symlink/collision/read-size/command-allowlist boundaries.
7. Frontend reducer, multi-conversation drafts, reconnect reconciliation, exact control confirmation.
8. UX/accessibility focus, dialogs/live regions, clipping, copy honesty, semantic contrast.
9. IPC malformed-frame/direction/type drift, Voice authentication, false-green test gaps.
10. Integrated automated gate plus app-scoped native startup/authentication and forced-parent cleanup.

## Baseline and current evidence

Pre-change baseline checks passed for the UI production build, four Rust tests, IPC synchronization (25 types), and the then-existing three Vitest tests. These are baseline evidence only, not post-audit proof.

Final current-tree `./dev.ps1 -Verify` evidence from 2026-07-22:

- `FULL AUTOMATED VERIFICATION PASSED` in Windows PowerShell.
- Complete IPC drift/validation check passed for all 25 schemas.
- Every discovered Brain and Voice Python test script passed.
- All five TypeScript self-checks passed; Vitest passed 8 files / 16 tests.
- Production UI build passed: 2,004 modules, 429.20 kB JavaScript (133.24 kB gzip), 31.58 kB CSS (6.20 kB gzip), 2.68 s Vite build in the recorded gate.
- Rust passed 7 tests, including minimized-window behavior, sidecar snapshot persistence, shutdown spawn handling, and Windows Job Object child termination.
- Phase 0 smoke, Phase 1 mock E2E, and Phase 2 real-Brain offline E2E all passed in the same run.

App-scoped native evidence: `./dev.ps1 -Mock` launched the real Tauri process, Brain and Voice both authenticated, and the orb rendered at its native 360x52 surface. A forced kill of exact UI PID 28628 reaped exact Voice PID 10088 and Brain PID 35148 within three seconds. Whole-desktop capture was deliberately not used. WebView accessibility, 720x480 visual inspection, NVDA, and real-key OpenRouter behavior remain manual checks, not claimed passes.

## Verified P1 findings

| Finding | Disposition and focused evidence |
| --- | --- |
| Search patterns and Tier-1 `dir`/`ls`/`git diff` operands escaped configured roots. | Fixed in `brain/brain/tools/files.py`: pattern/result checks, root-constrained command CWD/operands, dangerous Git mode rejection. `brain/tests/test_files.py` passed. |
| Tier-2 create could overwrite a file created after classification; delete undo could overwrite a replacement. | Fixed with exclusive create and `must_be_absent` undo precondition in `files.py`/`gate.py`. File regressions passed. |
| Failed undo consumed its token and emitted success. | Fixed in `gate.py`/`store.py`: success activity only on `ok`; failed inverse releases the claim and emits `undo_failed`. `test_undo.py` passed. |
| OpenRouter in-band stream errors were checkpointed and reported as `done`. | Fixed with `OpenRouterStreamError` in `llm.py` and honest graph termination. `test_graph.py::check_midstream_error_honesty` passed. |
| Stop waited for another provider delta and could stall for the network timeout. | Fixed by racing each iterator read against `stop.wait()` and closing the stream. The two-second stalled-stream regression passed. |
| One SQLite connection allowed transaction interleaving across worker threads. | Fixed with a re-entrant complete-operation lock in `store.py`; candidate add + provenance check + supersede + vector bookkeeping is now one rollback-safe transaction. Store/memory failure-injection regressions and Phase 2 E2E passed. |
| Forced Tauri death could orphan Brain/Voice. | Fixed with a Windows kill-on-close Job Object in `supervisor.rs`/`Cargo.toml`. Rust termination test and an app-scoped native force-kill of exact UI/Brain/Voice PIDs passed. |
| Enabled tray Pause/Mute items had empty handlers; real task controls could wait forever. | Removed unsupported tray entries. Non-mock `task_op`/`lane_pin` now return exact correlated `operation_unsupported` errors, immediately unlocking controls and showing the failure. Backend and UI regressions passed. |
| Summoning a minimized workspace could hide it. | Fixed with minimized detection, unminimize/show/focus and a Rust decision test; current native matrix pending. |
| Sidecar terminal error was a one-shot event lost on WebView reload. | Fixed with revisioned managed state, `sidecar_snapshot`, and event-then-snapshot hydration. Native reload verification pending. |
| Pre-token errors were invisible; rapid follow-up split/stranded the active assistant turn. | Fixed in `ui/src/state/reducer.ts`; focused reducer tests and the final 16-test Vitest suite passed. |
| Reconnect retained finished tasks, duplicated activity, and could erase a pending approval. | Fixed with snapshot-boundary reconciliation and replay matching in `reducer.ts`; focused tests added, current Vitest/native two-WebView check pending. |
| Rule-3 locks treated unrelated/stale object upserts as confirmation, and global errors could strand controls. | Fixed with exact confirmation predicates plus typed `operation_kind`/`operation_id` errors across schema, backend, reducer, and UI. Hook/reducer/backend tests and complete contract sync passed. |
| Approval cards were clipped and not announced/focused. | Fixed with bounded scrolling, `alertdialog` labels, initial focus, and restoration in `ApprovalCard.tsx`/CSS. Focused automated test passed; NVDA/720x480 remain manual. |
| Voice auth test passed merely because the client stayed pending. | Fixed with an explicit post-`hello_ack` event and black-hole-server regression. Voice script passed. |
| Keystore failures stranded Settings without a confirming frame, and invalid/unverified status became `set` after reconnect. | Fixed in `server.py`/`secrets_store.py`: set/delete/status failures return `settings_state:invalid` plus a recoverable error without key material; validation status persists. `test_server.py::check_settings_failures_and_status_persistence` passed. |
| Snapshot sends could hang and deferred broadcasts could grow without a bound. | Fixed with `_send` timeout plus frame/byte caps and overflow disconnect in `server.py`. `check_sends_and_deferred_queue_are_bounded` passed. |
| Detached request/background tasks were not retained or supervised. | Fixed with `_ServerRuntime`, managed shutdown, observed exceptions, terminal `request_failed`, and cancellation of handlers/decay work. `check_tasks_are_supervised_and_cancelled_on_close` passed. |
| Synchronous tools blocked the asyncio server loop. | Fixed by running synchronous tool functions with `asyncio.to_thread` while keeping coroutine tools on-loop. A deterministic heartbeat regression passed. |
| Distinct conversations created unbounded concurrent turns and retained idle lock entries. | Fixed with a four-turn semaphore and reference-counted, cancellation-safe lock leases. Stress regression proved a cap of two under its test semaphore, same-conversation order, and zero idle entries. |

## Verified P2 findings

| Finding | Disposition and focused evidence |
| --- | --- |
| History budgeting omitted tool-call arguments. | Fixed by budgeting the serialized provider payload in `graph.py`; current snapshot script passed. Add a dedicated large-argument assertion. |
| Every model round discarded HTTP pooling. | Fixed with a shared lifecycle-managed `httpx.AsyncClient` in `llm.py`; graph/router scripts passed. Benchmark remains pending. |
| A transient sqlite-vec setup failure could permanently omit `belief_vec`. | Fixed by verifying/creating the table whenever the extension is available. Store script passed; two-start recovery regression remains desirable. |
| File limits followed whole-file reads or whole-directory sorting. | Fixed with streaming hash, cap+1 reads, bounded delete buffering, and bounded selection. File script passed; peak-RSS benchmark pending. |
| Unknown-tool approval redaction exposed arbitrary arguments. | Fixed by redacting unknown tools to `{}` in `gate.py`; gate script passed. |
| IPC runtime checks/drift comparison omitted field types and directions. | Fixed by canonical field/direction metadata and mirrored Python/TS validators; complete sync and both contract self-checks passed for all 25 types. |
| Failed-input restoration reused another conversation's text. | Fixed with per-conversation drafts/last-sent values in `ChatView.tsx`; focused and full Vitest suites passed. |
| Hotkey fallback lacked cause/recovery guidance. | Fixed with structured native hotkey status and a status-strip notice; occupied-hotkey native tests pending. |
| Chat status/error changes and the composer label were not reliably exposed to assistive technology. | Production changes and focused tests exist in `ChatView`; current Vitest/NVDA checks pending. |
| Settings showed mock model identities in real mode and treated unhydrated key status as missing. | Fixed in `SettingsView.tsx` with truthful routing copy and an explicit checking state. `SettingsView.test.tsx` passed in the refreshed Vitest run. |

## Remaining findings and explicit deferrals

No validated P0 or P1 finding remains unresolved.

- **P2 organize cancellation/progress â€” deferred to the real task runtime.** `dir_organize` is bounded to 200 moves and runs off-loop, but it cannot cooperatively cancel or emit per-step live progress because Phase 2 tool functions do not receive a task-control/broadcast context. Adding that context would be a new task-runtime capability, overlapping Phase 3 rather than a minimal bug fix. The current UI receives an honest correlated unsupported error for task controls instead of hanging.
- **P2 quantitative performance baselines â€” follow-up.** This audit verified hard bounds (four simultaneous real turns, 256 deferred frames / 1 MiB, capped file reads/listings, 10,000 activity rows) and recorded build size/time. It did not manufacture before/after latency claims for HTTP pooling or streaming; production OpenRouter/network benchmarks need a real key and controlled network conditions.
- **P2 native visual/accessibility matrix â€” manual.** Focus/dialog/live-region behavior has automated coverage and light-theme contrast measured 6.63:1 (Tier 3) and 5.68:1 (success). The environment did not expose WebView descendants through Windows UI Automation, so NVDA, keyboard-only flows, 200% scaling, reduced motion/transparency, and 720x480 clipping remain unchecked in `VERIFY.md`.
- **P2 real-provider behavior â€” external input required.** A real OpenRouter key was neither available nor requested or exposed. Router escalation, billing, and live provider streaming remain the documented real-key walkthrough.

## Primary-source cross-checks

- OpenRouter in-band stream errors and cancellation: [Errors and debugging](https://openrouter.ai/docs/api/reference/errors-and-debugging), [Streaming](https://openrouter.ai/docs/api/reference/streaming).
- HTTP connection reuse: [HTTPX async support](https://www.python-httpx.org/async/).
- Child/job ownership: [Rust `Child`](https://doc.rust-lang.org/std/process/struct.Child.html), [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
- Window restoration: [Tauri `WebviewWindow`](https://docs.rs/tauri/latest/tauri/webview/struct.WebviewWindow.html).
- Retaining task references: [Python asyncio tasks](https://docs.python.org/3.12/library/asyncio-task.html).
- Accessibility criteria: [WAI-ARIA dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/), [WCAG contrast](https://www.w3.org/TR/WCAG22/#contrast-minimum), [status-message failure F103](https://www.w3.org/WAI/WCAG21/Techniques/failures/F103), [form labels](https://www.w3.org/WAI/tutorials/forms/instructions/#placeholder-text).

## Exact gates and remaining manual verification

Canonical full gate from the repository root:

```powershell
./dev.ps1 -Verify
```

It is intended to execute, in order:

```powershell
python shared/check_contract_sync.py
Get-ChildItem brain/tests/test_*.py | Sort-Object Name | ForEach-Object { python $_.FullName }
Get-ChildItem voice/tests/test_*.py | Sort-Object Name | ForEach-Object { python $_.FullName }
Get-ChildItem ui/src -Recurse -Filter *.selfcheck.ts | Sort-Object FullName | ForEach-Object { node $_.FullName }
Push-Location ui; npm test; npm run build; Pop-Location
Push-Location ui/src-tauri; cargo test; Pop-Location
python shared/smoke_test.py
python shared/phase1_check.py
python shared/phase2_check.py
```

Protocol-only check: `./dev.ps1 -Smoke`. If `python` is not the project Python, activate the project environment or prepend its directory for that process; never commit a machine-specific runtime path.

The automated gate and app-scoped force-kill/no-orphan check pass. Complete the remaining unchecked human items in `VERIFY.md`: minimized/hotkey interaction, 720x480, keyboard/NVDA, reconnect/two-WebView visual behavior, and the real-key Phase 2 walkthrough. Record date, commit, Windows/WebView2 version, tester, and findings.

## Next recommended audit order

1. Run the remaining human visual/NVDA matrix in `VERIFY.md` on the current commit.
2. Run the real-key provider/router/spend walkthrough without recording the key.
3. Before Phase 3a, design a real task runtime context (progress, cooperative cancellation, truthful capabilities); migrate `dir_organize` onto it rather than adding one-off callbacks.
4. Add controlled production-network latency/cost benchmarks only when a stable key, provider, model, and network baseline are available.
5. Repeat security boundary and native lifecycle audits after any Phase 3 browser/GUI/subprocess capability lands.

## Exit criteria

1. Fix or explicitly defer every validated finding with evidence and rationale. **Met.**
2. `./dev.ps1 -Verify` prints `FULL AUTOMATED VERIFICATION PASSED`. **Met 2026-07-22.**
3. Every fixed P0/P1 failure has a negative-path regression. **Met.**
4. App-scoped native startup/authentication and forced-parent-death cleanup pass; human-only and real-key checks remain accurately unchecked. **Met for automatable scope.**
5. `git diff --check`, secret review, and final docs/memory reconciliation pass. **Met 2026-07-22; only Git's existing LF-to-CRLF notices were emitted.**
