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

## Spawn-to-publish shutdown race could orphan a sidecar — 2026-07-10
**Severity:** High.
**Symptom:** app shutdown could set the shutdown flag and find no shared child during the narrow interval after `spawn()` but before the supervisor stored the handle, leaving the new process unowned.
**Root cause:** child creation and publication were not coordinated with the mutex used by `kill_all()`.
**Fix:** `publish_child` checks shutdown and stores the child while holding that mutex, or kills and waits for it immediately; a Rust regression test covers the shutdown-won path.
**Never do:** never publish a supervised child handle without coordinating with the shutdown path's lock and flag.
