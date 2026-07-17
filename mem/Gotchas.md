# Gotchas
_Non-obvious traps, hidden constraints, things that bite._

## CSS border radius does not shape a native Tauri window - 2026-07-17
**Symptom:** the capsule DOM has the correct pill outline and `overflow:hidden`, but the desktop still shows rectangular corners around it. **Cause:** CSS clips WebView content only; the top-level Windows HWND remains rectangular, and a merely `transparent:true` window can still expose its backing surface. **Handling:** set the WebView background alpha to zero and apply a Win32 window region; reapply after physical size or DPI changes. Browser preview cannot verify HWND shape or native hit testing, so launch the Tauri app for final confirmation.

## A live Tauri dev session can cause Rust test `LNK1104` - 2026-07-17
`cargo test --lib` can fail to link because `npm run tauri dev` is holding a Windows target artifact. Stop the specific dev session and retry before treating it as a source failure. In the 2026-07-17 remediation run, all four Rust tests passed immediately after the dev process was stopped.

## This machine's stable Rust toolchain lacks `rustfmt` - 2026-07-17
`cargo check`, native Tauri builds, and Rust tests work, but `cargo fmt --check` reports that `cargo-fmt.exe` is not installed for `stable-x86_64-pc-windows-msvc`. Do not report formatting verification as passed unless the component is installed; use compiler/tests plus `git diff --check` and state the limitation.

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

## Windows desktop notification click/action delivery is unreliable — 2026-07-13
`@tauri-apps/plugin-notification`'s `onAction` (the `actionPerformed` event) has solid handlers on mobile (Android/iOS) but click-to-focus on a plain Windows toast is not dependable in Tauri v2. Step 10's away-approval flow fires the toast (that part works) and registers `onAction → invoke("show_workspace")` best-effort, but do **not** treat the toast click as the guaranteed way back into the app — the orb's amber approval badge is the reliable path. If a Windows feature depends on the user clicking a toast body, verify delivery on the actual OS before relying on it; don't assume parity with the mobile handlers shown in the plugin docs.

## `@tauri-apps/plugin-notification` import is safe in the D9 browser fallback — 2026-07-13
Importing `sendNotification`/`onAction`/`isPermissionGranted` at module top-level does **not** throw in a plain `npm run dev` browser tab (unlike `invoke`, which needs the Tauri IPC global). So the usual pattern holds: import freely, gate the *calls* behind `isTauri()`. Confirmed by loading `?window=workspace` in the browser fallback with zero new console errors beyond the expected `read_session` retries.

## Borderless Tauri windows (`decorations:false`) have no automatic resize border — 2026-07-12
With no OS title bar/chrome, `resizable:true` alone does not give the user anything to grab — there's no native hit-testable border. You must implement resize handles yourself.
**Update (same day, after live testing):** the first approach — detect pointer-down proximity to *any* edge (an 8px `RESIZE_HANDLE_PX` band against `window.innerWidth`/`innerHeight`) and call `getCurrentWindow().startResizeDragging(direction)` — broke drag-to-move on a small (~64px) window: the 8px edge band covers almost the whole surface, so a normal grab-to-move pointer-down kept landing in a resize zone instead (see Bugs.md). Replaced with a single small corner grip (hover-revealed) checked by coordinate, and manual `setSize` calls (square-locked, clamped) driven from `onPointerMove` instead of the native `startResizeDragging` — simpler to keep mutually exclusive with move-drag, and gives full control over the aspect/size clamp. **Only reach for `startResizeDragging`/all-edge detection on windows large enough that an 8px band is a small fraction of the surface** — on anything orb-sized, prefer one corner grip + manual `setSize`.
Note: `@tauri-apps/api/window`'s `ResizeDirection` type (used by `startResizeDragging`) is not exported from the package — mirror the literal union locally if you do end up needing native OS-driven resize dragging.

## Windows `LNK1104` after a Rust rebuild is usually transient, not a real error — 2026-07-12
`cargo build`/`cargo test` can fail with `LINK : fatal error LNK1104: cannot open file '...exe'` right after a successful compile, even with no process visibly holding the file (checked via `tasklist`) — most likely Windows Defender briefly scanning the freshly-written binary. A bare retry of the same command has cleared this every time it's been hit in this project. Don't chase it as a code bug before retrying once.

## Shared CSS primitive classes lose ties on specificity, not just source order — 2026-07-15
When a view-local class overrides a property also set by a shared `.halo-*` primitive (e.g. `.task-lane-select` overriding `.halo-input`'s `background`/`font-size`), a single-class local selector has the *same* specificity as the single-class shared one — whichever stylesheet loads last wins, which in practice means whichever component happens to import its CSS later. That's an accident of import order, not a contract, and reordering imports elsewhere in the app can silently flip it. Fix: make the override selector two classes (`.halo-input.task-lane-select`) so it wins on specificity regardless of load order. Caught in code review during the 2026-07-15 refactor sweep before it shipped — check for this pattern any time a view CSS file overrides a property already set by a `.halo-*` primitive class.
