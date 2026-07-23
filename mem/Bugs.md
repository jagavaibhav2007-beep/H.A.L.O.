# Bugs

## Mic/skill_op sent to the real Brain were silently dropped, no confirmation, no error - 2026-07-23
**Severity:** High (misleading privacy affordance). Found during the Phase 3 readiness audit's whole-repo bug scan.
**Symptom:** the chat mic mute/unmute button and skill-panel actions call `sendMic`/`skill_op` unconditionally; the real (non-mock) Brain's dispatch only ever handled `user_msg`/`settings_update`/`interrupt`/`approval_response`/`memory_edit`/`undo` and fell through to nothing for `mic`/`skill_op` — no reply frame at all. `voice_state` (the only thing that would confirm a mute toggle) is emitted exclusively by `mock.py`. Against the real Brain the mic button looked functional and did nothing, with zero user-visible feedback.
**Root cause:** `task_op`/`lane_pin` had already been given honest `operation_unsupported` handling in the 2026-07-22 audit, but that fix was never extended to every UI-sendable inbound type — `mic`/`skill_op` were added to the contract later without a matching real-dispatch branch.
**Fix:** `server.py`'s `_REAL_UNSUPPORTED_OPS` now includes `mic`/`skill_op`, routed through the same `operation_unsupported` correlation path; `operation_kind` enum extended in all three contract mirrors; ChatView disables the mic control with an honest reason once the error lands. A new test (`test_server.py`) enumerates every inbound type in the contract spec and asserts the real dispatch answers all of them — not just the mock.
**Never do:** add a new inbound IPC type without wiring (or explicitly, honestly refusing) it in the REAL dispatch table, not just the mock. "Mock handles it" is not evidence the real Brain does.

## `note_turn` spawned as a bare unretained `asyncio.create_task` - 2026-07-23
**Severity:** Medium (silent data loss, low trigger rate). Found during the Phase 3 readiness audit.
**Symptom:** `graph.py`'s post-turn hook fired `asyncio.create_task(memory.note_turn(...))` directly — the one background task in the codebase not held by any tracking set. asyncio only keeps a weak reference to an unretained task; if garbage-collected before it completes, the conversation silently never gets marked dirty and its memory extraction is lost, with no error anywhere.
**Root cause:** `memory._spawn()` (a set + done-callback, used everywhere else for idle/pressure consolidation tasks) already existed and was the established pattern — this one call site was added without going through it, breaking the "every task has an explicit owner" rule from the 2026-07-22 audit.
**Fix:** changed to `memory._spawn(memory.note_turn(...))`. Added `check_note_turn_triggers` to `test_memory.py`, which asserts the armed idle timer is present in `memory._bg_tasks` — this specific assertion is what would fail if the bare-`create_task` regression recurred.
**Never do:** call `asyncio.create_task` directly in `brain/`. Always route through the module's existing `_spawn`/task-tracking helper so every background task has retention + a done-callback.

## v1→v2 belief-table migration could brick Brain startup after a crash - 2026-07-23
**Severity:** Medium (low real-world trigger rate; needs a pre-existing v1 DB + a crash in a narrow window). Found during the Phase 3 readiness audit.
**Symptom:** the `version < 2` migration branch ran `ALTER TABLE belief ADD COLUMN valid_at/invalid_at` then `conn.executescript(_V2_NEW_TABLES)` inside what looked like one `with conn:` transaction — but `executescript()` implicitly commits first. A crash after that implicit commit but before `PRAGMA user_version=2` left the columns added with `user_version` still `1`; the next Brain start re-entered the same branch and re-ran the `ALTER`, raising an uncaught `sqlite3.OperationalError: duplicate column name` that failed `store.connect()` — Brain would not start at all.
**Root cause:** treated `executescript` as participating in the surrounding transaction the same way plain `execute` calls do; it does not.
**Fix:** the branch now checks `PRAGMA table_info(belief)` for each column before its `ALTER TABLE`, making a re-entry into a half-migrated state a no-op instead of a crash. Test added: hand-build the half-migrated state (columns present, `user_version` still 1) and assert `connect()` succeeds.
**Never do:** assume `executescript()` shares the transaction of surrounding `execute()` calls inside a `with conn:` block — it commits implicitly. Any multi-step SQLite migration must be safe to re-enter after a crash between its own statements, not just written once and trusted to run atomically.

## Audit: permission-boundary and undo races in Lane-1 file tools - 2026-07-22
**Severity:** High. **Cause:** search trusted glob patterns, Tier-1 commands accepted outside-root operands, create classification raced an overwriting write, and delete undo restored without an absence precondition. **Fix:** validate resolved patterns/results/CWD/operands, reject dangerous Git modes, create exclusively, and require the restore target to remain absent. `brain/tests/test_files.py` passed. **Prevention:** NEVER treat a command name or pre-execution classification as the filesystem boundary; validate every resolved operand and re-check destructive preconditions at execution.

## Audit: stream termination lied or waited on a stalled provider - 2026-07-22
**Severity:** High. **Cause:** in-band provider errors were ignored and interrupt checked only after another delta. **Fix:** typed stream errors now terminate with `error`; every blocked iterator read races `stop.wait()` and closes on stop. Focused graph checks passed. **Prevention:** NEVER equate HTTP 200/SSE completion with model success, and never make cancellation depend on the producer yielding again.

