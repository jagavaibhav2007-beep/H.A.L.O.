# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**H.A.L.O.** — a local, resident desktop AI companion: Tauri+React UI, Python/LangGraph brain, Python/Pipecat voice worker, talking over an authenticated local-loopback WebSocket. **Phase 0 (skeleton & contract) is complete and hardened** — three real processes spawn, authenticate, and recover from crashes with proper shutdown ordering. **Phase 1 (front-end shell) is in progress** — the full premium UI with mocked Brain, design tokens, global hotkey, and orb+workspace windows. See [phases.md](phases.md) for the roadmap; [phase-1-plan.md](phase-1-plan.md) for current work.

The repo has two layers: design docs (source of truth for *behavior* and *architecture*) and the code that implements them.

- **[Halo-PRD.md](Halo-PRD.md)** — product spec: *what* Halo is and *how it behaves* (capabilities, control lanes, permissions, memory, self-improvement). Stack-agnostic by design — keep tech choices out of it.
- **[systemdesign/](systemdesign/00-overview.md)** — architecture per feature. **[11-ipc-contract.md](systemdesign/11-ipc-contract.md) is the canonical spec for the process model and message envelope** — read it before touching any cross-process code.
- **[techstack/](techstack/00-stack-summary.md)** — concrete technology choice per feature.
- **[ui_ux/](ui_ux/00-design-language.md)** — visual/interaction spec (tokens, motion, copy voice). Check `00-design-language.md` for existing tokens before inventing new ones.
- **[phases.md](phases.md)** — the phase-by-phase build roadmap (Phase 0 skeleton → Phase 1 front-end shell → Phase 2 backend spine → Phase 3 heavy systems).
- **[phase-0-plan.md](phase-0-plan.md)** — the 8-step Phase 0 implementation plan and its exit criteria (all met — see Commands below to re-verify).

Each `systemdesign/`/`techstack/`/`ui_ux/` folder numbers files by feature (`01-chat`, `02-voice`, `03-memory`, …) — the same number across folders covers the same feature from architecture, technology, and UI angles. When changing one, check whether the matching file in the others needs to move with it. Treat the PRD as the source of truth for behavior; if a design decision changes a PRD claim, update the PRD too.

## Commands

Three independent process trees (`ui/` Rust+Node, `brain/` Python, `voice/` Python) — there is no single root build/test command.

**Run everything for local dev:**
```powershell
./dev.ps1              # launches Tauri (UI parent spawns brain, voice sidecars)
./dev.ps1 -Only ui      # just one of: ui | brain | voice (for standalone debugging)
./dev.ps1 -Smoke        # runs the Phase 0 E2E smoke test in-place (no windows)
```

**Phase 1 (mocked Brain) for UI development:**
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
```
Rust side (`ui/src-tauri/`):
```powershell
cargo build
cargo test               # runs the backoff-ladder unit test in supervisor.rs
```

**Brain (`brain/`, Python 3.11+):**
```powershell
python -m brain                          # starts the WS server, writes session.json
python brain/tests/test_server.py        # auth/echo/ordering tests (plain asyncio+assert, no pytest)
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
No test framework is used anywhere in this repo (plain `asyncio` + `assert` scripts) — don't introduce pytest/jest without a real reason.

## Architecture

**Process model — Tauri is the parent.** On app start, `ui/src-tauri` spawns `brain` and `voice` as plain child processes (`std::process::Command`, run from source via `python -m brain`/`python -m voice` — no packaging yet, that's a later phase; see the `// ponytail:` comment in `supervisor.rs` for the packaged-binary path this will need). The Rust supervisor (`ui/src-tauri/src/supervisor.rs`) watches each child, restarts on a 1s/5s/30s backoff ladder with a healthy-uptime reset, and — critically — sets a shutdown flag *before* killing children on app exit, otherwise the supervision loop misreads the intentional kill as a crash and respawns it. It also explicitly kills children on exit since Windows doesn't reap them when the parent dies. `backoff_delay()` is a pure function with its own unit test, kept separate from the process-spawning loop.

**Session handshake.** The Brain holds a crash-safe OS lock so only one instance can own `session.json`, binds a random free loopback port, and atomically writes `{port, token}` to `%LOCALAPPDATA%\Halo\session.json`. **Every client must re-read this file fresh on every connect/reconnect attempt** — the Brain gets a new port on every restart, so caching the port anywhere causes silent reconnect failure. Every WS connection's first frame must be `{type:"hello", token}` matching that file; the Brain enforces this with `secrets.compare_digest`, silently drops failures, and sends `hello_ack` on success. Clients must not send or flush application messages before that acknowledgement.

**IPC contract — one schema, two hand-mirrored implementations.** `shared/ipc-contract.json` is the single source of truth for every message type and its required fields. `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py` are hand-mirrored from it (not codegen — deliberate, for simplicity at this scale). `shared/check_contract_sync.py` fails if the three diverge. The envelope is `{type, id, ts, ...payload}` — payload fields must never reuse the key `id` for a domain-specific identity (it collides with the envelope's own message id; this is why `approval_request`'s field is `approval_id`, not `id`).

**Tauri↔React state events are separate from the WS contract.** The supervisor emits a webview event (`"sidecar-state"`, via `tauri::Emitter::emit`) carrying OS-process health (`starting`/`running`/`restarting`/`error`) — this is not part of `shared/ipc-contract.json` and never should be. The UI keeps two genuinely distinct pieces of state: whether the WebSocket itself is connected+authenticated (drives the chat input and reconnect indicator) versus whether the Brain *process* is alive (drives a separate "Brain failed to start" banner from sidecar-state). Don't conflate them.

**Conversation serialization.** The Brain keeps an `asyncio.Lock` per `conversation_id` in `brain/server.py`, so concurrent messages to the same conversation are handled in arrival order. This lock must be held by the shared dispatch boundary (not buried inside individual turn handlers), so every Brain mode (real, mock, test) routes through the same serialization point and the pattern can't be accidentally bypassed.

**IPC envelope `id` field is reserved.** The `id` in `{type, id, ts, ...payload}` is the message envelope's own message ID. Payload fields must never use `id` as a key (e.g., `approval_request` uses `approval_id`, not `id`) — otherwise the payload value would collide and overwrite the envelope's message ID when flattened.

**UI WS client** (`ui/src/ipc/useHaloConnection.ts`) is transport-only by design — no business logic lives in the UI. It re-reads `session.json` via a Tauri command (`read_session` in `lib.rs`) on every (re)connect, queues outbound `user_msg`s until `hello_ack`, and is written to survive React StrictMode's double-invoke of effects (teardown flag checked before every async continuation; handlers nulled before the intentional close so it doesn't trigger the reconnect loop).

