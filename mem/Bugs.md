# Bugs

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
**Severity:** Medium.
**Symptom:** the orb window rendered as a non-square rectangle with the 56px circle visually offset instead of centered, despite `tauri.conf.json` correctly specifying 64×64.
**Root cause:** `tauri_plugin_window_state`'s `StateFlags` are global across every window registered on one `Builder` (see Gotchas.md) — enabling `SIZE` so the workspace's size/position persist also restores whatever size was last saved under the "orb" label, silently overriding the config on every launch once any stale entry existed.
**Fix (superseded):** an `enforce_orb_size()` override was added, then removed once the orb became intentionally resizable (2026-07-12 same day) — see Decisions.md "Orb is user-resizable". The CSS fix (circle always sizes to `min(window width, height)` via `ResizeObserver`, centered by flexbox) makes the orb visually correct regardless of window aspect ratio going forward, so this class of bug can't recur even if window-state restores an odd size.
**Never do:** don't assume a plugin's per-flag config (`StateFlags::SIZE`) applies per-window just because you pass a per-window label elsewhere in the same file — check whether the flags are global to the `Builder` before trusting size/position persistence on a fixed-size window.

## Edge-based orb resize handle conflicted with drag-to-move — 2026-07-12
**Severity:** High. Found only by live manual testing (tsc/cargo/selfchecks were all green).
**Symptom:** dragging the orb to move it would intermittently balloon the window into a large non-square rectangle — the user reported "as soon as I moved it, it turned back into [a giant] screenshot."
**Root cause:** `resizeDirectionAt` treated the outer 8px (`RESIZE_HANDLE_PX`) of *every* window edge as a native-resize zone. On a ~64px orb that 8px band covers nearly the entire clickable surface, so a normal grab-to-drag pointer-down frequently landed inside a resize zone instead and started `startResizeDragging` — combined with the pre-existing persisted large size (see the "Stale window-state SIZE" entry above, same day), this was very easy to trigger and hard to distinguish from a rendering bug at first glance.
**Fix:** replaced all-edge resize detection with a single small bottom-right corner grip (`RESIZE_CORNER_PX`, hover-revealed), and made resizing square-locked + clamped 48–128px (`MIN_ORB_PX`/`MAX_ORB_PX`) via manual `setSize` rather than native `startResizeDragging`, so drag-to-move now owns the entire rest of the surface unambiguously.
**Never do:** never size a resize-hit-zone as a fraction of the window when the window itself can be very small — a fixed-px band that's fine on a normal-sized window can swallow almost the whole surface on a ~64px one. Give resize and move mutually exclusive, clearly-bounded hit areas (a small corner grip, not "every edge"), especially on borderless windows with no OS-drawn resize affordance.

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
