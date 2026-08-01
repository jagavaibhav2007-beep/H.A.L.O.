# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this repo is

**H.A.L.O.** - a local, resident desktop AI companion: Tauri+React UI, Python/LangGraph brain, Python/Pipecat voice worker, talking over an authenticated local-loopback WebSocket. **Phase 0 (skeleton & contract), Phase 1 (front-end shell), and the Phase 2 backend/exit-hardening implementation are present; formal Phase 2 closure remains gated by `VERIFY.md`.** The default Brain now provides real chat/model routing, checkpointing, permissions, activity/undo, Lane-1 file tools, memory, snapshots, history summarization, spend rollups, and durable background tasks. The Voice worker is still a Phase-0 idle stub; Phase 3 heavy systems (coding orchestration, browser automation, real voice, GUI control, self-improvement, and integrations) have not started.

The repo has two layers: design docs (source of truth for *behavior* and *architecture*) and the code that implements them.

- **[Halo-PRD.md](Halo-PRD.md)** - product spec: *what* Halo is and *how it behaves* (capabilities, control lanes, permissions, memory, self-improvement). Stack-agnostic by design - keep tech choices out of it.
- **[systemdesign/](systemdesign/00-overview.md)** - architecture per feature. **[11-ipc-contract.md](systemdesign/11-ipc-contract.md) is the canonical spec for the process model and message envelope** - read it before touching any cross-process code.
- **[techstack/](techstack/00-stack-summary.md)** - concrete technology choice per feature.
- **[ui_ux/](ui_ux/00-design-language.md)** - visual/interaction spec (tokens, motion, copy voice). Check `00-design-language.md` for existing tokens before inventing new ones.
- **[phases.md](phases.md)** - the roadmap. Phase 0 and Phase 1 are complete. The Phase 2 feature and exit-hardening implementation is present; native checks and a final current-tree integrated gate must pass before Phase 3 starts.
- **[phase-0-plan.md](phase-0-plan.md)** - the 8-step Phase 0 implementation plan and its exit criteria (all met - see Commands below to re-verify).
- **[phase-1-plan.md](phase-1-plan.md)** - the completed 15-step front-end shell plan.
- **[phase-2-plan.md](phase-2-plan.md)** - the completed 10-step backend spine plan.
- **[VERIFY.md](VERIFY.md)** - automated and native verification status. The Phase 2 native checklist still requires a human run with a real OpenRouter key.
- **[AUDIT_PLAN.md](AUDIT_PLAN.md)** - the 2026-07-22 evidence ledger: findings, fixes, explicit deferrals, test/native evidence, sources, and exit criteria.

Each `systemdesign/`/`techstack/`/`ui_ux/` folder numbers files by feature (`01-chat`, `02-voice`, `03-memory`, ...) - the same number across folders covers the same feature from architecture, technology, and UI angles. When changing one, check whether the matching file in the others needs to move with it. Treat the PRD as the source of truth for behavior; if a design decision changes a PRD claim, update the PRD too.

## Commands

Three independent process trees (`ui/` Rust+Node, `brain/` Python, `voice/` Python) - there is no single root build/test command.

**Run everything for local dev:**
```powershell
./dev.ps1               # launches Tauri with the real Brain and Voice
./dev.ps1 -Browser      # launches the real Brain in a functional browser workspace
./dev.ps1 -Only brain   # standalone worker debugging: brain | voice | ui
./dev.ps1 -Mock         # launches Tauri against the scripted mock Brain
./dev.ps1 -Smoke        # runs Phase 0/1/2 automated gates in-place (no windows)
./dev.ps1 -Verify       # full repository gate; required again after the 2026-07-31 exit-hardening changes
```

**UI (`ui/`, Tauri + Vite + React + TS):**
```powershell
# Run these from ui/ unless the comment says otherwise.
npm install
npm run tauri dev       # native window; needs Rust/cargo + MSVC Build Tools (C++ workload) on Windows
npm run dev             # UI-only browser preview; use ../dev.ps1 -Browser for a live Brain
npx tsc --noEmit        # typecheck
node ui/src/ipc/contract.selfcheck.ts   # contract self-check (from repo root)
node ui/src/ipc/queue.selfcheck.ts      # interrupted queue flush preserves unsent messages
npm test -- --run                       # Vitest hook/component tests (from ui/)
```

Rust side (`ui/src-tauri/`):
```powershell
cargo build
cargo test              # runs the backoff-ladder unit test in supervisor.rs
```

**Brain (`brain/`, Python 3.11+):**
```powershell
python -m brain                          # starts the WS server, writes session.json
python brain/tests/test_server.py        # auth/ordering tests (plain asyncio+assert, no pytest)
python shared/phase2_check.py             # real-Brain backend E2E gate with offline stubs (from repo root)
python -m brain.ipc.contract             # contract self-check
```

**Voice (`voice/`, Python 3.11+):**
```powershell
pip install -e ../brain      # from voice/'s env - voice imports brain.ipc.contract
python -m voice
python voice/tests/test_client.py
```

**Cross-language IPC contract drift check** (run after editing the schema or either mirrored type file):
```powershell
python shared/check_contract_sync.py
```

**Automated phase gates** (the smoke command runs Phase 0 transport, Phase 1 mock UI, and Phase 2 real-Brain checks in sequence):
```powershell
./dev.ps1 -Smoke
```

Backend and cross-process checks use plain `asyncio` + `assert` scripts; the UI has Vitest tests. Do not introduce another test framework without a real reason.

