# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**H.A.L.O.** — a local, resident desktop AI companion: Tauri+React UI, Python/LangGraph brain, Python/Pipecat voice worker, talking over an authenticated local-loopback WebSocket. **Phase 0 (skeleton & contract) is implemented and working** — three real processes spawn, authenticate, and recover from crashes. Later phases (real model calls, memory, permission gate, voice audio) are not yet built.

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
./dev.ps1              # launches brain, voice, ui each in its own window
./dev.ps1 -Only ui      # just one of: ui | brain | voice
./dev.ps1 -Smoke        # runs the Phase 0 E2E smoke test in-place (no windows)
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

**Conversation serialization.** The Brain keeps an `asyncio.Lock` per `conversation_id` in `brain/server.py`, so concurrent messages to the same conversation are handled in arrival order.

**UI WS client** (`ui/src/ipc/useHaloConnection.ts`) is transport-only by design — no business logic lives in the UI. It re-reads `session.json` via a Tauri command (`read_session` in `lib.rs`) on every (re)connect, queues outbound `user_msg`s until `hello_ack`, and is written to survive React StrictMode's double-invoke of effects (teardown flag checked before every async continuation; handlers nulled before the intentional close so it doesn't trigger the reconnect loop).

## Repo conventions (public open-source repo)

- **Never commit or hardcode secrets** (API keys, tokens, credentials) anywhere, including doc examples — use placeholders (`<YOUR_API_KEY>`) or an env var/OS keystore name instead.
- Before staging or pushing, re-check `git status`/`git diff` for anything that looks like a real key, token, or personal path/credential.
- Commit messages and PR descriptions should stand on their own for outside contributors: explain *why*, avoid internal shorthand, don't assume prior conversation context.
- Prefer small, reviewable commits over large mixed ones.

## Picking a skill/plugin

Before doing non-trivial work, check whether an available skill or agent already fits the task (e.g. planning → `ecc:plan`; docs sync → `ecc:update-docs`; UI/UX work → `ui-ux-pro-max`) rather than solving it from scratch — this workspace has skills disabled granularly in `.claude/settings.local.json`, so check there before assuming one is unavailable.

## Project memory

`mem/` holds a running project-memory system (bugs already hit, gotchas, patterns, decisions) — check it for context before debugging something that may have already been diagnosed, and update it (`/mem update memory`) at the end of a session with anything new worth persisting.
