# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**H.A.L.O.** — a local resident desktop AI companion built with Tauri/React, a Python/LangGraph Brain, and a Python Voice worker over authenticated loopback WebSocket. Phases 0–2 are implemented; the default non-`--mock` Brain is the real Phase 2 backend, while Voice remains an authenticated idle sidecar. The 2026-07-22 hardening audit and 2026-07-28 whole-repo deep-scan remediation are complete for automatable scope; the remediation was committed as `116d4af`, and the full gate was green on 2026-07-29. Human visual/NVDA and real-key OpenRouter walkthroughs remain in [VERIFY.md](VERIFY.md). Phase 3 has not started; read [PHASE3_READINESS_AUDIT.md](PHASE3_READINESS_AUDIT.md) and the required task-runtime split in [systemdesign/12-task-runtime.md](systemdesign/12-task-runtime.md) before building any long-running-task feature.

The repo has two layers: design docs (source of truth for *behavior* and *architecture*) and the code that implements them.

- **[Halo-PRD.md](Halo-PRD.md)** — product spec: *what* Halo is and *how it behaves* (capabilities, control lanes, permissions, memory, self-improvement). Stack-agnostic by design — keep tech choices out of it.
- **[systemdesign/](systemdesign/00-overview.md)** — architecture per feature. **[11-ipc-contract.md](systemdesign/11-ipc-contract.md) is the canonical spec for the process model and message envelope** — read it before touching any cross-process code. **[12-task-runtime.md](systemdesign/12-task-runtime.md)** is a design-only doc (not yet implemented) for how long-running tasks detach from interactive chat turns — read it before starting Phase 3a or any feature that runs longer than a turn.
- **[techstack/](techstack/00-stack-summary.md)** — concrete technology choice per feature.
- **[ui_ux/](ui_ux/00-design-language.md)** — visual/interaction spec (tokens, motion, copy voice). Check `00-design-language.md` for existing tokens before inventing new ones.
- **[phases.md](phases.md)** — the phase-by-phase build roadmap (Phase 0 skeleton → Phase 1 front-end shell → Phase 2 backend spine → Phase 3 heavy systems).
- **[phase-0-plan.md](phase-0-plan.md)** — the 8-step Phase 0 implementation plan and its exit criteria (all met — see Commands below to re-verify).
- **[phase-1-plan.md](phase-1-plan.md)** — the completed 15-step front-end shell plan.
- **[phase-2-plan.md](phase-2-plan.md)** — the completed 10-step backend spine plan.
- **[VERIFY.md](VERIFY.md)** — automated and native verification status; the Phase 2 native checklist still needs a human run with a real OpenRouter key.
- **[DEEPSCAN_AUDIT.md](DEEPSCAN_AUDIT.md)** — the 2026-07-28 evidence ledger for the whole-repo audit. Its findings and tranche plan are historical provenance; the remediation is complete in `116d4af`. Current durable status and follow-ups live in `mem/Memory.md` and [VERIFY.md](VERIFY.md).

Each `systemdesign/`/`techstack/`/`ui_ux/` folder numbers files by feature (`01-chat`, `02-voice`, `03-memory`, …) — the same number across folders covers the same feature from architecture, technology, and UI angles. When changing one, check whether the matching file in the others needs to move with it. Treat the PRD as the source of truth for behavior; if a design decision changes a PRD claim, update the PRD too.

## Commands

Three independent process trees (`ui/` Rust+Node, `brain/` Python, `voice/` Python) — there is no single root build/test command.

