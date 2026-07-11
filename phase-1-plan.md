# Phase 1 — Front-end Shell: Implementation Plan

The full premium UI, every surface rendering against a **mocked Brain** that replays scripted IPC events, per [phases.md](phases.md#phase-1--front-end-shell-the-feel). Establishes the look, motion, and trust surfaces before any real logic exists. Built strictly against [systemdesign/11-ipc-contract.md](systemdesign/11-ipc-contract.md) and the [ui_ux/](ui_ux/00-design-language.md) spec — the mocked Brain and the Phase-2 real Brain emit the *same* message shapes, so Phase 1 → Phase 2 is a swap, not a rewrite.

**Phase exit criteria (the whole phase is done when):**
1. Every panel — Chat, Tasks, Activity, Memory, Skills, Settings — renders and animates from scripted events; sidebar navigation preserves each view's scroll and state.
2. Orb ↔ workspace works: global hotkey and orb-click expand (250ms scale+fade from the orb), `Esc` collapses back; the orb displays its full state language including the amber approval state.
3. One scripted walkthrough ("demo everything") drives: token streaming, a task lifecycle with live lane chip, a Tier-3 approval round-trip (plus the destructive hold-to-approve variant), an undo, a memory auto-correct, a skill birth, and the voice loop (wake → listening → transcript ghost text → speaking) — all rendering correctly.
4. Reduced-motion and reduced-transparency modes are fully usable; the complete keyboard path works (hotkey summon, `Esc`, `Ctrl+K`, tab order = visual order); contrast meets the token table.
5. The activity feed stays smooth with 2,000+ entries (virtualized).
6. Killing the mock Brain mid-session: orb/chat show reconnecting, input queues with the "will send when reconnected" badge, and reconnection re-syncs panel state idempotently (Phase-0 supervision still green).
7. All selfchecks pass: contract sync, contract TS/Py, queue, the new store-reducer replay check, and the mock-scenario frame check.

**Stack:** existing Tauri + React/TS + Python Brain. New UI dependencies (each justified in D8): `zustand`, `@tanstack/react-virtual`, `react-markdown`, `lucide-react`, Tauri plugins `global-shortcut`, `notification`, `window-state`.

**Out of scope for all steps:** real LLM calls, real memory/gate/undo logic (the mock *honors the shapes*, not the semantics), voice audio/STT/TTS (the Voice sidecar stays the Phase-0 idle stub — `voice_state`/`transcript` frames are scripted by the mock Brain), first-run onboarding (needs real key storage → Phase 2), sidecar packaging, real Lane 2/3 desktop streams (the tile renders scripted `stream_frame`s), code syntax highlighting (plain JetBrains Mono for now).

---

## Architecture decisions

**D1 — The mock Brain is the real Brain with a scripted handler, not a second server.**
`python -m brain --mock` keeps Phase 0's WS server, session.json handshake, auth gate, single-instance lock, and envelope validation — only the `user_msg` handler is swapped for a scenario engine. Everything Phase 0 proved (auth, reconnect, supervision) stays exercised for free, and every scripted frame passes the same `_envelope()` contract validation as real frames, so the mock physically cannot emit a shape the real Brain couldn't. *Rejected:* a standalone TS/Node mock server — second implementation of auth + envelope to keep in sync, and it would bypass the supervision tree.

**D2 — Scenarios are keyword-triggered; the contract is not extended for "test control".**
Typing `demo approval`, `demo task`, `demo destructive`, `demo memory`, `demo skill`, `demo voice`, `demo error`, `demo flood` (2,000 activity entries for perf), or `demo everything` triggers the matching script; any other `user_msg` gets a generic streamed reply. No out-of-band control channel, no test-only message types polluting the contract.

**D3 — Two Tauri windows (orb + workspace), not one morphing window.**
The orb needs always-on-top, no taskbar entry, no focus stealing (`focusable(false)` → WS_EX_NOACTIVATE), transparency, and a 56px footprint. The workspace needs normal resize/focus/taskbar behavior. Morphing one window between those flag-sets while animating 56px→900px is exactly the janky, GPU-fighting motion the design language forbids. Two windows, with the workspace's 250ms scale+fade *anchored to the orb's screen position* (passed at open), fake the spatial continuity cheaply. *Rejected:* single morphing window.

**D4 — Each webview owns its WS connection; the Brain broadcasts outbound frames to all authenticated clients.**
Orb and workspace each connect through the existing `useHaloConnection` (re-reading session.json per attempt, per the Phase-0 rule). The server moves from reply-to-sender to broadcast-to-authenticated for outbound frames — which the contract's routing section already implies ("the UI gets everything") and Phase 2 needs anyway for the Voice subset. State stays consistent between windows by construction: both project the same event stream (D5). *Rejected:* one window owning the WS and relaying to the other via Tauri events — a second, bespoke IPC layer.

**D5 — UI state is a pure projection of IPC events: one reducer, one store.**
A pure `applyFrame(state, frame)` in `ui/src/state/reducer.ts`, wrapped by a zustand store. Slices: connection, conversations (with per-conversation streaming turn assembly), activities, tasks, approvals, beliefs, skills, voice, spend. Components subscribe via selectors (a `token` frame re-renders only the streaming bubble, not the tree). Because the reducer is pure, its selfcheck is a frame-log replay asserting the final state — the store is testable without React, a browser, or a WS.

**D6 — Reconnect safety = idempotent upserts + snapshot re-push.**
On (re)connect after `hello_ack`, the mock pushes a snapshot: all `belief_state`s, `skill_state`s, live `task_state`s, pending `approval_request`s, and a `spend_update`. Every store mutation is an upsert keyed by domain id (`belief_id`, `skill_name`, `task_id`, `approval_id`), so replays converge instead of duplicating. On WS drop, open streaming turns close with an "interrupted — connection lost" marker (a turn is never visually stuck streaming), and pending approval cards persist (they wait forever, per spec) until the snapshot reconciles them.

**D7 — Arrival order is render order.** `ts` is display-only; the UI never sorts by it. Ordering guarantees come from the Brain's per-conversation serialization (Phase 0) and TCP.

**D8 — New dependencies, each earning its place (ponytail-gated):**
- `zustand` (~1kB) — selector-based subscriptions; Context+useReducer re-renders every consumer on every token frame, which dies at streaming rates.
- `@tanstack/react-virtual` — the virtualized activity log is mandated by [techstack/10-ui](techstack/10-ui.md); variable-height rows rule out hand-rolling.
- `react-markdown` — chat spec requires full markdown; a hand-rolled parser is the classic over-engineering trap. **Raw HTML stays disabled** (default) — an error-prevention rule that matters in Phase 2 when model output flows through the same component.
- `lucide-react` — the design language mandates Lucide, "never emoji".
- Tauri plugins `global-shortcut` (hotkey summon), `notification` (Tier-3 away-toast), `window-state` (workspace size/pos + orb position persistence — official plugin over hand-rolled JSON).
- **Not added:** animation library (motion tokens are ≤400ms CSS transitions — CSS covers it), router (sidebar switching is local state, no URLs), syntax highlighter (deferred), CSS framework (custom CSS per techstack).

**D9 — Browser fallback stays alive.** `npm run dev` without Tauri must keep working for fast iteration: all Tauri API calls sit behind a `isTauri()` guard, and `?window=orb|workspace` selects which root renders in a plain browser tab (session.json read falls back to a fetch of a dev-served copy, as in Phase 0).

---

## Cross-cutting error-prevention rules (apply to every step)

1. **Validate at the boundary:** every inbound frame passes the `contract.ts` validator before touching the store; invalid/unknown frames are logged and dropped — a malformed frame may never crash a render.
2. **Idempotent upserts** keyed by domain ids (D6) — reconnects and snapshot re-pushes converge.
3. **No double-fire on consequential clicks:** Approve/Deny/Edit disable on first press; the card shows a pending spinner and resolves only on the Brain's confirming frame (`task_state`/`activity`), never optimistically.
4. **No color-only state:** every chip/state pairs color with text or icon (a11y + the sidebar rule "never color alone").
5. **Motion and blur are capability-gated:** all non-essential animation behind `prefers-reduced-motion`; all glass behind a transparency check with solid `--bg`-tinted fallback. Nothing is readable only through blur.
6. **Autoscroll only when pinned:** chat/feed auto-follow only if the user is at the bottom; never yank scroll from someone reading history.
7. **IME guard:** `Enter` during composition (`event.isComposing`) never sends — CJK input would otherwise fire mid-word.
8. **A turn is never lost:** `error` frames and disconnects restore the user's text to the input box (per chat spec).
9. **Off-screen clamp:** on start, if a remembered window/orb position is outside every current monitor (monitor unplugged), clamp to the nearest visible edge.
10. **Copy voice:** every user-facing mock string follows the copy rules — first person, one sentence, plain cause + way forward, no jargon.

---

## Step 1 — Contract additions for panel data

**Intent:** Close the gaps between the 21-type Phase-0 contract and what the panels actually consume. Today the Memory and Skills panels have **no data source**, the Activity feed's Undo button has **no message to send**, task cards have no title/step, and approval cards have no human sentence. Extend the contract *first* so every later step builds against real shapes — mirrored across `shared/ipc-contract.json`, `contract.ts`, `contract.py`, and documented in [11-ipc-contract.md](systemdesign/11-ipc-contract.md).

**New message types:**
- Inbound `undo` — `{undo_token}`: the feed's Undo button. (The `activity` frame already carries `undo_token` out; nothing carried it back.)
- Outbound `belief_state` — `{belief_id, text, kind:"preference"|"project"|"workflow"|"decision"|"lesson", provenance:"user"|"inferred", salience, status:"active"|"archived"|"superseded", superseded_by?, used_at?}`: memory panel cards, pushed as snapshot-on-connect + delta-on-change (same pattern as `task_state`).
- Outbound `skill_state` — `{skill_name, origin:"auto"|"user", kind:"skill"|"playbook", uses, success_rate, status:"active"|"paused"|"retired", born_at, reason?}`: skills panel cards, same snapshot+delta pattern.

**Extended types (optional fields only — Phase-0 validators keep passing, no required-field changes):**
- `task_state` + `title?, step?, steps_total?, step_label?, reason?` — task cards show "Reorganizing Downloads · step 4/9 — moving PDFs" and paused-why.
- `approval_request` + `summary?` (the one plain sentence the card leads with — authored by the Brain because the UI holds no business logic) and `destructive?:bool` (drives the red-border / hold-to-approve / no-voice-approval variant).
- `activity` + `tier?:1|2|3, lane?:1|2|3` — the feed renders tier chips and lane per entry and filters on them.

**Edge cases:** payload fields never named `id` (envelope collision rule); enums validated on both sides; unknown optional fields tolerated by old readers.
**Deliverables:** all three contract files + the systemdesign doc updated together; a `mem/Decisions.md` entry for the snapshot+delta pattern.
**Acceptance:** `python shared/check_contract_sync.py` passes; both language selfchecks pass; every field a Step-5→14 component renders exists in the schema.

---

## Step 2 — Mock Brain scenario player

**Intent:** Swap the echo handler for a scenario engine behind `python -m brain --mock` (D1, D2). Scenarios are data — ordered lists of `(delay_ms, frame)` steps plus await-points — replayed through the existing `_send()`/`_envelope()` path so every frame is contract-validated at emission.

**Design:**
- `brain/brain/mock.py`: scenario table keyed by trigger keyword; a generic streamed reply (20–40ms token cadence — realistic feel) for everything else.
- **Reactive await-points:** the approval scenario emits `approval_request` then *suspends until* the matching `approval_response` arrives, branching approve/deny/edit; `demo destructive` sets `destructive:true`; `undo` inbound is honored with a reversal `activity` referencing the same task.
- **Snapshot-on-connect** (D6): after `hello_ack`, push seeded `belief_state`s (all five kinds, both provenances, one superseded chain, one archived), `skill_state`s (auto + user + playbook, one <60% success, one retired), two live `task_state`s, pending approvals, `spend_update`.
- `demo everything`: the full walkthrough for exit-criterion 3. `demo flood`: 2,000 activities for exit-criterion 5. `demo voice`: scripted `voice_state` transitions + `transcript` partials→final→`user_msg`-shaped turn, mimicking the Phase-3 Voice worker's real emission pattern.
- Broadcast outbound frames to all authenticated clients (D4) — replaces reply-to-sender in `server.py`.

**Edge cases:** `interrupt` during a pending approval → cancel it (implicit deny per contract) then emit `task_state: paused` — this is what lets Step 10 prove the stale-card rule; two clients approving the same card → first `approval_response` wins, second gets an `error` frame (recoverable); scenario running when client disconnects → scenario aborts cleanly, no writes to a dead socket.
**Deliverables:** `--mock` flag; scenario module; broadcast send; a plain-asyncio selfcheck (`brain/tests/test_mock.py`) asserting: trigger → expected frame sequence, all frames validate, approval await-point branches correctly, snapshot arrives after `hello_ack`.
**Acceptance:** `python -m brain --mock` + the Phase-0 smoke test still passes (auth, reconnect, supervision untouched); `test_mock.py` green; `dev.ps1` gains a `-Mock` switch.

---

## Step 3 — Design tokens, theme & primitives

**Intent:** Encode [ui_ux/00-design-language.md](ui_ux/00-design-language.md) as the CSS foundation every component inherits — before any panel exists, so no component ever hardcodes a hex or a duration.

**Design:**
- `ui/src/styles/tokens.css`: every color token as CSS variables under `:root` (light) and `[data-theme="dark"]`; motion tokens (`--motion-fast: 150ms` etc.); type scale; elevation scale (companion < panel < card < modal). Theme: `auto` follows `prefers-color-scheme`, manual override persisted.
- `ui/src/styles/glass.css`: the one glass recipe (blur 24px, 1px light border) as a class + the **reduced-transparency fallback** (`prefers-reduced-transparency` media query *and* a manual setting) swapping to solid tinted surfaces. No stacked blurs — lint by convention: only `.glass` applies blur.
- `prefers-reduced-motion`: one global rule zeroing non-essential transitions; state changes fall back to color/opacity.
- Primitives in `ui/src/components/`: `GlassPanel`, `Button` (primary/ghost/destructive — destructive is red, physically separated, confirms), `Chip` (tier/lane/status — icon + text, never color alone), `Icon` (Lucide wrapper locked to 1.5px stroke, 16/20/24), focus ring (2px `--primary`, 2px offset, never removed).

**Edge cases:** contrast ≥4.5:1 for text and ≥3:1 for muted *checked per mode* (dark is designed, not inverted); focus visible **on glass**; click targets ≥32px even for icon buttons.
**Deliverables:** token/glass stylesheets, primitives, a `/dev/tokens` throwaway route (browser fallback, D9) rendering every primitive in both themes × both fallback modes for eyeball QA.
**Acceptance:** no raw hex/ms literals outside `tokens.css` (greppable); all four render modes (light/dark × glass/solid) legible; keyboard focus visible everywhere.

---

## Step 4 — UI event store

**Intent:** The single projection of IPC events into renderable state (D5, D6, D7) — the architectural heart of the shell. Transport (`useHaloConnection`) stays untouched; it gains one consumer: `store.applyFrame`.

**Design:**
- `ui/src/state/reducer.ts`: pure `applyFrame(state, frame): state`. Streaming assembly: `token` appends to the conversation's open assistant turn (creating it on first token); `done` closes it; `error` closes it with the error attached and flags input-restore.
- `ui/src/state/store.ts`: zustand wrapper + selectors per slice.
- Caps: activities ring-buffer capped at 10,000 in memory (the full log is a Phase-2 SQLite concern); conversations keep full turns (chat history is the product).
- Connection slice merges *two distinct signals* (the Phase-0 rule): WS connected+authenticated (drives input/reconnect UI) vs `sidecar-state` process health (drives the "Brain failed to start" banner). Never conflated.
- On WS close: close open streaming turns with the interrupted marker; keep approvals; mark connection reconnecting.

**Edge cases:** `token` for an unknown `conversation_id` → open a turn anyway (arrival order is truth — the `user_msg` echo may be local-only); duplicate snapshot pushes → upserts converge (D6); `task_state` for an unknown task → create it (snapshot may race deltas); `approval_response` confirmation path (rule 3) — approvals resolve on `task_state`/`activity`, not on button press.
**Deliverables:** reducer + store + `ui/src/state/reducer.selfcheck.ts`: replays a canned frame log (happy path, reconnect mid-stream, duplicate snapshot, approval round-trip, undo) and asserts the projected state — runnable via `npx tsx`, matching the repo's no-framework rule.
**Acceptance:** selfcheck green; `npx tsc --noEmit` clean; token-frame dispatch re-renders only the subscribed bubble (verified with React DevTools highlight during `demo everything`).

---

## Step 5 — Window architecture: orb + workspace

**Intent:** The two-window topology (D3): a permanent orb and a summonable workspace, with the Rust side owning window lifecycle and the hotkey.

**Design:**
- `tauri.conf.json`: `orb` window (64×64, transparent, no decorations, always-on-top, skip-taskbar, `focusable:false`) + `main` workspace (hidden at start, resizable, min 720×480).
- Global hotkey (`Alt+Space` default) via the global-shortcut plugin → Rust command `toggle_workspace(orb_x, orb_y)`; workspace opens with the CSS scale+fade transform-origin set to the orb's position; `Esc` in the workspace runs the reverse and hides (never quits — the exit animation is ~70% of 250ms per motion rules).
- Orb dragging: manual (pointer events) with a **4px movement threshold** discriminating click (→ expand) from drag; on release, snap to the nearest screen edge; position persisted by window-state plugin.
- Tray icon (quit/status secondary affordance) + right-click orb menu: Mute mic · Pause all tasks · Open workspace · Quit. Quit is explicit — closing the workspace never kills the app.
- Shutdown ordering (Phase-0 gotcha): quit sets the supervisor shutdown flag before killing sidecars.

**Edge cases:** hotkey already registered by another app → fall back to secondary (`Ctrl+Alt+Space`) and surface a status-strip note (cause + way forward); remembered position off-screen → clamp (rule 9); orb must never steal focus even when clicked (no-activate — expansion focuses the *workspace*, not the orb); always-on-top vs fullscreen games is accepted as a platform limitation, noted in `mem/Gotchas.md`; both windows in `npm run dev` browser mode via `?window=` (D9).
**Deliverables:** window config, Rust toggle/position commands, orb drag+snap, tray + context menu, hotkey with fallback.
**Acceptance:** hotkey summons from anywhere; click expands with spatial continuity; `Esc` collapses; orb drags, snaps, remembers position across restarts, never takes focus; quit tears down all three processes cleanly.

---

## Step 6 — Workspace shell: sidebar, status strip, view routing

**Intent:** The frame every panel lives in, per [ui_ux/02-workspace.md](ui_ux/02-workspace.md).

**Design:**
- Left sidebar: Chat, Tasks, Activity, Memory, Skills + Settings bottom-separated; icon + label; active = color **plus** left indicator; amber count badge on Tasks from the approvals slice.
- Status strip: lane chip (visible whenever a task runs), mic state, compact running-task chip (name + progress + stop → `task_op`).
- View routing = local state (D8: no router); **each view stays mounted and hidden** (CSS, not unmount) so scroll and state survive switching — the cheapest correct implementation of the "preserves each view's scroll" rule.
- `Ctrl+K` focuses the chat input from anywhere; tab order = visual order.
- Deep-jump plumbing: a store-level `focusTarget` (approval/task id) that orb badge clicks, toasts, and task chips set, and views consume on next render.

**Edge cases:** badge count and lane chip derive from the store only (no local mirrors to drift); all six views mounted-hidden is fine at this scale — the feed is virtualized (Step 8) so hidden views hold no heavy DOM ("mounted ≠ rendered rows").
**Deliverables:** workspace layout, sidebar, status strip, view switcher, deep-jump mechanism, keyboard path.
**Acceptance:** switching views preserves scroll/state; badge reflects pending approvals live; `Ctrl+K` works from every view; keyboard-only navigation reaches everything.

---

## Step 7 — Orb state language & peek bubble

**Intent:** The orb's nine states per [ui_ux/01-companion-orb.md](ui_ux/01-companion-orb.md) — 95% of the relationship.

**Design:**
- A pure `deriveOrbState(store) → state` selector encoding the priority rule **approval > error > task > voice**, one state at a time; the component just renders the result. The selector gets unit cases in the reducer selfcheck (it's the one piece of UI logic subtle enough to regress silently).
- Visuals: breathing glow (4s), ripple-on-wake, listening rim + level-reactive ring, thinking swirl, speaking pulse, progress arc (task), amber 2-pulse-then-steady (approval, + count badge), one red flash then persistent badge (error), gray slashed-mic (muted — always visually loud).
- Peek bubble: slides from the orb (200ms), auto-dismisses in 4s, hover pins; fed by `activity(narrate:true)` lines, live `transcript` partials, and skill-birth notices. **Never approvals** — those get ring + card.
- Reduced motion: every state also exists as pure color/opacity.

**Edge cases:** state priority when several are true simultaneously (approval while speaking while task runs) — the selector, not CSS, decides; error badge persists until the error is seen (workspace opened), never a modal from the orb; mic-muted overrides glow entirely.
**Deliverables:** orb component + state selector + peek bubble; hover tooltip ("what I'm doing") over the progress arc.
**Acceptance:** `demo everything` walks every state visibly correct in both motion modes; amber badge click deep-jumps to the card; selector cases green in the selfcheck.

---

## Step 8 — Chat view

**Intent:** The default view per [ui_ux/03-chat.md](ui_ux/03-chat.md) — a conversation, not a terminal.

**Design:**
- Bubbles: user right/baby-blue glass, Halo left/plain surface, 16px body; streamed tokens render as they arrive from the store's open turn.
- `react-markdown` (raw HTML off — rule); code blocks in JetBrains Mono with copy button, collapsible >20 lines.
- "What I did" rows: activities sharing the turn's `task_id` render as a slim tool-icon row under the message; expanding shows the entries inline without leaving chat.
- States: thinking dots ≤300ms (replaced by live narration line when a tool runs); interrupted divider ("stopped · what should I do differently?"); in-bubble errors with cause + Retry, input text restored (rule 8); first-run empty state with 3 example chips (wired to `demo …` triggers — honest examples that actually work).
- Input: `Enter` sends / `Shift+Enter` newline / IME guard (rule 7); mic button mirrors orb state; disconnected → input stays usable, sends queue with the "will send when reconnected" badge (Phase-0 queue does the actual queueing — the badge is just truthful UI over it).
- Voice turns: small mic glyph; `transcript` partials as ghost text that solidifies on `final:true`.

**Edge cases:** autoscroll only when pinned (rule 6); very long streams stay smooth (append to one bubble, no per-token elements); copy button gives ≤300ms feedback; markdown link clicks open the system browser (Tauri opener), never navigate the webview.
**Deliverables:** chat view, message components, markdown rendering, input, empty state.
**Acceptance:** generic demo streams smoothly; `demo error` shows the in-bubble error and restores input; ghost-text voice turn renders during `demo voice`; queued badge appears when the Brain is killed and clears on reconnect.

---

## Step 9 — Activity feed

**Intent:** The flight recorder per [ui_ux/06-watching-halo-work.md](ui_ux/06-watching-halo-work.md).

**Design:**
- `@tanstack/react-virtual` timeline, newest first, variable-height rows: icon + sentence + tier chip + lane + timestamp.
- **Undo** button where `undo_token` exists → sends the new `undo` message; button enters pending state until the reversal `activity` arrives (rule 3); `undoable:false` entries show "not reversible" up front — the UI never implies false safety.
- Filters (tier ∧ lane ∧ task ∧ undoable-only) + text search — all client-side over the capped slice.
- Designed empty state.

**Edge cases:** 2,000-entry `demo flood` scrolls at 60fps; filter changes reset virtualizer measurements correctly (variable heights); undo double-click can't double-send; new entries while scrolled up don't yank position (rule 6) — a "new activity ↓" pill instead.
**Deliverables:** feed view, entry component, filters, search, undo wiring.
**Acceptance:** flood is smooth; undo round-trip renders the reversal; filters compose; tier/lane chips are icon+text (rule 4).

---

## Step 10 — Approval cards & trust surfaces

**Intent:** The consent surface per [ui_ux/05-permissions-trust.md](ui_ux/05-permissions-trust.md) — the most trust-critical component in the app; edge cases here *are* the feature.

**Design:**
- One `ApprovalCard` used in two anchors: bottom-center overlay of the current view, and inline in chat at the pause point. Content: `summary` sentence, truncated expandable payload, expandable redacted args.
- **Approve** primary blue · **Deny** ghost (never red — denying is safe) · **Edit** opens args inline, edited args go back in `approval_response.edited_args`.
- **Destructive/money variant** (`destructive:true`): red border, bolded consequence in the sentence, **700ms hold-to-approve** (press-and-hold with visible progress; keyboard equivalent: hold `Enter`), voice approval disabled by design.
- Cards wait forever — no timeout, nothing approved by timeout, ever.
- Away flow: `approval_request` while workspace hidden → Windows toast via notification plugin; click opens workspace deep-jumped to the card; orb stays amber throughout.

**Edge cases:** the **stale-card rule** — `interrupt` while waiting → mock cancels (implicit deny) and emits `task_state: paused` → the UI removes the card; a card that was already responded to (double-window race, D4) → second response gets the mock's `error` frame and the card shows "already handled"; buttons disable on press with pending spinner until confirmation (rule 3); hold-to-approve cancels if the pointer leaves the button; `Esc` never dismisses an approval card (collapse ≠ deny — the card is still there on next expand).
**Deliverables:** card component + variants, hold-to-approve, toast wiring, deep-jump target consumption, inline-in-chat anchor.
**Acceptance:** `demo approval` full round-trip (approve, deny, and edit paths); `demo destructive` requires the hold and blocks reflex clicks; toast → deep-jump lands on the card; interrupt removes the card.

---

## Step 11 — Tasks view & lane chip

**Intent:** Everything in flight per [ui_ux/09-tasks.md](ui_ux/09-tasks.md), fed by the extended `task_state`.

**Design:**
- Cards ordered: running → waiting-approval (amber, embedding their approval card) → paused (showing `reason`) → recent-done (24h, collapsed). Title, lane chip, `step/steps_total` + `step_label`, pause/stop → `task_op`.
- Resumability made visible: paused cards say "will continue from step N"; post-crash tasks reappear paused with "resumed safely — Brain restarted" (scripted in the reconnect snapshot — recovery is a visible feature).
- Lane chip states 🟦 Fast / 🟨 Takeover / 🟪 Sandbox with the chip dropdown sending `lane_pin`; sandbox cards host the stream tile rendering scripted `stream_frame`s (design ships, lane deferred).
- Status-strip task chip mirrors the top running task.

**Edge cases:** `task_op` with omitted `task_id` = all tasks (orb menu "Pause all"); pause/stop buttons follow rule 3 (disable-until-confirm); `stream_frame` throttling — render latest frame only, drop stale ones by `seq` (never queue jpegs); done-cards collapse after 24h by `ts` display logic only.
**Deliverables:** tasks view, task card, lane chip + pin dropdown, stream tile.
**Acceptance:** `demo task` walks running → step progress → waiting_approval (embedded card) → paused ("you said stop") → resume → done; lane chip always visible while running; stream tile renders frames.

---

## Step 12 — Memory panel

**Intent:** The inspectable second brain per [ui_ux/07-memory-panel.md](ui_ux/07-memory-panel.md), fed by `belief_state` and driving `memory_edit`.

**Design:**
- Cards grouped by kind, searchable, salience-sorted; provenance chip is the star: 🗣 *you said* vs ✨ *Halo inferred*, visually distinct (icon + label, rule 4).
- Edit inline → `memory_edit{op:"edit"}` → mock replies with a superseding user-stated `belief_state` (the provenance rule made visible). Delete → soft: 5s undo toast, then archived — recoverable from the archived filter indefinitely; restore → `memory_edit{op:"restore"}`.
- Superseded chains expand (⌄) showing history, any version restorable; archived view shows decayed beliefs (salience <0.2).
- Auto-correct delight: mock scripts a mid-conversation `belief_state` change + `activity(narrate:true)` peek line ("updated what I remember — you switched to pnpm"), click = deep-jump to the card.

**Edge cases:** edits follow rule 3 (pending until the confirming `belief_state` lands — the store never mutates a belief locally); the undo toast timer and the archived state can't race (archive happens at toast expiry *or* is reconciled by the next `belief_state`, whichever first — id-keyed upsert makes both paths converge); empty state per spec.
**Deliverables:** memory view, belief card, chains, archived filter, soft-delete toast.
**Acceptance:** `demo memory` shows edit/delete/restore round-trips, a superseded chain, and the auto-correct moment; nothing hard-deletes.

---

## Step 13 — Skills panel & Settings

**Intent:** The remaining two views — mechanically similar card surfaces, per [ui_ux/08-skills-panel.md](ui_ux/08-skills-panel.md) and the workspace settings spec.

**Design:**
- Skills: cards in Auto-learned ✨ / User-made 🛠 groups + Playbooks filter; uses × success-rate bar (red-tinted <60% — the retire threshold visible before it fires); trial/pause/delete → `skill_op`; born-moment peek line; retired cards gray with reason + restore.
- Settings (lean, single scroll, grouped per spec): General (hotkey display, theme, launch-at-startup placeholder), Voice toggles, Models (IDs shown from mock, month spend from `spend_update`), Keys & connections as status dots with ●●● placeholders (no real key entry — Phase 2), Advanced collapsed with defaults shown. Toggles send `settings_update`; theme applies locally + persists.
- Trial run: `skill_op{op:"trial"}` → mock streams a scripted run into a results drawer.

**Edge cases:** skill ops follow rule 3; a Tier-3 skill edit attempt scripts a standard approval card (the panels share the trust surface); settings that need a real backend render disabled with an honest note rather than pretending.
**Deliverables:** skills view + card + trial drawer; settings view; spend display.
**Acceptance:** `demo skill` shows birth (peek + feed + undo), trial run, pause, auto-retire with reason, restore; theme switch is instant across both windows; spend renders from `spend_update`.

---

## Step 14 — Voice presence (scripted)

**Intent:** Make the voice *feel* real before Phase 3 builds it: the orb loop, ghost text, and degraded modes, driven entirely by mock-scripted `voice_state`/`transcript` frames. The Voice sidecar itself stays the Phase-0 idle stub.

**Design:**
- `demo voice` scripts the full loop: wake ripple + chime → listening rim + `transcript` partials as ghost text (chat + peek) → thinking swirl ≤300ms after final → speaking pulse + simultaneous `token` stream → done. A barge-in beat: `voice_state:"listening"` mid-speaking → pulse stops instantly.
- Mic button + orb menu send `mic{op}`; mock replies `voice_state:"muted"` — gray slashed orb everywhere, always (mute is visually loud; there is no ambiguous mic state).
- Degraded modes as scripted scenarios: TTS-down (replies as text + chime + status-strip note) and STT-down (listening disabled with tooltip, typing works) — errors state cause + way forward per copy rules.

**Edge cases:** every acknowledge-step ≤300ms (latency is a feature — the orb never looks frozen); transcript `final:true` coincides with the turn appearing as a real message (glyph swap, no duplicate bubble); muted state wins over all voice states in the orb selector.
**Deliverables:** voice scenario, ghost-text wiring, mute round-trip, degraded-mode notes.
**Acceptance:** `demo voice` renders the full loop including barge-in; mute is unambiguous in orb + chat + status strip simultaneously; degraded modes read honestly.

---

## Step 15 — Demo walkthrough, a11y/perf pass & phase E2E

**Intent:** Lock the exit criteria behind repeatable checks, mirroring Phase 0's Step 8.

**Deliverables:**
- `demo everything` finalized to walk every exit-criterion-3 beat in ~90 seconds.
- `shared/phase1_check.py` (plain asyncio+assert): connects as a fake UI client to `--mock`, triggers each scenario, and asserts the *frame sequences* — every frame contract-valid, approval round-trips branch, undo reverses, snapshot idempotent on reconnect. (Store-side logic is already covered by the reducer selfcheck; visual rendering is the manual checklist — no browser-automation framework enters the repo for this.)
- `VERIFY.md` manual checklist: the four render modes, keyboard-only pass, reduced-motion/transparency pass, flood-scroll smoothness, kill-Brain recovery, both themes across both windows.
- `dev.ps1 -Smoke` extended to run the Phase-1 check after the Phase-0 one.

**Acceptance:** all automated checks green in one run from `dev.ps1`; the manual checklist completes with no blocking finding; Phase-0 smoke still green (nothing regressed in the transport).

---

## Build order & dependencies

```
Step 1 (contract additions)
   ├─> Step 2 (mock Brain) ──────────────┐
   └─> Step 4 (event store) ─────────────┤
Step 3 (tokens & primitives) ────────────┤
                                         ├─> Steps 8–14 (panels — parallel-friendly)
Step 5 (windows) ─> Step 6 (workspace) ──┤        8 chat · 9 feed · 10 approvals
                    Step 7 (orb) ────────┘        11 tasks · 12 memory · 13 skills/settings
                                                  14 voice presence
                                                       └─> Step 15 (E2E & polish)
```

Steps 2, 3, 4, 5 can proceed in parallel once Step 1 lands (2 and 4 both consume the contract; 3 and 5 don't touch it). Panels need the store (4), primitives (3), and the shell (6); they are then independent of each other. Step 10 (approvals) is the highest-risk/highest-value panel — schedule it early in the panel batch, not last. Step 15 closes the phase.