## Audit: reconnect replay used append-only live semantics - 2026-07-22
**Severity:** High. **Cause:** snapshot absence retained stale tasks, replay duplicated activity or hid approval, and object identity masqueraded as confirmation. **Fix:** snapshot-boundary reconciliation/replay matching plus operation-specific confirmation predicates. Focused UI tests and the completed full gate pass (Vitest 16/16). **Prevention:** NEVER apply replay as live append-only traffic; define boundary, identity, absence handling, and exact confirmation.

## Audit: sidecars had graceful cleanup but no forced-parent ownership - 2026-07-22
**Severity:** High. **Cause:** Rust `Child` drop does not kill the process, so forced Tauri death bypassed exit cleanup. **Fix:** assign sidecars to a Windows kill-on-close Job Object while retaining graceful shutdown. **Prevention:** NEVER claim crash-safe child ownership from an exit callback alone; force-kill and verify no child, lock owner, or listening port remains.

## Audit: shared SQLite operations could interleave and belief correction could half-commit - 2026-07-22
**Severity:** High. **Cause:** one `sqlite3.Connection(check_same_thread=False)` was shared by concurrent async turns without serializing complete operations, and belief insert/provenance/supersession/vector bookkeeping crossed transaction boundaries. This allowed transaction overlap and a correction to become only partly durable after a later failure. **Fix:** `brain/brain/store.py` now owns a re-entrant operation lock and performs candidate-belief creation and its related writes in one rollback-safe transaction; vector-table setup is verified/recreated when the extension is available. **Prevention:** `check_same_thread=False` permits cross-thread use but does not make a connection or multi-statement invariant concurrent-safe; lock the whole logical operation and commit once.

## Audit: backend work lacked finite ownership and admission bounds - 2026-07-22
**Severity:** High. **Cause:** detached request/decay tasks were not retained for shutdown, synchronous tools could block the event loop, outbound sends and deferred snapshots had no complete bounds, real turns admitted without a global cap, and conversation-lock entries were never reclaimed. **Fix:** `_ServerRuntime` supervises tasks and cancels them on shutdown; sync tools run through `asyncio.to_thread`; sends/snapshots are time- and size-bounded; real turns use a four-slot semaphore; reference-counted conversation lock leases reclaim idle entries without weakening same-conversation ordering. **Prevention:** every task, queue, send, snapshot, lock registry, and expensive operation needs an explicit owner, limit, cancellation path, and shutdown behavior.

## Audit: unsupported task controls left the UI pending forever - 2026-07-22
**Severity:** High. **Cause:** Phase-1 controls could send `task_op`/`lane_pin` to the real Phase-2 Brain even though no real task runtime existed; there was no operation-specific failure correlation, so generic errors could not safely unlock the exact pending control. **Fix:** the real Brain returns an `operation_unsupported` error carrying paired `operation_kind` and `operation_id`; both IPC mirrors validate the pair, the reducer records correlated failures, and `usePendingConfirm` unlocks and displays the exact failure. **Prevention:** never acknowledge or silently ignore an executable-looking control that has no backend implementation; return a typed, correlated terminal failure.

## Audit: keystore failures were rendered as trustworthy key status - 2026-07-22
**Severity:** High. **Cause:** secret-backend failures could be collapsed into ordinary missing/set state or expose backend details through logs, making Settings look authoritative when the OS keystore was unavailable. **Fix:** strict status checks propagate backend failure to the server, which emits an invalid/unverified state plus a recoverable error; secret/backend logs are sanitized to exception class only. `get_key()` may still degrade to `None` at the model boundary so chat fails safely. **Prevention:** distinguish “missing,” “present,” and “could not verify”; never turn an observability failure into a confident security-state claim.

## Tool results never reached the model — every data-returning tool read "I ran X." - 2026-07-22
**Severity:** High (silent confabulation). Found live: user asked Halo to find `name.pdf` on the Desktop; the file existed, but Halo "searched" and claimed no such PDF exists.
**Symptom:** `file_search`, `dir_list`, `file_read`, `run_readonly_cmd` executed correctly but their return values never got back to the LLM. A search that found the file and one that found nothing both produced the *same* tool message, so the model confabulated "not found."
**Root cause:** `gate.py` `_execute_tail` captured the tool's return `out` but on success hardcoded `content = f"I ran {tool}."`, using `out` only for the undo-inverse builder. `graph.py` `_gate_node` wraps exactly that string as the `role:"tool"` message fed back to the model — so the result was structurally discarded. No test caught it: `test_toolcall.py`/`test_files.py` asserted tools *executed* and *some* text came back, never that the result *propagated*. Contributing: the system prompt never told the model the accessible roots, and `_resolve` anchored bare relative paths at the Brain's CWD (outside roots → empty glob / spurious Tier-3).
**Fix:** `_execute_tail` now serializes `out` (`json.dumps(..., default=str)`, capped at 8KB) into `content` for every non-None return — `I ran file_search. Result: [...]`; `out == []` reads `Result: [] (no matches).`; None-returning tools (e.g. `file_delete`, whose prior bytes ride `args["_prior"]` not the return, so nothing sensitive leaks) keep the bare ack. One mechanism in one place; redactors only touch *args* (activity feed), never return values, so nothing needed excluding. `_resolve` now anchors bare/relative paths at `Path.home()`. `graph._roots_note()` appends the accessible roots to the system prompt per turn (derived from `_roots()`, no hardcoded path). Tests now assert the return value appears in the `tool` message content and that `[]` reads as "no matches."
**Never do:** never return a content-free acknowledgement ("I ran X.") for a tool that returns data — the model only ever sees that string. When testing a tool path, assert the result *propagated* into the message the model receives, not merely that the tool executed and the status was "ok".

