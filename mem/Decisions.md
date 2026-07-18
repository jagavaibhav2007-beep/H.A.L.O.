# Decisions
_Architectural, structural, and system design choices._

## Store-connection wiring extracted to a shared hook, verified live - 2026-07-18
**What:** the 2026-07-18 ponytail pass found the store-connection glue (`onMessage` → `applyFrame`, the `connState → applyConnectionEvent` effect, the `sidecarError → sidecar_state:error` effect) duplicated byte-for-byte in `ui/src/orb/OrbRoot.tsx` and `ui/src/workspace/WorkspaceRoot.tsx`. Extracted to `ui/src/state/useStoreConnection.ts`; both roots call `useStoreConnection()` once (workspace destructures its `send*`/`connState`, orb ignores the return). Pure code-motion — statements moved verbatim, same effect deps, each root still opens exactly one connection.
**Why verification mattered:** this glue sits on the connection lifecycle — the repo's single most-documented "green tests pass, real stack broken" surface (Bugs.md: "Voice received full snapshot", "Rule-3 unlock silently never fired"). Transport code has *no* selfcheck by design ("transport-only, no business logic"), so `tsc` + the selfchecks provably cannot catch a botched extraction. Was initially going to defer to Phase 2 to avoid the live-verification cost, but chose to verify and ship.
**How it was verified (the pattern to reuse for any connection-lifecycle change):** static gates first (tsc + 4 UI selfchecks + cargo build/test + Python smoke/phase1/contract-sync — all green), then the mandatory live run: `./dev.ps1 -Mock`, and confirm in the Brain log that BOTH webviews authenticate — two `client authenticated (role=ui, mock=True)` lines (orb + workspace), no `read_session` retry loop, no reconnect storm. The browser fallback (`npm run dev`) canNOT verify this: `useHaloConnection` reads port/token via the Tauri `read_session` command, which only exists in the native app — so connection changes must be checked in the native window, and the Brain-side auth log is the observable signal (the native WebView2 window can't be headlessly driven).
**Trade-off:** none — behavior-preserving. Phase 2's real Brain reuses this one hook instead of two copies.

## Stable attached launcher by default; hot reload is opt-in - 2026-07-17
**What:** `./dev.ps1` runs the UI in its own terminal instead of spawning a detached PowerShell. Its default stable path uses `ui/src-tauri/tauri.stable.conf.json`, `npm run dev:stable`, `vite preview`, and Tauri `--no-watch`; `-WatchNative` selects the original Vite dev server and native watcher. A named mutex rejects a second launcher before it starts any build or server.
**Why:** automated workspace synchronization emits metadata-only writes across the dirty tree, which made both watchers repeatedly reload otherwise unchanged code. An attached process also gives Ctrl+C one ownership path for Tauri, Brain, Voice, and the frontend server.
**Trade-off:** stable mode performs a frontend build at startup and does not live-reload edits. Developers intentionally editing UI/Rust can pass `-WatchNative`; agent-driven runs favor continuity and trustworthy logs.

## Clip the Windows capsule at the HWND layer - 2026-07-17
**What:** the orb/capsule window uses a target-specific Win32 GDI dependency and `SetWindowRgn` to make the native window itself a 360x52 pill. Tauri's transparent background remains enabled, with an explicit zero-alpha `backgroundColor`.
**Why:** the existing CSS radius correctly shaped the DOM but could not alter the rectangular native window or its hit-test region. A native region is the smallest Windows-specific fix that makes painting and pointer bounds agree with the visual capsule.
**Trade-off:** a small platform-specific unsafe block and one Windows-only crate feature. Non-Windows builds keep a no-op shaping function; resize and DPI events reapply the region so physical dimensions stay correct.

## Remove conclusively unused generated scaffold assets - 2026-07-17
**What:** removed the unused Vite, React, and Tauri placeholder SVG/PNG assets, removed the Vite favicon reference, and dropped stale `peek`/resize-drag capability entries left after the capsule redesign. The bundle icons still referenced by `tauri.conf.json` remain.
**Why:** repository-wide reference searches found no consumers for the deleted assets or permissions; retaining them implied obsolete product surfaces and widened the capability manifest without a caller.
**Trade-off:** if Windows Store tile assets or native resize dragging are introduced later, generate or add only the required files and permissions as part of that feature rather than preserving dead scaffolding now.

## Approval card: `interrupt` lives on the card as "Stop this task" — 2026-07-13
**What:** Step 10's `ApprovalCard` has a "Stop this task" link that sends `interrupt(conversation_id)`, kept in the Step-10 file rather than the chat/status-strip. It is deliberately distinct from Deny: Deny answers *this one action* (`approval_response{deny}` → the task pauses gracefully), while Stop cancels *the whole conversation's pending work* (implicit deny + pause).
**Why:** the "stale-card rule" (interrupt removes a pending card) is a Step-10 acceptance item, but nothing in the UI could send `interrupt` — the outbound union was `UserMsg|TaskOpMsg|UndoMsg`. `task_op` is not a substitute: `handle_task_op` only broadcasts a `task_state` and never resolves the mock's pending-approval future, and it defaults `task_id` to the seed. A real `sendInterrupt` was needed regardless; putting the affordance on the card makes the rule clickable/verifiable without touching Step-6/8 components. The card's *removal* was already automatic — the store's `resolveApprovalsForTask` fires on the `task_state:paused` that `handle_interrupt` broadcasts.
**Trade-off:** a 4th control on the most trust-critical surface. Acceptable because it's visually secondary (a small underlined link in the footer, not a button) and semantically separate from the Approve/Deny/Edit row. **A broader chat/global "Stop" is the eventual home** (Step 8/11) — when that lands, the card link can defer to it.
**Also settled:** `approval_response.reply_to` MUST be the request's `approval_id`, never the envelope `id` — the Brain keys `_pending_approvals` by `approval_id`, so the envelope id would leave the future unresolved forever (the standing envelope-id-collision trap).

## User messages live in the store as turns, appended at send time — 2026-07-12
**What:** Step 8's chat renders one ordered `turns` array per conversation, a `UserTurn | AssistantTurn` union discriminated by `role`. Assistant turns come from `token`/`done` frames (as before); user turns are appended by a pure `appendUserTurn(state, convId, text, id)` helper (wrapped in the store), called from `ChatView` right before `sendUserMsg`.
**Why:** `user_msg` is never echoed back — not by the mock, and not by Phase 2's real Brain (the reducer already noted "the user_msg echo may be local-only"). Merging a local user-message list with store assistant turns in the component fails on the cases that actually occur (two sends before a reply, an error/interrupted turn with no paired assistant turn, tokens for an unknown conversation_id). One ordered list appended in arrival order is the honest D7 model.
**Why not in `useHaloConnection`:** that hook is transport-only by design (no business logic in the UI WS client). The view owning "reflect my own send" keeps that boundary intact. The user turn's `id` is just a React key — it needn't match the sent frame's id, so there's no correlation risk.
**Trade-off:** `turns` became a union, so every `.status/.taskId/.note` access needs `role === "assistant"` narrowing (reducer callbacks + a `assistantTurn()` assert-helper in the selfcheck). ~15 min of narrowing, not a rewrite. Selfcheck scenario 7 pins the one behavior the union could silently regress (user turn → next token opens a fresh assistant turn, not a fold-in).

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
**What:** the orb window changed from fixed 64×64/`resizable:false` to `resizable:true`. The visible glass sphere is a child of a full-window "hit area," sized in JS (via `ResizeObserver` on `document.documentElement`) to `Math.floor(Math.min(width, height))` and centered by flexbox — so a non-square window never stretches the circle into an ellipse; the excess space on the longer axis just stays transparent. This part is unchanged and still holds.
**Why:** explicit user request after confirming the fixed-size orb worked correctly. The "always a circle, never an ellipse" constraint came from `advisor` review before implementation — the naive `width:100%;height:100%` approach would have shipped a visibly-broken ellipse on the very first non-corner drag.
**Superseded same day — the resize *mechanism*:** the original all-edge `RESIZE_HANDLE_PX`/`startResizeDragging` approach (see Gotchas.md) broke drag-to-move on the tiny orb and shipped as a real bug (see Bugs.md "Edge-based orb resize handle conflicted with drag-to-move"). Replaced with: resize confined to a single hover-revealed bottom-right corner grip (`RESIZE_CORNER_PX`), square-locked and clamped to `MIN_ORB_PX`–`MAX_ORB_PX` (48–128) via manual `setSize` in `onPointerMove`, with `tauri.conf.json`'s `maxWidth`/`maxHeight` set to match so the OS can't override the clamp either. Drag-to-move now unambiguously owns the rest of the surface.
**Trade-off:** the resize range is now bounded (48–128px) rather than unlimited — acceptable; an orb outside that range isn't a real use case, and the clamp is what prevents a stale/huge persisted `window-state` size from recurring visually even if the state file is ever corrupted again. `enforce_orb_size` (added, then removed same day — see Bugs.md) stays superseded: size is legitimately user-controlled within the clamp, not forced to a constant.

## Deleted the legacy single-window chat prototype (`App.tsx`) — 2026-07-15
**What:** removed `ui/src/App.tsx` + `ui/src/App.css` (the Phase-0 scaffold chat UI) and its `main.tsx` routing branch. A bare browser tab (`npm run dev`, no `?window=` param) now resolves to `WorkspaceRoot` instead of the old prototype.
**Why:** found during the ponytail refactor sweep — `App` had zero importers besides its own lazy route, was superseded by the real `ChatView` since Phase 1, and `App.css` was the repo's only tokens-rule violation (11 raw hex colors, never migrated to `tokens.css`). Its only remaining purpose was as the fallback for a bare dev-server tab, which is better served by the actual app shell anyone iterating on the UI wants to see.
**Trade-off:** none functionally — nothing production-facing referenced it. If a future need arises for an isolated single-window chat harness (e.g. a minimal embed target), rebuild it against the current `ChatView`/`useHaloConnection` rather than resurrecting this file; it predates the real IPC contract's later additions (undo, belief/skill state, mic) and never tracked them.

## Companion orb → capsule redesign (Midnight Blue) — 2026-07-15
**What:** the floating companion changed from a bare glass circle to a horizontal **capsule** (360×52, radius=height/2 — a true pill): `[lane · task] ((orb)) [approval · mic]`, all signals visible at once. The orb survives as the capsule's centre (voice state + inline narration only). Palette shifted to "Midnight Blue" — `--canvas` gradient `#02060E → #0356C5` is the app's own backdrop; glass surfaces stay dark midnight (`--surface: rgba(10,16,34,.62)` dark) with royal blue as a *glow* accent, never a flat fill.
**Why:** direct user feedback — the orb's "one glow colour = one state" language was pretty but useless for tracking what Halo was doing / what needed approval; you had to open the whole workspace to find out. The PRD (§12) had always listed a lane indicator as a core surface that the orb never implemented. The `ui-ux-pro-max` skill's "Modern Dark / Cinema" glassmorphism entry supplied the key rule that fixed an earlier heavy-looking mockup: **accent as jewel, not slab** — dark base + blue glow reads premium, a bright-blue fill reads cheap.
**What it killed (deleted, not deprecated — user asked for no carry-over dead code):** the whole `deriveOrbState` priority ladder (approval>error>task>voice existed only to cram 4 signals into one circle; chips show everything now), the separate peek **window** (`PeekBubble.tsx`, `PeekWindow.tsx`, the `peek` Tauri window, `show_peek`/`hide_peek` Rust commands + cross-window plumbing — narration is inline in the capsule now), and the square-lock resize system (`geometry.ts`/`.selfcheck.ts` — a fixed capsule doesn't resize). Net −480 lines.
**Trade-off / deferred:** chip clicks currently all open the workspace to the last view — true deep-linking (approval chip → that approval) needs a cross-window nav channel that doesn't exist yet (the old `focusTarget` was deleted as dead code earlier this session); left a `ponytail:` marker in `OrbRoot.tsx`. `--canvas` token exists but wiring the workspace window background to actually use it is a follow-up (this change was the capsule). Design docs `ui_ux/01-companion-orb.md` (retitled "Companion Capsule") and `00-design-language.md` (Midnight Blue tokens + "jewel not slab" rule) rewritten to match — they are the source of truth, updated before the code.