**Run everything for local dev:**
```powershell
./dev.ps1              # launches Tauri (UI parent spawns brain, voice sidecars) — stable/attached by default
./dev.ps1 -Only ui      # just one of: ui | brain | voice (for standalone debugging)
./dev.ps1 -Mock         # full app, but Brain launches with --mock (HALO_MOCK) for scripted demo scenarios
./dev.ps1 -Smoke        # runs Phase 0, Phase 1, and Phase 2 automated gates in-place (no windows)
./dev.ps1 -WatchNative  # opt into normal Vite + Rust hot reload instead of the stable build (see below)
```
The default `./dev.ps1` (no flags) launches the real Phase 2 Brain. It does not respond to `demo ...` triggers; use `-Mock` to drive scripted UI scenarios in `brain/brain/mock.py`. After the automated gate passes, [VERIFY.md](VERIFY.md) is the manual native-app checklist (render matrix, keyboard/a11y, performance/recovery, and the real-key Phase 2 walkthrough) — run the mock app for visual/runtime surfaces and the normal app with a real OpenRouter key for Phase 2 behavior, since green scripts alone don't catch rendering or lifecycle bugs.

**Launcher runs attached and stable by default (since 2026-07-17).** `./dev.ps1` now runs in its own terminal (not a detached `Start-Process`), holds a named mutex (`Local\HaloDevLauncher`) so a second launcher refuses to start, and by default builds once (`tauri.stable.conf.json`, `npm run dev:stable`, `vite preview`, `tauri dev --no-watch`) instead of live-reloading. Pass `-WatchNative` to get the original Vite dev server + native Rust hot reload back. This exists because agent-driven edits that touch many files (metadata-only rewrites included) were triggering repeated HMR/Tauri rebuilds under the old default watcher setup — see Decisions.md "Stable attached launcher by default." If you're iterating interactively on UI/Rust code and want hot reload, use `-WatchNative`; leave the default alone for anything scripted/automated.

**Mocked Brain for standalone UI development** (no Tauri, browser-only iteration):
```powershell
python -m brain --mock   # starts Brain in mock mode with scripted scenarios
# Then launch UI separately with npm run tauri dev or npm run dev
```

**UI (`ui/`, Tauri + Vite + React + TS):**
```powershell
npm install
npm run tauri dev       # native window; needs Rust/cargo + MSVC Build Tools (C++ workload) on Windows
npm run dev              # browser-only fallback, no Rust toolchain needed
npx tsc --noEmit         # typecheck
node ui/src/ipc/contract.selfcheck.ts   # contract self-check (from repo root)
node ui/src/ipc/queue.selfcheck.ts      # interrupted-queue-flush self-check (from repo root)
npx tsx ui/src/state/reducer.selfcheck.ts   # event-store reducer self-check: replays a canned frame log and asserts projected state
```
Rust side (`ui/src-tauri/`):
```powershell
cargo build
cargo test               # runs the backoff-ladder unit test in supervisor.rs
```

**Brain (`brain/`, Python 3.11+):**
```powershell
python -m brain                          # starts the WS server, writes session.json
python brain/tests/test_server.py        # auth/ordering tests (plain asyncio+assert, no pytest)
python shared/phase2_check.py             # real-Brain E2E gate with offline LLM/extraction stubs (from repo root)
python -m brain.ipc.contract             # contract self-check
```

**Voice (`voice/`, Python 3.11+):**
```powershell
pip install -e ../brain      # from voice/'s env — voice imports brain.ipc.contract
python -m voice
python voice/tests/test_client.py
```

**Cross-language IPC contract drift check** (run after editing the schema or either mirrored type file):
```powershell
python shared/check_contract_sync.py
```

**Phase 0 exit-criteria smoke test** (single command proving the whole skeleton — auth round-trip, wrong-token rejection, voice auth, kill-Brain→respawn-on-new-port→reconnect):
```powershell
python shared/smoke_test.py
```