## Broadcast could hang every conversation on one stalled-but-open client - 2026-07-17
**Severity:** Medium. Found by subagent bug-hunt, not live testing.
**Symptom:** none observed yet — a latent hang. `server.py`'s `_broadcast` awaited each client's `.send()` sequentially with no timeout, only catching `ConnectionClosed` (a closed socket), not a stalled-but-open one (peer stopped reading, OS send buffer fills). Since `_broadcast` runs inside the per-conversation `asyncio.Lock`, a stuck send there froze every future message on every conversation.
**Fix:** wrap each client send in `asyncio.wait_for(..., timeout=5)`; treat `TimeoutError` the same as `ConnectionClosed` (drop the client from routing).
**Never do:** await an unbounded per-client send inside a shared lock — bound every fan-out send with a timeout, or gather with per-client timeouts, so one slow consumer can't freeze the rest.

## Mock task_state confirmations carried the wrong task's title/lane/progress - 2026-07-17
**Severity:** High. The UI reducer replaces a task wholesale on `task_state` (no merge), so any handler that broadcasts a partial/hardcoded payload corrupts the card.
**Symptom:** pausing/stopping/lane-pinning any task other than the hardcoded seed relabeled the card and wiped its progress; a reconnect snapshot re-seeded fresh state (mock kept no live task registry), discarding real mutations and diverging between the orb/workspace windows.
**Root cause:** `handle_task_op`/`handle_lane_pin`/`handle_interrupt` each hardcoded fields instead of reading/merging the task's actual state; `push_snapshot` called `_seed_tasks()` fresh instead of reading live state.
**Fix:** added a live `_tasks: dict` registry (same pattern as `_beliefs`/`_skills`) plus an `_emit_task(broadcast, patch)` helper that merges a partial patch onto the stored task and broadcasts the complete object; every task_state emit site now routes through it; `push_snapshot` reads `_tasks.values()`.
**Never do:** broadcast a partial state object to a client whose reducer replaces (not merges) on that object's id — either merge server-side before sending, or never send an incomplete object.

## Denied destructive approval in "demo everything" reported as done - 2026-07-17
**Severity:** Low-Medium (safety-relevant surface). `_scenario_everything`'s destructive-delete branch checked only `"cancelled"`, not `"deny"`, so denying the file-delete approval still broadcast `task_state: done` — a denied destructive action was shown as completed successfully.
**Fix:** added an explicit deny branch (mirrors the sibling `_scenario_approval`/`_scenario_task` pattern) that pauses the task with a "you denied a step" reason instead of falling through to done.
**Never do:** when one branch of a decision (`approve`/`deny`/`edit`/`cancelled`) is handled, verify every sibling scenario handling the same decision enum handles all branches too — copy-pasted approval flows are exactly where one gets missed.

## Rapid sequential memory deletes silently dropped all but the last - 2026-07-17
**Severity:** High (silent data-loss of explicit user intent). `MemoryView.tsx`'s `requestDelete` used one `deleteTimer`/`pendingDelete` slot; clicking Delete on belief A then B within the 5s undo window cancelled A outright with no local mutation (by design) and no error — A just silently stayed in the active list while the toast showed B.
**Fix:** before arming a new pending delete, flush (send) any delete already pending for a *different* belief. The flush side-effect stays outside the `setState` updater per the StrictMode rule (see "Rule-3 unlock on confirm" below).
**Never do:** let a single-slot "debounce pending action" pattern silently cancel a *different* target's pending action — either queue, or flush-before-replace.

