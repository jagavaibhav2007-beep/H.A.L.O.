# Gotchas
_Non-obvious traps, hidden constraints, things that bite._

## Brain respawns on a new ephemeral port every time — 2026-07-10
The Brain binds port 0 (OS-assigned) and rewrites `%LOCALAPPDATA%\Halo\session.json` on every start, including supervised restarts. Any client (UI, Voice, smoke test) must re-read session.json fresh on *every* connect/reconnect attempt — never cache port/token from a prior connection, or reconnection silently dials a dead port forever.

## Shutdown-flag-before-kill ordering — 2026-07-10
The Tauri sidecar supervision loop polls an `AtomicBool` shutdown flag. It must be set to `true` BEFORE killing child processes on app exit — otherwise the poll loop sees the intentional kill as a crash and immediately respawns the child mid-shutdown. Classic race; not caught by a shallow "does it launch" check, only by an actual graceful-close test.

## `hello_ack` gates application traffic — 2026-07-10
The Brain silently closes failed authentication but sends `hello_ack` after a valid `hello`. UI and Voice must keep application messages queued until that acknowledgement arrives; an open WebSocket alone is not authenticated.

## StrictMode double-invokes effects — 2026-07-10
React 18/19 StrictMode (on in `ui/src/main.tsx`) mounts→unmounts→remounts effects in dev. A WS-connect effect needs a teardown flag (checked before every async continuation) and must null out `onclose` before calling `.close()` in cleanup, or the intentional teardown close fires the reconnect loop and creates a zombie second socket.

## `CloseMainWindow()` can no-op on a `MainWindowHandle` of 0 — 2026-07-10
If the webview is mid-reload (e.g. right after Vite's dependency-optimization reload), `Get-Process -Name ui`'s `MainWindowHandle` can transiently read 0, and `CloseMainWindow()` silently does nothing. Re-fetch the process and confirm a non-zero handle before concluding a graceful-close/kill_all test failed.

## First `tauri dev` build is slow; later ones are fast — 2026-07-10
A clean `cargo tauri dev` compiles ~359 crates and can take 2-3 minutes (MSVC's linker is the slow part, especially the final binary link). Once `target/` is warm, subsequent builds are seconds. Don't assume a build is stuck at 355-358/359 — that's normal for the final crates + link step.

## Native Rust/cargo binaries misbehave under Git Bash on Windows — 2026-07-10
`rustc.exe`/`cargo.exe` can throw "error while loading shared libraries" when invoked through Git Bash due to path mangling. Use the PowerShell tool for Rust toolchain operations on this machine.
