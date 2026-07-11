# Decisions
_Architectural, structural, and system design choices._

## Three-process architecture — 2026-07-10
**What:** Tauri (Rust) UI is the parent process; it spawns Brain (Python/LangGraph) and Voice (Python/Pipecat) as child processes, all talking over an authenticated local-loopback WebSocket.
**Why:** matches Halo-PRD.md's process model; keeps the permission gate meaningful by putting a real auth choke point (hello-token handshake) on the only transport between processes.
**Trade-off:** more moving parts to supervise (backoff/restart logic) than a single-process app.

## Sidecars run from source in dev; packaging deferred — 2026-07-10
**What:** Phase 0 spawns `python -m brain`/`python -m voice` directly rather than packaging them as PyInstaller binaries.
**Why:** packaging is explicitly out of scope for Phase 0 (walking-skeleton phase); avoids building binary packaging before the process contract is even proven.
**Trade-off:** dev-mode cwd resolution (`CARGO_MANIFEST_DIR` → repo root) is a `// ponytail:`-marked stopgap that the packaged-binary phase will replace.

## std::process::Command over tauri-plugin-shell sidecar API — 2026-07-10
**What:** Step 6's Rust supervisor uses plain `std::process::Command` instead of Tauri's `externalBin`/sidecar plugin machinery.
**Why:** that plugin API is built for packaged binaries; since packaging is deferred, it's the wrong rung on the ladder for dev-mode `python -m brain`.

## Tauri→React state events are webview events, not IPC frames — 2026-07-10
**What:** the `sidecar-state` event (`{process, state}`) that Step 6 emits to React uses `tauri::Emitter::emit`, and is NOT added to `shared/ipc-contract.json`.
**Why:** it's a Tauri-internal signal about OS process health, distinct from the Brain↔UI/Voice WebSocket message contract — conflating the two would blur "can I chat" (WS state) with "is the Brain process alive" (sidecar state), which are genuinely different things (Step 7 keeps them as two separate pieces of UI state).

## Explicit authentication acknowledgement — 2026-07-10
**What:** Brain sends the transport-level `hello_ack` frame after validating `hello`; UI and Voice do not send application messages before receiving it.
**Why:** an open socket does not prove that a session token was accepted, so optimistic queue flushing could lose messages during a restart race.
**Trade-off:** adds one IPC message and one handshake step, but gives clients an unambiguous authenticated state.

## Crash-safe single-Brain lock — 2026-07-10
**What:** Brain holds an OS file lock on `%LOCALAPPDATA%\Halo\brain.lock` for its process lifetime.
**Why:** prevents manually launched or duplicated Brain processes from racing to own `session.json`; OS locks release automatically after crashes so Tauri can restart Brain.
**Trade-off:** a second Brain exits immediately instead of running independently under the same OS account.
