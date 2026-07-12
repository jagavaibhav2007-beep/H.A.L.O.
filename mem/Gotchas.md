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

## `tauri-plugin-window-state`'s `StateFlags` are global, not per-window — 2026-07-12
`tauri_plugin_window_state::Builder::new().with_state_flags(...)` is set once and applies to *every* window the plugin tracks — there is no way to say "POSITION only for window A, POSITION+SIZE for window B" via flags alone. Per-window control only exists as all-or-nothing exclusion (`with_denylist`, `skip_initial_state`). If different windows genuinely need different persisted fields (e.g. a fixed-size orb wants position-only, a resizable workspace wants position+size), either give every window the same policy, or don't rely on the plugin's automatic per-window hooks for the one that's different — enforce that window's constraint manually after `setup()` runs (the plugin's restore fires in `on_window_ready`, which completes before the app's own `.setup()` closure).

## Borderless Tauri windows (`decorations:false`) have no automatic resize border — 2026-07-12
With no OS title bar/chrome, `resizable:true` alone does not give the user anything to grab — there's no native hit-testable border. You must implement resize handles yourself.
**Update (same day, after live testing):** the first approach — detect pointer-down proximity to *any* edge (an 8px `RESIZE_HANDLE_PX` band against `window.innerWidth`/`innerHeight`) and call `getCurrentWindow().startResizeDragging(direction)` — broke drag-to-move on a small (~64px) window: the 8px edge band covers almost the whole surface, so a normal grab-to-move pointer-down kept landing in a resize zone instead (see Bugs.md). Replaced with a single small corner grip (hover-revealed) checked by coordinate, and manual `setSize` calls (square-locked, clamped) driven from `onPointerMove` instead of the native `startResizeDragging` — simpler to keep mutually exclusive with move-drag, and gives full control over the aspect/size clamp. **Only reach for `startResizeDragging`/all-edge detection on windows large enough that an 8px band is a small fraction of the surface** — on anything orb-sized, prefer one corner grip + manual `setSize`.
Note: `@tauri-apps/api/window`'s `ResizeDirection` type (used by `startResizeDragging`) is not exported from the package — mirror the literal union locally if you do end up needing native OS-driven resize dragging.

## Windows `LNK1104` after a Rust rebuild is usually transient, not a real error — 2026-07-12
`cargo build`/`cargo test` can fail with `LINK : fatal error LNK1104: cannot open file '...exe'` right after a successful compile, even with no process visibly holding the file (checked via `tasklist`) — most likely Windows Defender briefly scanning the freshly-written binary. A bare retry of the same command has cleared this every time it's been hit in this project. Don't chase it as a code bug before retrying once.