## Memory history-row Restore showed no lock/pending feedback - 2026-07-17
**Severity:** Low (feedback-only; `usePendingConfirm`'s dedup still prevented an actual double-send). The superseded-history Restore button read the *active* belief's `pending[belief_id]`, not the history row's own id, so it never disabled or showed "Restoring…".
**Fix:** pass a per-id `pendingFor(id)` lookup into `BeliefCard` so each history row reads its own pending state.
**Never do:** when a card renders controls for more than one entity (an active belief + its history rows), make sure each control's disabled/label state keys off *that entity's* id, not the card's primary prop.

## Mock turn path had no turn_failed recovery (asymmetric with the real path) - 2026-07-17
**Severity:** Low (defensive; no live trigger found — every mock-reachable field is already validated). The non-mock echo turn wraps its work in try/except and emits a recoverable `turn_failed` error frame on exception; the mock dispatch branch called `mock_engine.handle_user_msg` unguarded, so an unexpected exception there would drop the turn silently (UI waits on a `done` that never comes).
**Fix:** wrapped the mock branch in the same try/except/turn_failed pattern as the non-mock path.
**Never do:** add a second code path that handles the same message type as an existing path without checking it has the same failure-recovery guarantees.

## Workspace-sync events repeatedly reloaded the floating window - 2026-07-17
**Severity:** High.
**Symptom:** while Codex edited the dirty worktree, the capsule repeatedly disappeared and reopened; the terminal reported many Vite HMR updates and Tauri rebuilds for files whose contents had not changed. Repeated `./dev.ps1 -Mock` calls could also leave detached launchers competing for port 1420.
**Root cause:** the workspace bridge rewrites timestamps for multiple dirty files during a patch. The default launcher combined Vite and Tauri file watchers with `Start-Process -NoExit`, so metadata-only events restarted both WebViews and `ui.exe`, while the detached parent made duplicate sessions easy to create.
**Fix:** `./dev.ps1` now stays attached, holds the `Local\HaloDevLauncher` named mutex, and defaults to a stable one-time `vite build` served by `vite preview` plus `tauri dev --no-watch`. `-WatchNative` explicitly restores normal Vite and Rust hot reload.
**Never do:** run watcher-heavy detached dev trees while an automated workspace bridge is rewriting a dirty worktree; use stable mode for agent-driven edits and opt into watchers only for interactive development.

## Capsule CSS left the native floating window rectangular - 2026-07-17
**Severity:** Medium.
**Symptom:** the floating companion drew a correct pill border, but its desktop window retained visible rectangular corners and rectangular native hit bounds.
**Root cause:** `.capsule { border-radius: 999px; overflow: hidden; }` clipped only DOM content; it did not change the Windows HWND region, and the WebView had no explicit zero-alpha background color.
**Fix:** set the orb window `backgroundColor` to `#00000000`, apply a height-radius Win32 `SetWindowRgn`, and reapply the region after resize/DPI changes.
**Never do:** treat CSS rounding as proof that a transparent, borderless native window has non-rectangular paint and hit-test bounds.

## Repeated sidecar poll errors could abandon a live child - 2026-07-17
**Severity:** High.
**Symptom:** any `Child::try_wait()` error was treated as process completion, so supervision could clear its shared handle and spawn a replacement while the original process was still alive.
**Root cause:** the poll loop collapsed `Ok(Some(status))` and `Err(error)` into the same `done` branch.
**Fix:** reset an error counter after successful polls, retry transient failures, and after three consecutive failures kill and wait for the owned child before entering restart backoff. If termination fails, retain ownership and continue polling.
**Never do:** interpret an inability to query process state as evidence that the process exited; preserve ownership until exit is observed or termination is confirmed.

## Mock snapshot disconnect bypassed authenticated-client cleanup - 2026-07-17
**Severity:** Medium.
**Symptom:** a UI connection that closed while its mock reconnect snapshot was being sent could remain in the authenticated-client routing map.
**Root cause:** `push_snapshot()` ran after registration but before the `try/finally` that removes the socket.
**Fix:** move snapshot delivery inside the same `try/finally` as the receive loop so every post-registration exit removes the connection.
**Never do:** register a connection before an awaited operation unless every subsequent await is inside the registration's cleanup scope.

## Fallible window setup ran after sidecars started - 2026-07-17
**Severity:** Medium.
**Symptom:** if hotkey, tray, or other window setup failed, Tauri setup returned an error after Brain and Voice supervisor threads had already started, creating avoidable partial-startup ownership risk.
**Root cause:** `sidecars.start()` preceded the fallible `windows::setup()` call.
**Fix:** complete `windows::setup()` first and start sidecars only after it succeeds.
**Never do:** start long-lived child processes before required fallible application initialization has completed.

## Active Phase 1 controls again outgrew runtime IPC validation - 2026-07-17
**Severity:** High.
**Symptom:** malformed memory/skill identifiers crashed detached handlers, unknown operations were silently accepted, and an invalid mic operation was treated as unmute.
**Root cause:** the typed contract gained executable `memory_edit`, `skill_op`, `lane_pin`, and `mic` handlers without extending both runtime validators beyond required-field presence.
**Fix:** mirrored string/enum/optional-field validation in Python and TypeScript, including rejecting boolean lanes, with malformed-frame regressions.
**Never do:** make an inbound control executable until every field it uses for lookup or branching is runtime-validated in both mirrors.

## Activity rule-3 boundaries broke at the ring-buffer cap - 2026-07-17
**Severity:** Medium.
**Symptom:** at 10,000 activities an Undo could remain pending forever and the new-activity pill stopped appearing because the array length no longer increased.
**Root cause:** both behaviors used array length as an arrival boundary even though the reducer drops the oldest item at the cap.
**Fix:** track the newest activity message id and treat a missing boundary as evicted; a focused self-check covers retained and evicted boundaries.
**Never do:** use collection length as a monotonic event cursor for a capped collection.

## Non-primary pointer buttons could approve destructive actions - 2026-07-17
**Severity:** High.
**Symptom:** holding right-click or middle-click for 700 ms triggered the destructive approval callback.
**Root cause:** the hold button started on every `pointerdown` without checking the primary pointer or button.
**Fix:** only start the hold for the primary pointer's left button; keyboard hold behavior is unchanged.

## Oversized restored windows could panic during off-screen clamping - 2026-07-17
**Severity:** Medium.
**Symptom:** an off-screen window larger than the selected monitor produced an invalid `Ord::clamp` range during startup.
**Root cause:** the computed maximum coordinate could be less than the monitor-origin minimum.
**Fix:** clamp each maximum to at least the monitor origin and cover oversized, negative-origin, and ordinary cases with Rust unit tests.

## Rule-3 "unlock on confirm" silently never fired in Tasks/Memory/Skills views - 2026-07-13
**Severity:** High.
**Symptom:** clicking Pause/Delete/Restore/etc. correctly locked a card's buttons, but the buttons stayed disabled forever even after the Brain's confirming frame landed — verified live only for Skills (Tasks/Memory had never actually been tested past the lock step, only the lock itself).
**Root cause:** the "clear pending when a fresh object reference confirms" effect mutated a `useRef` **inside** the function passed to `setPending(prev => ...)`. React 18 StrictMode (active via `<React.StrictMode>` in `main.tsx`) double-invokes function-form state updaters in dev specifically to catch impure updaters — the first invocation mutated the ref as a side effect, so the second invocation (whose return value React actually keeps) compared the store's new value against the ref it had *just been mutated to*, saw no difference, and returned the unmodified `prev` — the pending entry never cleared.
**Fix:** capture the ref's old value into a local (`const prev = prevRefs.current`) and mutate the ref *before* calling `setPending`, then have the updater close over that immutable local instead of reading/writing the ref itself — makes the updater a pure function of its closure, safe under double-invocation. Same fix applied identically in `TasksView.tsx`, `MemoryView.tsx`, `SkillsView.tsx`.
**Never do:** mutate a `ref.current` (or any other side effect) from inside a function passed to `setState`/`setX(prev => ...)` — StrictMode's double-invoke exists precisely to catch this, and it silently produces a stuck-locked UI rather than a crash, so it's easy to ship unnoticed if the unlock path isn't actually exercised end-to-end.

## Skill snapshot entries appeared as new-skill orb peeks - 2026-07-12
**Severity:** Medium.
**Symptom:** seeded skills such as `flaky-scraper` could appear beside the orb as if Halo had just learned them during startup or reconnect.
**Root cause:** `usePeekSource` diffed incrementally arriving `skill_state` snapshot frames; after the first frame seeded its seen-set, later frames in the same snapshot looked like new skills.
**Fix:** removed the redundant skill-diff heuristic; real skill births already emit a narrated activity, which remains an orb peek source.
**Never do:** infer event semantics from incremental snapshot arrival unless the protocol provides an explicit snapshot boundary; prefer the existing event frame when one exists.

## Phase 1 inbound fields passed presence-only validation - 2026-07-12
**Severity:** High.
**Symptom:** malformed `approval_response.reply_to`, `interrupt.conversation_id`, or `undo.undo_token` values passed both contract validators and could crash detached mock handler tasks; an unknown approval decision could fall through the mock flow like an approval.
**Root cause:** runtime type and enum validation still covered only the original Phase 0 fields after Phase 1 handlers became active.
**Fix:** expanded the mirrored Python/TypeScript validators for active Phase 1 handler fields and enums; malformed-frame regression cases verify the Brain returns a recoverable error and keeps the connection open.
**Never do:** when an inbound message type becomes executable, promote every field used for lookup or control flow from presence-only schema checking to runtime type/enum validation in both mirrors.

## Mock Brain bypassed per-conversation serialization - 2026-07-12
**Severity:** High.
**Symptom:** two rapid mock `user_msg` frames for the same conversation interleaved output (`token,token` instead of the first turn's `token,error`) and could overwrite conversation-scoped pending approval state.
**Root cause:** the conversation lock lived inside the non-mock echo handler, while mock user messages were dispatched directly as independent tasks.
**Fix:** moved serialization to the shared user-message dispatch boundary and added a deterministic mock integration regression.
**Never do:** keep conversation serialization outside individual turn implementations so every Brain mode and handler path crosses the same lock.

## Orb dominant-axis resize could not shrink on one axis - 2026-07-12
**Severity:** Medium.
**Symptom:** dragging the bottom-right resize grip left while keeping the pointer vertically level (or upward while horizontally level) did not shrink the orb.
**Root cause:** `Math.max(dx, dy)` selected the numerically larger delta, not the delta from the axis with the greatest absolute movement.
**Fix:** extracted and tested a pure dominant-axis size calculation with 48-128px clamps; pointer cancellation now also clears gesture state.
**Never do:** choose a resize axis by absolute movement magnitude, preserve that axis's sign, and regression-test growth, shrink, and clamps independently of the native window.
> ⚠️ READ BEFORE WRITING ANY CODE. Every bug below has already been hit.
> Do not repeat them. Each entry has a "Never do" rule — treat it as a hard constraint.

## Corrupted rustup toolchain — 2026-07-10
**Symptom:** `cargo --version` failed with "Missing manifest in toolchain 'stable-x86_64-pc-windows-msvc'" even after `rustup default stable`.
**Root cause:** a pre-existing, partially-broken toolchain directory under `~/.rustup/toolchains/` that `rustup toolchain install` wouldn't cleanly overwrite (also hit a "detected conflict: bin\rust-gdb" error on reinstall attempt).
**Fix:** manually `Remove-Item -Recurse -Force` the toolchain dir + clear `update-hashes`, then `rustup toolchain install stable --profile minimal` from PowerShell (not Git Bash).
**Never do:** don't trust `rustup toolchain install`/`uninstall` to self-heal a broken toolchain dir — remove it by hand first. Don't run Rust toolchain commands through Git Bash on Windows (native binaries can throw "error while loading shared libraries" there); use the PowerShell tool.

## IPC envelope/payload `id` collision — 2026-07-10
**Symptom:** `approval_request`'s payload originally used the key `id` for the approval's own identity, which would collide with the envelope's `id` (message id) once flattened per `{type, id, ts, ...payload}`.
**Root cause:** payload field naming didn't account for the flattened envelope shape.
**Fix:** renamed the field to `approval_id` across all four layers (systemdesign/11-ipc-contract.md, shared/ipc-contract.json, ui/src/ipc/contract.ts, brain/brain/ipc/contract.py).
**Never do:** never name a payload field `id` in the IPC contract — the envelope already owns that key at the top level.

## Tauri child processes not reaped on Windows — 2026-07-10
**Symptom:** without explicit handling, closing the Tauri app would leave orphaned `python -m brain`/`python -m voice` processes holding the loopback port and a stale `session.json`, colliding with the next launch.
**Root cause:** `std::process::Command` does not auto-reap children when the parent dies on Windows.
**Fix:** `ui/src-tauri/src/supervisor.rs` kills children explicitly in the Tauri `RunEvent::ExitRequested` handler, via `kill_all()`.
**Never do:** never rely on OS process-tree cleanup for sidecar children on Windows — always kill them explicitly on app exit, and set the shutdown flag *before* killing (see Gotchas.md) so the supervision loop doesn't misread the intentional kill as a crash and respawn it.

## Default launcher spawned duplicate workers — 2026-07-10
**Severity:** High.
**Symptom:** `./dev.ps1` launched Brain and Voice directly, then launched Tauri, whose supervisor launched a second Brain and Voice. Two Brain processes raced to write `session.json`, and the directly launched workers were outside Tauri's cleanup ownership.
**Root cause:** the development script duplicated process ownership already implemented by Tauri.
**Fix:** the default `all` path now launches only Tauri; `-Only brain` and `-Only voice` remain for standalone debugging. Brain also holds a crash-safe OS lock, so an accidental second instance exits instead of overwriting `session.json`.
**Never do:** never launch workers separately when starting the full app; Tauri must be their only parent, and Brain must retain its single-instance lock.

## Required-field-only validation silently dropped turns — 2026-07-10
**Severity:** High.
**Symptom:** an authenticated `user_msg` with `conversation_id: []` passed validation, crashed a background task with an unhashable-list error, and emitted no `error` frame.
**Root cause:** both runtime validators checked field presence but not the types and enum values used during Phase 0.
**Fix:** TS and Python validators now check active Phase 0 field types and `user_msg.source`; regression tests cover malformed values.
**Never do:** never treat presence-only validation as sufficient for fields used as map keys, strings, or control values.

## UI queue cleared before sends completed — 2026-07-10
**Severity:** Medium.
**Symptom:** if a reconnect flush failed after sending only part of the queue, all remaining messages had already been removed and were lost.
**Root cause:** the hook copied and cleared the entire queue before calling `WebSocket.send`.
**Fix:** `flushQueuedMessages` removes each message only after that send succeeds; direct send failures are re-queued and force reconnect.
**Never do:** never remove queued work before the operation that commits it succeeds.

## Voice received the full mock snapshot and every broadcast frame — 2026-07-11
**Severity:** High. Found only by running the real stack (all automated tests were green).
**Symptom:** the Voice sidecar logged 17 "received frame" lines immediately after `hello_ack` — the entire mock snapshot (`belief_state`/`skill_state`/`task_state`/`spend_update`), which the contract says Voice must never receive.
**Root cause:** `hello` carried only `token`, so the Brain tracked authenticated clients as a role-less `set` and pushed the snapshot (and broadcast every outbound frame) to *any* authenticated connection — the "Voice gets only its subset" routing rule was unimplementable without a role signal at connect time.
**Fix:** `hello` gained an optional `role:"ui"|"voice"` (default `"ui"`); `server.py` tracks role per connection (`dict[ServerConnection, str]`) and `_frame_visible_to(role, msg_type, payload)` gates both the snapshot push and `_broadcast`.
**Never do:** never assume a green test suite proves routing/visibility rules — write a regression test that actually asserts what a restricted-role client receives (see `test_mock.py`'s `check_voice_routing_subset`), and confirm it live at least once.

## Stale window-state SIZE silently overrode the orb's fixed dimensions — 2026-07-12
**Severity:** Medium. **Symptom:** the orb rendered as a non-square rectangle with the circle off-center despite config specifying 64×64.
**Root cause:** `tauri_plugin_window_state`'s `StateFlags` are global across every window on one `Builder` — enabling `SIZE` for the workspace also restored whatever size was last saved under the "orb" label.
**Fix (superseded 2026-07-12):** circle now always sizes to `min(window width, height)` via `ResizeObserver`, centered by flexbox — correct regardless of window aspect ratio, so this class of bug can't recur.
**Never do:** don't assume a plugin's per-flag config applies per-window just because a per-window label exists elsewhere — check whether flags are global to the `Builder`.

## Edge-based orb resize handle conflicted with drag-to-move — 2026-07-12
**Severity:** High. Found only by live manual testing (tsc/cargo/selfchecks were all green).
**Symptom:** dragging the orb to move it would intermittently balloon the window into a large non-square rectangle.
**Root cause:** the resize-zone check treated the outer 8px of *every* window edge as a native-resize zone; on a ~64px orb that band covers nearly the whole clickable surface, so grab-to-move pointer-downs kept landing in a resize zone instead.
**Fix:** replaced all-edge detection with a single hover-revealed bottom-right corner grip, resize square-locked + clamped 48–128px via manual `setSize` (not native `startResizeDragging`) — drag-to-move now unambiguously owns the rest of the surface.
**Never do:** never size a resize-hit-zone as a fraction of the window when the window can be very small. Give resize and move mutually exclusive, clearly-bounded hit areas (a small corner grip, not "every edge").

## Mock Brain had no `task_op` handler — Stop button stuck forever — 2026-07-12
**Severity:** Medium. Found only by live manual testing.
**Symptom:** clicking Stop on the Step 6 status strip's running-task chip left the button reading "Stopping…" indefinitely.
**Root cause:** the UI followed the disable-until-confirmed rule correctly (rule 3 — resolve only on a confirming `task_state`, never optimistically), but `server.py`'s mock-mode dispatch only routed `user_msg`/`approval_response`/`interrupt`/`undo` — `task_op` was validated by the contract but never handled, so the Brain silently dropped it and no confirming frame ever arrived.
**Fix:** added `mock.handle_task_op` (stop/pause/resume → the matching `task_state`) and wired it into `server.py`'s dispatch table.
**Never do:** when a UI affordance sends a new outbound message type, verify the *mock* handles it too, not just that the contract validates it — a correctly-implemented "wait for confirmation" UI pattern will hang forever, indistinguishable from a UI bug, if the other side of the wire never confirms.

## Spawn-to-publish shutdown race could orphan a sidecar — 2026-07-10
**Severity:** High.
**Symptom:** app shutdown could set the shutdown flag and find no shared child during the narrow interval after `spawn()` but before the supervisor stored the handle, leaving the new process unowned.
**Root cause:** child creation and publication were not coordinated with the mutex used by `kill_all()`.
**Fix:** `publish_child` checks shutdown and stores the child while holding that mutex, or kills and waits for it immediately; a Rust regression test covers the shutdown-won path.
**Never do:** never publish a supervised child handle without coordinating with the shutdown path's lock and flag.

## Arg-shaped tier demotion in the gate (caught pre-commit) — 2026-07-20
**Severity:** High (would have shipped a permission bypass). Caught by orchestrator review of subagent code, before commit.
**Symptom:** none visible — all tests green. `file_delete` classified Tier 2 when args carried `expected_sha256`, intended "only for undo-of-create's inverse".
**Root cause:** the classifier trusted an arg value to prove *who* was calling. Args come from the LLM: it could `file_read` (Tier 1) a file, hash it, and issue a hash-carrying delete — an unapproved Tier-2 delete. Intent can't live in arg shapes; anything the classifier keys on is attacker-controllable by whoever authors tool calls.
**Fix:** deletes are Tier 3 for everyone; `gate.handle_undo` instead *trusts recorded inverses* (Brain-authored at execution time of an already-gated action, guarded by precondition + atomic token consumption) and runs them at Tier 2 without re-classifying. Same review also closed `dir > file` (cmd.exe honors redirection in joined args) and `git log --output=x` (write flag on an allowlisted "read-only" command).
**Never do:** never let arg *contents* lower a tier — classification may only ever raise on arg predicates, never demote. Privileged execution paths (undo) get trust from *where the record came from*, not from what the args look like. And an allowlisted command is only read-only if its flags and shell metacharacters are too.

## Broadcast interleaved into a client's snapshot — 2026-07-21
**Severity:** High (intermittent, ~1-in-3). Found only by running the suite in a loop; a single green run hid it completely.
**Symptom:** `test_server`/`test_gate` failed at random with e.g. `AssertionError: {'type': 'spend_update', ...}` where `settings_state` (the snapshot's first frame) was expected.
**Root cause:** a client is added to the `authenticated` broadcast set *before* its connect-time snapshot finishes streaming (correctly — so no event is missed). But that means a `_broadcast` fired by another conversation's turn could be sent *into the middle* of the snapshot. That breaks the "spend_update is the snapshot's last frame" sentinel every drain helper reads to, and would show the UI a live delta before the state it applies to.
**Fix:** `server._deferred` holds frames aimed at a client whose snapshot is still in flight; `_release_deferred()` flushes them in order once the snapshot completes, then the client goes live. Nothing is dropped and nothing arrives early.
**Never do:** never assume a green test run means a concurrency path is correct — race windows this narrow need the suite run repeatedly. And when a test is meant to catch a race, **verify it fails against the broken code**: the first version of this regression test raced two real sockets and passed 6/6 against the unfixed server, proving nothing. The version that shipped drives the mechanism directly and does fail without the fix.

## Memory panel Delete/Restore stuck on "Deleting…"/"Restoring…" forever — 2026-07-21
**Severity:** High (user-reported; silent data-loss of explicit intent — the belief is never archived, no error shown). Real (non-mock) Brain only; the reporter was on Phase-2 `./dev.ps1` with no `-Mock`.
**Symptom:** click Delete on a belief -> button locks to "Deleting…" (rule 3) and never unlocks; the belief never leaves the active list. Same for Restore in the archived view. Edit was unaffected.
**Root cause:** `useHaloConnection.ts`'s `sendMemoryEdit(belief_id, op, text?)` builds the outbound object via `{ type: "memory_edit", ...env(), belief_id, op, text }`. For delete/restore, `text` is an unpassed function argument (`undefined`) — but in a JS object literal that still creates an *own property* `text: undefined`. `contract.ts`'s validator checked `"text" in obj && typeof obj.text !== "string"`, and `"text" in obj` is `true` even when the value is `undefined`. So `dispatch()`'s unconditional `parseIpcMessage(msg)` call (line before the try/catch that would otherwise queue-on-failure) threw synchronously, and the throw was never caught anywhere in the MemoryView click/timer handler — the message never reached `ws.send`, so the Brain never saw a `memory_edit` and never had a chance to broadcast the confirming `belief_state`. The lock (`begin()`) had already fired before the throw, so the button stayed disabled with no recovery path. (`task_op`'s optional `task_id` had the identical latent bug — no caller currently passes `undefined` there, so it hadn't surfaced yet.)
**Ruled out:** (a) partially true (frame never arrived) but the cause was client-side, not the Brain silently swallowing a broadcast — `brain/brain/memory.py`'s `handle_memory_edit`/`store.set_belief_status` round-trip correctly (`test_memory.py` check 6 already covered it end-to-end via direct call). (b) reducer/selector are fine — `upsert()` always returns a fresh object, and the panel correctly filters `status==="archived"` out of the active list. (c) not a design mismatch — soft-archive-with-restore is exactly what shipped and what the code intends; the bug was purely that the confirming round trip never started. (d) `push_beliefs` replays are idempotent (same belief_id upserts a fresh object) and not implicated.
**Fix:** `contract.ts`'s optional-field checks (`hello.role`, `memory_edit.text`, `task_op.task_id`, `error.conversation_id`) now test `obj.field !== undefined` instead of `"field" in obj` — the single validator every outbound `dispatch()` call routes through. Added `contract.selfcheck.ts` cases that build the message the way the real sender does (spread of an unset param, not a hand-omitted key) — those failed before the fix and pass after.
**Never do:** never validate an optional field's presence with `"field" in obj` when the object may have been built via spread/destructure of a possibly-`undefined` variable — `in` sees the key even when the value is `undefined`. Use `obj.field !== undefined`. And never call a throwing validator (`parseIpcMessage`) outside a try/catch in a UI event handler that has already flipped a rule-3 lock — an uncaught throw there leaves the lock permanently stuck with no confirming frame ever possible.

## Global broadcast mistaken for a per-connection reply (test flake) — 2026-07-21
**Severity:** Medium (test-only, but it masked itself as "just flakiness"). ~1-in-4 in `test_server`, ~1-in-10 in `phase2_check`.
**Symptom:** random failures like `AssertionError: {'type': 'spend_update', ...}` where an `error` or `done` was expected. Passed on rerun every time, which is exactly what makes this class tempting to ignore.
**Root cause:** a turn runs as a background `asyncio.create_task`; its `spend_update` broadcast fires *after* the test already read `done` and moved on. `spend_update` is global (session/month totals) so it correctly reaches every connected client — including a connection opened by a later check. The checks read the next frame raw and asserted its type, so a legitimate unsolicited broadcast looked like a wrong reply. **Product behavior was correct; the tests were wrong.**
**Fix:** those reads now skip unsolicited global frames (the `_recv_type` idiom `test_gate.py` already used) instead of assuming the next frame belongs to them.
**Never do:** never assume the next frame on a shared connection is a reply to what you just sent — a broadcast channel can deliver global state at any time. And never write off a repeating "flake" without finding its cause: this one was benign, but the identically-shaped symptom the day before (`mem/Bugs.md`, "Broadcast interleaved into a client's snapshot") was a real product bug.

## Mock Brain silently discarded a real API key while reporting "connected" — 2026-07-21
**Severity:** High — cost a real user a real (paid) key rotation. Zero tests failed; every gate was green.
**Symptom:** user pasted their OpenRouter key, Settings showed "connected", then a relaunch showed "not set". Assuming the key was lost, they went to OpenRouter and generated a replacement. The original key had never been stored anywhere.
**Root cause:** `mock.py` kept key status in a module-level `_settings = {"openrouter_key": "missing"}` dict and its `handle_settings_update` only flipped that string — the key VALUE was dropped on the floor. `push_snapshot` then replayed the in-memory default, so every relaunch reported "missing". Under `--mock` the real `secrets_store` was never involved. A second, independent contributor: `secrets_store.get_key()` swallowed every backend exception and returned `None`, so an unreachable Credential Manager was reported as `"missing"` → rendered as "not set" → same wrong conclusion.
**Fix:** `openrouter_key` now always routes to the real keystore in BOTH modes (a credential the user deliberately typed is not mock data), the mock's snapshot reads real status instead of its in-memory default, and `key_status()` distinguishes `missing` (the keystore answered: nothing stored) from `invalid` (couldn't reach the keystore at all), worded in the UI as "couldn't verify" rather than "not set".
**Never do:** never let a mock/stub accept a real credential and report success — either store it for real or refuse it visibly; silently discarding it is the worst of the three. And never render "I couldn't check" as "it isn't there": they have different fixes, and the wrong one costs the user money. When a test writes a credential, set the keyring seam **explicitly in that test file** — `test_mock.py` was only protected by inheriting `test_server`'s import side effect, which would have overwritten the developer's real key the moment that import order changed.

## Real chat could not call any tool — the gate was unreachable — 2026-07-21
**Severity:** Critical (whole feature dead in production). Every automated gate was green; found only by driving the real Brain with a real key.
**Symptom:** asked to delete a real file, real Halo replied "please confirm" and raised no approval card. Asked to list a folder, it claimed "permission restrictions". No tool ever ran.
**Root cause — two independent layers:**
1. **No function-calling existed at all.** `pending_tool_intent` was set in exactly one place, behind `if os.environ.get("HALO_LLM_STUB") and text.startswith("CALL_TOOL ")`. `llm.py` never sent a `tools` array. Every gate/undo/file-tool test had passed against that offline sentinel, so Steps 5-7 were provably correct and simultaneously unreachable from real chat. The graph was also single-shot (`route -> gate|respond -> END`), with no loop to feed tool results back.
2. **No system prompt.** After wiring real tool calling, Tier 1 worked but Tier 3 still didn't: the model was never told the app gates its actions, so it asked for consent in prose — which never reaches the approval step — instead of calling the tool. The approval UI was bypassed by the model being polite.
**Fix:** real OpenRouter tool calling (schemas on the registry, `tools` on every request, per-`index` accumulation of fragmented streaming tool-call deltas, `respond ⇄ gate` loop with a round cap), plus a system prompt stating that calling the tool IS how you ask for permission. Verified live: Tier-1 `dir_list` executed; Tier-3 `file_delete` raised a real card and, on deny, left the file on disk with `denied` in the action log.
**Never do:** never accept a test seam as evidence the real path works. `HALO_LLM_STUB` made ~60 assertions pass across five suites while the production path did nothing — the seam tested itself. When a feature's only proof runs through a stub, the honest status is "unverified", not "done". And an offline stub can never surface a prompt-level failure: layer 2 was invisible to every test and only appeared when a real model chose politeness over the tool.