**Testing & verification — green tests are not sufficient.** Automated selfchecks and contract validators catch schema drift and logic errors in isolation. But several classes of bugs are *only* found by running the real stack end-to-end:
  - **UI/rendering bugs** (orb resize conflicts with drag, window state persistence quirks, component layering).
  - **Routing & visibility rules** (Voice should never receive the full UI snapshot; confirmed by running the real three-process stack).
  - **Async serialization** (concurrent messages to the same `conversation_id` must be serialized; found when mock dispatch bypassed the conversation lock).
  - **Mock handler completeness** (UI affordances that send outbound message types must be handled by the mock or the UI will hang waiting for confirmation).
  - **Process lifecycle** (shutdown-flag-before-kill ordering is tested only by an actual graceful-close test, not by checking "does it spawn").

**Pattern: After implementing a Phase 1+ step or fixing a bug, launch the real app (`./dev.ps1`) and exercise the surface.** Selfchecks pass → run the app. This is part of the standard verification flow, not optional.

**Contract drift between TypeScript and Python is caught by:** `python shared/check_contract_sync.py` (after editing `shared/ipc-contract.json`, `ui/src/ipc/contract.ts`, or `brain/brain/ipc/contract.py`).

## Repo conventions (public open-source repo)

- **Never commit or hardcode secrets** (API keys, tokens, credentials) anywhere, including doc examples — use placeholders (`<YOUR_API_KEY>`) or an env var/OS keystore name instead.
- Before staging or pushing, re-check `git status`/`git diff` for anything that looks like a real key, token, or personal path/credential.
- Commit messages and PR descriptions should stand on their own for outside contributors: explain *why*, avoid internal shorthand, don't assume prior conversation context.
- Prefer small, reviewable commits over large mixed ones.

## Phase 1 specifics — UI against a mocked Brain

Phase 1 build ([phase-1-plan.md](phase-1-plan.md)) renders every surface against a **scripted mock Brain** that replays predetermined IPC events. The mock is in `brain/server.py` (`--mock` flag) and supports role-based routing (Voice only sees its protocol subset, UI sees everything).

**When implementing a Phase 1 step that sends a new outbound message type:**
1. Add the type to `shared/ipc-contract.json`, mirror it to both `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py`.
2. **Add a mock handler in `brain/server.py`** before testing the UI. If the UI sends `task_op` and the mock doesn't handle it, the UI affordance (e.g., "Stop" button) will hang indefinitely waiting for a confirming `task_state` that never arrives — indistinguishable from a UI bug.
3. Verify with `python shared/check_contract_sync.py`.
4. Test with `./dev.ps1` and exercise the surface end to end.

**Mock Brain scenarios** are scripted in `brain/server.py` (`demo_*` methods). When adding a new inbound message type, verify the mock *and* the test suite (`brain/tests/test_mock.py`) handle it.

## Picking a skill/plugin

Before doing non-trivial work, check whether an available skill or agent already fits the task (e.g. planning → `ecc:plan`; docs sync → `ecc:update-docs`; UI/UX work → `ui-ux-pro-max`) rather than solving it from scratch — this workspace has skills disabled granularly in `.claude/settings.local.json`, so check there before assuming one is unavailable.

## Project memory & lessons learned

`mem/` holds a running project-memory system:
- **[mem/Memory.md](mem/Memory.md)** — current phase, completed work, active goals, next steps.
- **[mem/Bugs.md](mem/Bugs.md)** — every bug hit during development, root cause, fix, and a "Never do" rule.
- **[mem/Gotchas.md](mem/Gotchas.md)** — non-obvious traps: port ephemeral, shutdown flag ordering, StrictMode double-invoke, Tauri window-state plugin gotchas.
- **[mem/Patterns.md](mem/Patterns.md)** — established patterns for common tasks (event store, mock design, etc.).
- **[mem/Decisions.md](mem/Decisions.md)** — why certain design choices were made (e.g., why orb is user-resizable, why all-edge resize was replaced with corner grip).

**Before implementing or debugging:** check `mem/Bugs.md` for similar symptoms and "Never do" rules. Before starting a new feature, check `mem/Decisions.md` to understand prior reasoning. **Update mem/ at the end of your session** if you:
- Hit a new bug (add to Bugs.md with root cause and "Never do" rule).
- Discover a new gotcha (add to Gotchas.md).
- Make a design decision with trade-offs (add to Decisions.md with reasoning).
- Establish a reusable pattern (add to Patterns.md).

Do NOT update mem/ for routine fixes or fixes to bugs already documented — Bugs.md is not a log, it's a decision tree. Mark it "superseded" or "fixed" in a note if needed, but keep it for future reference so the pattern doesn't repeat.