**Phase 1 exit-criteria protocol check** (connects as a fake UI client to `--mock`, triggers every `demo *` scenario, asserts frame sequences — contract-valid frames, approval approve/deny/edit branches, undo reversal, reconnect snapshot idempotence; visual rendering is out of scope here, that's [VERIFY.md](VERIFY.md)):
```powershell
python shared/phase1_check.py
```
**Phase 2 exit-criteria check** (fake UI client against the **real** Brain over a real WS, offline+deterministic via the `HALO_LLM_STUB`/`HALO_EXTRACT_STUB` seams — no paid API calls; asserts real chat streaming, all three tier behaviors + approve/deny/edit + implicit-deny-on-interrupt, a file op + undo round-trip on disk, memory surviving a Brain restart with provenance rejection, a pending approval surviving a kill and still resuming, snapshot idempotence, and key-missing honesty):
```powershell
python shared/phase2_check.py
```
All three run together via `./dev.ps1 -Smoke`. Backend and cross-process checks use plain `asyncio` + `assert` scripts; the UI also has scoped Vitest tests. Don't introduce another test framework without a real reason.

## Architecture

**Process model — Tauri is the parent.** On app start, `ui/src-tauri` spawns `brain` and `voice` as plain child processes (`std::process::Command`, run from source via `python -m brain`/`python -m voice` — no packaging yet, that's a later phase; see the `// ponytail:` comment in `supervisor.rs` for the packaged-binary path this will need). The Rust supervisor (`ui/src-tauri/src/supervisor.rs`) watches each child, restarts on a 1s/5s/30s backoff ladder with a healthy-uptime reset, and — critically — sets a shutdown flag *before* killing children on app exit, otherwise the supervision loop misreads the intentional kill as a crash and respawns it. It also explicitly kills children on exit since Windows doesn't reap them when the parent dies. `backoff_delay()` is a pure function with its own unit test, kept separate from the process-spawning loop.

**Session handshake.** The Brain holds a crash-safe OS lock so only one instance can own `session.json`, binds a random free loopback port, and atomically writes `{port, token}` to `%LOCALAPPDATA%\Halo\session.json`. **Every client must re-read this file fresh on every connect/reconnect attempt** — the Brain gets a new port on every restart, so caching the port anywhere causes silent reconnect failure. Every WS connection's first frame must be `{type:"hello", token}` matching that file; the Brain enforces this with `secrets.compare_digest`, silently drops failures, and sends `hello_ack` on success. Clients must not send or flush application messages before that acknowledgement.

**IPC contract — one schema, two hand-mirrored implementations.** `shared/ipc-contract.json` is the single source of truth for every message type and its required fields. `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py` are hand-mirrored from it (not codegen — deliberate, for simplicity at this scale). `shared/check_contract_sync.py` fails if the three diverge. The envelope is `{type, id, ts, ...payload}` — payload fields must never reuse the key `id` for a domain-specific identity (it collides with the envelope's own message id; this is why `approval_request`'s field is `approval_id`, not `id`).

**Tauri↔React state events are separate from the WS contract.** The supervisor emits a webview event (`"sidecar-state"`, via `tauri::Emitter::emit`) carrying OS-process health (`starting`/`running`/`restarting`/`error`) — this is not part of `shared/ipc-contract.json` and never should be. The UI keeps two genuinely distinct pieces of state: whether the WebSocket itself is connected+authenticated (drives the chat input and reconnect indicator) versus whether the Brain *process* is alive (drives a separate "Brain failed to start" banner from sidecar-state). Don't conflate them.

**Conversation serialization.** The Brain keeps an `asyncio.Lock` per `conversation_id` in `brain/server.py`, so concurrent messages to the same conversation are handled in arrival order. This lock must be held by the shared dispatch boundary (not buried inside individual turn handlers), so every Brain mode (real, mock, test) routes through the same serialization point and the pattern can't be accidentally bypassed.

**IPC envelope `id` field is reserved.** The `id` in `{type, id, ts, ...payload}` is the message envelope's own message ID. Payload fields must never use `id` as a key (e.g., `approval_request` uses `approval_id`, not `id`) — otherwise the payload value would collide and overwrite the envelope's message ID when flattened.

**UI WS client** (`ui/src/ipc/useHaloConnection.ts`) is transport-only by design — no business logic lives in the UI. It re-reads `session.json` via a Tauri command (`read_session` in `lib.rs`) on every (re)connect, queues outbound `user_msg`s until `hello_ack`, and is written to survive React StrictMode's double-invoke of effects (teardown flag checked before every async continuation; handlers nulled before the intentional close so it doesn't trigger the reconnect loop).

**UI event store is split pure/impure on purpose.** `ui/src/state/reducer.ts` is a framework-free `applyFrame(state, frame) → state` projection of every IPC frame type — this is what `reducer.selfcheck.ts` replays canned frame logs against. `ui/src/state/store.ts` is the only impure piece: a thin zustand wrapper that calls the pure reducer inside `set()`, plus UI-only navigation state (`activeView`) that never came from a frame and so has no business being in the reducer. (The old `deriveOrbState` priority selector and `focusTarget` nav channel were both deleted as dead — see the comment at `ui/src/orb/OrbRoot.tsx:175`. The capsule shows every true signal at once and has no priority ladder.) Each of the orb and workspace windows boots its **own** store instance (separate webviews, no shared JS heap) — both independently open a WS connection and independently project state from the same broadcast frames.

**The companion window is a capsule, not a circle, and its shape is enforced at the native window layer.** Since the 2026-07-15 redesign, the floating companion is a horizontal 360×52 pill (`[lane · task] ((orb)) [approval · mic]`, Midnight Blue palette — dark glass surfaces, blue as a glow accent never a flat fill) rather than the original bare circle. CSS `border-radius` only shapes the DOM — on Windows the actual HWND stays rectangular unless clipped, so `ui/src-tauri` applies a Win32 `SetWindowRgn` (with an explicit zero-alpha `backgroundColor`) to make the native window's paint and hit-test bounds match the visual pill, reapplied on resize/DPI change. Non-Windows builds keep a no-op shaping function. See Decisions.md "Clip the Windows capsule at the HWND layer" and "Companion orb → capsule redesign."

**"Lock on press, unlock only on confirm" (rule 3) governs every outbound action with a visible affordance** (approve/deny/edit, pause/resume/stop, lane-pin, memory edit/delete/restore, skill trial/disable/restore). The control disables immediately on click and clears only when a frame confirming that exact change arrives — never optimistically. Implement the "did it confirm" check by comparing object identity/timestamp of the relevant store slice, not by mutating a ref inside a `setState` functional updater — React 18 StrictMode double-invokes functional updaters in dev, and a side effect (ref mutation) inside one silently breaks the unlock path on the second invocation while looking correct on the first (see `mem/Bugs.md`, "Rule-3 unlock on confirm"). If you add a new confirmable action, exercise the unlock step live, not just the lock step — the failure mode is invisible until a real confirming frame lands.

**Testing & verification — green tests are not sufficient.** Automated selfchecks and contract validators catch schema drift and logic errors in isolation. But several classes of bugs are *only* found by running the real stack end-to-end:
  - **UI/rendering bugs** (orb resize conflicts with drag, window state persistence quirks, component layering).
  - **Routing & visibility rules** (Voice should never receive the full UI snapshot; confirmed by running the real three-process stack).
  - **Async serialization** (concurrent messages to the same `conversation_id` must be serialized; found when mock dispatch bypassed the conversation lock).
  - **Mock handler completeness** (UI affordances that send outbound message types must be handled by the mock or the UI will hang waiting for confirmation).
  - **Process lifecycle** (shutdown-flag-before-kill ordering is tested only by an actual graceful-close test, not by checking "does it spawn").

**Pattern: after implementing a step or fixing a bug, launch the real app (`./dev.ps1` or `./dev.ps1 -Mock`) and exercise the surface.** Selfchecks pass → run the app. This is part of the standard verification flow, not optional.

**Contract drift between TypeScript and Python is caught by:** `python shared/check_contract_sync.py` (after editing `shared/ipc-contract.json`, `ui/src/ipc/contract.ts`, or `brain/brain/ipc/contract.py`).

## Repo conventions (public open-source repo)

- **Never commit or hardcode secrets** (API keys, tokens, credentials) anywhere, including doc examples — use placeholders (`<YOUR_API_KEY>`) or an env var/OS keystore name instead.
- Before staging or pushing, re-check `git status`/`git diff` for anything that looks like a real key, token, or personal path/credential.
- Commit messages and PR descriptions should stand on their own for outside contributors: explain *why*, avoid internal shorthand, don't assume prior conversation context.
- Prefer small, reviewable commits over large mixed ones.

## Working against the mocked Brain

The UI has two supported Brain modes: the default real Phase 2 Brain and the permanent scripted mock harness. Use the mock for deterministic UI scenarios and the real Brain for backend behavior. The mock lives in `brain/brain/mock.py` (state + `demo_*`/`_scenario_*` scenario methods, plus live `_beliefs`/`_skills` registries so edits round-trip realistically) and is dispatched from `brain/server.py` (`--mock` flag / `HALO_MOCK` env var), which also supports role-based routing (Voice only sees its protocol subset, UI sees everything).

**When adding a new outbound message type:**
1. Add the type to `shared/ipc-contract.json`, mirror it to both `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py`.
2. **Add a mock handler in `brain/brain/mock.py` and wire it into `server.py`'s dispatch table** before testing the UI. If the UI sends `task_op` and the mock doesn't handle it, the UI affordance (e.g., "Stop" button) will hang indefinitely waiting for a confirming `task_state` that never arrives — indistinguishable from a UI bug (this exact bug happened once, see `mem/Bugs.md`).
3. Verify with `python shared/check_contract_sync.py`.
4. Test with `./dev.ps1 -Mock` and exercise scripted surfaces end to end (plain `./dev.ps1` uses the real non-mock Phase 2 Brain and will not respond to `demo ...` triggers).

**Mock Brain scenarios** are scripted in `brain/brain/mock.py` (`demo_*`/`_scenario_*` methods). When adding a new inbound message type, verify the mock *and* the test suite (`brain/tests/test_mock.py`) handle it, and extend `shared/phase1_check.py` if the new scenario needs an automated frame-sequence assertion.

## Picking a skill/plugin

Before doing non-trivial work, check whether an available skill or agent already fits the task (e.g. UI/UX work → `ui-ux-pro-max`; feature planning → `feature-dev`) rather than solving it from scratch — this workspace has skills disabled granularly in `.claude/settings.local.json` (mostly unrelated stacks — Django, Laravel, Spring Boot, etc. — turned off), so check there before assuming one is unavailable.

## Project memory & lessons learned

`mem/` holds a running project-memory system:
- **[mem/Memory.md](mem/Memory.md)** — current phase, completed work, active goals, next steps.
- **[mem/Bugs.md](mem/Bugs.md)** — every bug hit during development, root cause, fix, and a "Never do" rule.
- **[mem/Gotchas.md](mem/Gotchas.md)** — non-obvious traps: port ephemeral, shutdown flag ordering, StrictMode double-invoke, Tauri window-state plugin gotchas.
- **[mem/Patterns.md](mem/Patterns.md)** — established patterns for common tasks (event store, mock design, etc.).
- **[mem/Decisions.md](mem/Decisions.md)** — why certain design choices were made (e.g., why orb is user-resizable, why all-edge resize was replaced with corner grip).
- **[mem/MigrationLog.md](mem/MigrationLog.md)** — database schema changes, newest first (currently at v3 — the `doc_digest` content-hash cache table, added additively on top of memory v2's consolidation/episodic/bi-temporal columns). Upgrades are single-hop: `_run_migrations` takes a v1 DB straight to `SCHEMA_VERSION` in one transaction, never landing on an intermediate version.

**Before implementing or debugging:** check `mem/Bugs.md` for similar symptoms and "Never do" rules. Before starting a new feature, check `mem/Decisions.md` to understand prior reasoning. **Update mem/ at the end of your session** if you:
- Hit a new bug (add to Bugs.md with root cause and "Never do" rule).
- Discover a new gotcha (add to Gotchas.md).
- Make a design decision with trade-offs (add to Decisions.md with reasoning).
- Establish a reusable pattern (add to Patterns.md).

Do NOT update mem/ for routine fixes or fixes to bugs already documented — Bugs.md is not a log, it's a decision tree. Mark it "superseded" or "fixed" in a note if needed, but keep it for future reference so the pattern doesn't repeat.
