# Bugs
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