## Architecture

**Process model - Tauri is the parent.** On app start, `ui/src-tauri` spawns `brain` and `voice` as plain child processes (`std::process::Command`, run from source via `python -m brain`/`python -m voice` - no packaging yet, that is a later phase; see the `// ponytail:` comment in `supervisor.rs` for the packaged-binary path this will need). The Rust supervisor (`ui/src-tauri/src/supervisor.rs`) watches each child, restarts on a 1s/5s/30s backoff ladder with a healthy-uptime reset, and - critically - sets a shutdown flag *before* killing children on app exit, otherwise the supervision loop misreads the intentional kill as a crash and respawns it. It also explicitly kills children on exit since Windows does not reap them when the parent dies. `backoff_delay()` is a pure function with its own unit test, kept separate from the process-spawning loop.

**Session handshake.** The Brain holds a crash-safe OS lock so only one instance can own `session.json`, binds a random free loopback port, and atomically writes `{port, token}` to `%LOCALAPPDATA%\Halo\session.json`. **Every client must re-read this file fresh on every connect/reconnect attempt** - the Brain gets a new port on every restart, so caching the port anywhere causes silent reconnect failure. Every WS connection's first frame must be `{type:"hello", token}` matching that file; the Brain enforces this with `secrets.compare_digest`, silently drops failures, and sends `hello_ack` on success. Clients must not send or flush application messages before that acknowledgement.

**IPC contract - one schema, two hand-mirrored implementations.** `shared/ipc-contract.json` is the single source of truth for every message type and its required fields. `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py` are hand-mirrored from it (not codegen - deliberate, for simplicity at this scale). `shared/check_contract_sync.py` fails if the three diverge. The envelope is `{type, id, ts, ...payload}` - payload fields must never reuse the key `id` for a domain-specific identity (it collides with the envelope's own message id; this is why `approval_request`'s field is `approval_id`, not `id`).

**Tauri<->React state events are separate from the WS contract.** The supervisor emits a webview event (`"sidecar-state"`, via `tauri::Emitter::emit`) carrying OS-process health (`starting`/`running`/`restarting`/`error`) - this is not part of `shared/ipc-contract.json` and never should be. The UI keeps two genuinely distinct pieces of state: whether the WebSocket itself is connected+authenticated (drives the chat input and reconnect indicator) versus whether the Brain *process* is alive (drives a separate "Brain failed to start" banner from sidecar-state). Do not conflate them.

**Conversation serialization.** The Brain keeps an `asyncio.Lock` per `conversation_id` in `brain/server.py`, so concurrent messages to the same conversation are handled in arrival order.

**Real Brain backend.** The non-`--mock` Brain uses SQLite for durable state, keyring for secrets, an async OpenRouter client with a light/heavy rule-based router, LangGraph checkpointing keyed by `conversation_id`, and a single permission-gate/tool-registry choke point. Lane-1 local file operations and allowlisted read-only commands run under that gate; activity, undo, memory, snapshot hydration, summarization, and spend reporting are real. Offline automated checks use the env-gated `HALO_LLM_STUB` and `HALO_EXTRACT_STUB` seams; native real-model verification is not yet complete.

**UI WS client** (`ui/src/ipc/useHaloConnection.ts`) is transport-only by design - no business logic lives in the UI. It re-reads `session.json` via a Tauri command (`read_session` in `lib.rs`) on every (re)connect, queues outbound `user_msg`s until `hello_ack`, and is written to survive React StrictMode's double-invoke of effects (teardown flag checked before every async continuation; handlers nulled before the intentional close so it does not trigger the reconnect loop).

## Repo conventions (public open-source repo)

- **Never commit or hardcode secrets** (API keys, tokens, credentials) anywhere, including doc examples - use placeholders (`<YOUR_API_KEY>`) or an env var/OS keystore name instead.
- Before staging or pushing, re-check `git status`/`git diff` for anything that looks like a real key, token, or personal path/credential.
- Commit messages and PR descriptions should stand on their own for outside contributors: explain *why*, avoid internal shorthand, do not assume prior conversation context.
- Prefer small, reviewable commits over large mixed ones.

## Picking a skill/plugin

Before doing non-trivial work, check whether an available skill or agent already fits the task rather than solving it from scratch. This workspace has skills disabled granularly in `.Codex/settings.local.json`, so check there before assuming one is unavailable.

## Project memory

`mem/` holds a running project-memory system (bugs already hit, gotchas, patterns, decisions) - check it for context before debugging something that may have already been diagnosed, and update it (`/mem update memory`) at the end of a session with anything new worth persisting.

## Current progress

- Phase 0 — complete: authenticated three-process lifecycle, IPC contract, crash recovery, and reconnect behavior.
- Phase 1 — complete: mocked premium UI shell, orb/workspace windows, chat, activity, approvals, tasks, memory, skills/settings, voice presence, and automated/native verification.
- Phase 2 — Steps 1–10 plus the exit-hardening implementation are present. Durable tasks, authority separation, bounded admission, atomic batch undo, turn correlation, project-root repair, dependency locks, and CI are implemented. Formal closure still requires the unchecked native scenarios in `VERIFY.md` and one final integrated green gate.
- Phase 3 — not started and remains gated on formal Phase 2 closure.
- Recent hardening: the 2026-07-22 tool-result/confabulation fix makes data-returning file tools visible to the model, anchors relative paths at the user home, and documents accessible roots. Re-verify this behavior when changing the gate or file tools.
