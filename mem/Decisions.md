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

## Panel data as snapshot-on-connect + delta-on-change — 2026-07-11
**What:** Memory and Skills panels are fed by outbound `belief_state`/`skill_state` frames (Phase 1 Step 1), pushed as a full snapshot after `hello_ack` and again as single deltas when one item changes — the same pattern `task_state` already uses. The UI stores them as idempotent upserts keyed by `belief_id`/`skill_name`.
**Why:** a reconnecting or second client (orb + workspace both connect) must be able to rebuild full panel state from the event stream alone, with no separate query API; id-keyed upserts make snapshot re-pushes converge instead of duplicating.
**Trade-off:** the Brain re-sends all beliefs/skills on every connect (fine at Halo's single-user scale; a paged fetch is the upgrade path if the belief count ever gets large).

## `hello.role` for outbound routing — 2026-07-11
**What:** the `hello` handshake frame gained an optional `role:"ui"|"voice"` field (default `"ui"`). The Brain tracks each authenticated connection's role and gates both snapshot-push and broadcast frames through `_frame_visible_to(role, msg_type, payload)`.
**Why:** the contract's routing rule ("Voice is sent only its subset") was literally unimplementable without a client-identity signal at connect time — the snapshot fires before any `user_msg`, so nothing later in the stream could distinguish a UI client from a Voice client. Found and fixed after running the real stack (see Bugs.md); no automated test had asserted this until `test_mock.py`'s `check_voice_routing_subset` was added alongside the fix.
**Trade-off:** one more optional field on the most security-relevant frame in the contract; mitigated by defaulting unknown/absent role to `"ui"` (the permissive case), so no existing client (including raw test clients) silently loses data.

## Orb: free-placement drag, no edge-snap — 2026-07-12
**What:** `phase-1-plan.md`'s original Step 5 spec called for the orb to "snap to the nearest screen edge" on drag release. Reversed after user testing — the orb now stays exactly where it's dropped.
**Why:** direct user feedback ("I don't want it to stick to an edge like a magnet — I should be able to place it anywhere"). The plan doc itself was updated to match, so it stays an accurate record rather than describing removed behavior.
**Trade-off:** none functionally; `window-state` still persists whatever position the user leaves it at, so the "remembers position across restarts" acceptance criterion is unaffected.

## Orb is user-resizable, circle locked to `min(width,height)` — 2026-07-12
**What:** the orb window changed from fixed 64×64/`resizable:false` to `resizable:true` (min 32×32, no max). Since the window is borderless (`decorations:false`), resize handles are hand-implemented: pointer-down within `RESIZE_HANDLE_PX` of an edge calls `getCurrentWindow().startResizeDragging(direction)` instead of the normal move-drag/click logic. The visible glass sphere is a child of a full-window "hit area," sized in JS (via `ResizeObserver` on `document.documentElement`) to `Math.floor(Math.min(width, height))` and centered by flexbox — so a non-square window (e.g. dragging one edge, not a corner) never stretches the circle into an ellipse; the excess space on the longer axis just stays transparent.
**Why:** explicit user request after confirming the fixed-size orb worked correctly. The "always a circle, never an ellipse" constraint came from `advisor` review before implementation — the naive `width:100%;height:100%` approach would have shipped a visibly-broken ellipse on the very first non-corner drag.
**Trade-off:** the interior (non-resize-edge) click/drag zone shrinks toward the `RESIZE_HANDLE_PX` (8px) border at the 32px minimum size — acceptable, matches how any small resizable window behaves. `enforce_orb_size` (added, then removed same day — see Bugs.md) is superseded by this: size is now legitimately user-controlled and persisted via `window-state`, not forced back to a constant.
