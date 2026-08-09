# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this repo is

**H.A.L.O.** - a local, resident desktop AI companion: Tauri+React UI, Python/LangGraph Brain, and Python/Pipecat Voice worker over an authenticated local-loopback WebSocket. **Phases 0 (skeleton & contract), 1 (front-end shell), and 2 (backend spine + exit-hardening) are COMPLETE** (declared 2026-08-01). The default Brain provides real chat/model routing, checkpointing, permissions, activity/undo, Lane-1 file, document, and managed-command tools, memory consolidation, snapshots, history summarization, spend rollups, and durable background tasks (`TaskRuntime`, schema v5). Voice is still audio-idle (no wake word, capture, STT, or TTS), but its authenticated sidecar now reconnects in-process when Brain restarts. Phase 3a is underway: the shared command/script executor is implemented, while Codex/Claude adapters remain next. Browser automation, real voice, GUI control, self-improvement, and integrations have not started. Human/native visual, accessibility, recovery, and broader real-key checks remain recommended follow-ups in `VERIFY.md`.

The repo has two layers: design docs (source of truth for *behavior* and *architecture*) and the code that implements them.

- **[Halo-PRD.md](Halo-PRD.md)** - product spec: *what* Halo is and *how it behaves* (capabilities, control lanes, permissions, memory, self-improvement). Stack-agnostic by design - keep tech choices out of it.
- **[systemdesign/](systemdesign/00-overview.md)** - architecture per feature. **[11-ipc-contract.md](systemdesign/11-ipc-contract.md) is the canonical spec for the process model and message envelope** - read it before touching any cross-process code.
- **[systemdesign/12-task-runtime.md](systemdesign/12-task-runtime.md)** - implemented task-runtime architecture. Read it before changing long-running tools or starting Phase 3 work; it defines the separation between interactive turns and durable tasks.
- **[techstack/](techstack/00-stack-summary.md)** - concrete technology choice per feature.
- **[ui_ux/](ui_ux/00-design-language.md)** - visual/interaction spec (tokens, motion, copy voice). Check `00-design-language.md` for existing tokens before inventing new ones.
- **[phases.md](phases.md)** - the roadmap. Phases 0, 1, and 2 are complete; their step-by-step implementation plans (formerly `phase-0/1/2-plan.md`) were retired 2026-08-01 once implemented - see git history for the original checklists.
- **[VERIFY.md](VERIFY.md)** - automated and native verification status. A real-key OpenRouter walkthrough and human visual/NVDA pass remain as recommended, non-blocking follow-ups.

**Current working situation (2026-08-07).** Phase 0-2 work and the Phase 3 UI foundation are merged to `main`; the Phase-3a managed-command foundation is implemented on its feature branch. Always inspect `git status` and the relevant `git diff` before editing and preserve unrelated work. Codex/Claude adapters are the next 3a tranche; PyInstaller packaging remains scheduled before 3c, and per-client `stream_frame` subscription remains scheduled with 3d.

Each `systemdesign/`/`techstack/`/`ui_ux/` folder numbers files by feature (`01-chat`, `02-voice`, `03-memory`, ...) - the same number across folders covers the same feature from architecture, technology, and UI angles. When changing one, check whether the matching file in the others needs to move with it. Treat the PRD as the source of truth for behavior; if a design decision changes a PRD claim, update the PRD too.

## Commands

Three independent process trees (`ui/` Rust+Node, `brain/` Python, `voice/` Python) - there is no single root build/test command.

**Run everything for local dev:**
```powershell
./dev.ps1               # launches Tauri with the real Brain and Voice
./dev.ps1 -Only ui      # standalone UI/native debugging: ui | brain | voice
./dev.ps1 -Browser      # launches the real Brain in a functional browser workspace
./dev.ps1 -Only brain   # standalone worker debugging: brain | voice | ui
./dev.ps1 -Mock         # launches Tauri against the scripted mock Brain
./dev.ps1 -Smoke        # runs Phase 0/1/2 automated gates in-place (no windows)
./dev.ps1 -Verify       # full repository gate: contract sync, Python suites, UI checks/build, Rust tests, phase checks
./dev.ps1 -WatchNative  # opt into Vite/Rust hot reload; stable attached mode is the default
```

`-Smoke`, `-Verify`, and `-Browser` are mutually exclusive with one another and with `-Only`, `-Mock`, and `-WatchNative`. Browser mode serves the real Brain plus a loopback-only Vite workspace at `http://127.0.0.1:1420/`; it sets `HALO_BROWSER_DEV=1`, has no Tauri supervision, and has no Voice process. A bare `npm run dev` is UI-only and does not expose the browser session endpoint. The default launcher is attached and stable (`vite preview` plus `tauri dev --no-watch`); use `-WatchNative` only when interactive hot reload is needed.

**UI (`ui/`, Tauri + Vite + React + TS):**
```powershell
# Run these from ui/ unless the comment says otherwise.
npm install
npm run tauri dev       # native window; needs Rust/cargo + MSVC Build Tools (C++ workload) on Windows
npm run dev             # UI-only browser preview; use ../dev.ps1 -Browser for a live Brain
npx tsc --noEmit        # typecheck
ui/node_modules/.bin/vite-node.cmd ui/src/ipc/contract.selfcheck.ts   # contract self-check (from repo root)
ui/node_modules/.bin/vite-node.cmd ui/src/ipc/queue.selfcheck.ts      # interrupted queue flush preserves unsent messages
ui/node_modules/.bin/vite-node.cmd ui/src/state/reducer.selfcheck.ts   # frame-log event-store projection self-check
ui/node_modules/.bin/vite-node.cmd ui/src/state/conversations.selfcheck.ts # conversation registry/unread/eviction self-check
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

The focused protocol checks are also runnable directly: `python shared/smoke_test.py`, `python shared/phase1_check.py`, and `python shared/phase2_check.py`. `./dev.ps1 -Smoke` runs them in sequence; `./dev.ps1 -Verify` adds the full Python/Voice suite, UI checks/build, Rust tests, and phase checks.

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

**IPC contract - two hand-mirrored implementations, diffed against each other.** There is no separate JSON schema: commit `e41d77b` deleted the old `shared/ipc-contract.json`, so the contract now lives as two hand-mirrored `CONTRACT_SPEC` dicts - `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py` (not codegen - deliberate, for simplicity at this scale). `shared/check_contract_sync.py` diffs those two runtime dicts directly and fails if they diverge. The envelope is `{type, id, ts, ...payload}` - payload fields must never reuse the key `id` for a domain-specific identity (it collides with the envelope's own message id; this is why `approval_request`'s field is `approval_id`, not `id`).

**Tauri<->React state events are separate from the WS contract.** The supervisor emits a webview event (`"sidecar-state"`, via `tauri::Emitter::emit`) carrying OS-process health (`starting`/`running`/`restarting`/`error`) - this is not part of the IPC contract (`contract.ts`/`contract.py`) and never should be. The UI keeps two genuinely distinct pieces of state: whether the WebSocket itself is connected+authenticated (drives the chat input and reconnect indicator) versus whether the Brain *process* is alive (drives a separate "Brain failed to start" banner from sidecar-state). Do not conflate them.

**Conversation serialization.** The Brain keeps an `asyncio.Lock` per `conversation_id` in `brain/server.py`, so concurrent messages to the same conversation are handled in arrival order.

**Real Brain backend.** The non-`--mock` Brain uses SQLite for durable state, keyring for secrets, an async OpenRouter client with a light/heavy rule-based router, LangGraph checkpointing keyed by `conversation_id`, and a single permission-gate/tool-registry choke point. Lane-1 local file operations and allowlisted read-only commands run under that gate; activity, undo, memory, snapshot hydration, summarization, and spend reporting are real. Offline automated checks use the env-gated `HALO_LLM_STUB` and `HALO_EXTRACT_STUB` seams; native real-model verification is not yet complete.

**Task runtime.** Task-shaped tools detach from the short interactive-turn pool and per-conversation lock into the bounded, durable `TaskRuntime` (`HALO_TASK_CONCURRENCY`, default 2). It persists intent/result metadata in schema v5, gives tools cooperative stop/pause/progress/log callbacks, emits bounded `task_log` tails to UI clients, and reconciles torn tasks after restart without blindly replaying side effects. `dir_organize` and `doc_digest` are the first task-shaped tools. `task_op` is real; unsupported pause is reported as a correlated error. Read `systemdesign/12-task-runtime.md` before adding long-running work.

**Readiness hardening now in the tree.** Pending checkpoint rehydration is scoped to threads with a live interrupt; whole-thread archival is deliberately deferred because there is no reliable closed-thread signal and deleting dormant checkpoints would lose resumable history. Voice re-reads `session.json` on every reconnect and follows Brain's new ephemeral port. The UI caps live turns per conversation at `MAX_TURNS=200` while leaving explicit history loads uncapped. The activity-array ring rewrite was intentionally deferred because high-rate task output already uses bounded `task_log` state and ordinary activity is human-paced.

**UI WS client** (`ui/src/ipc/useHaloConnection.ts`) is transport-only by design - no business logic lives in the UI. It re-reads `session.json` via a Tauri command (`read_session` in `lib.rs`) on every (re)connect, queues outbound `user_msg`s until `hello_ack`, and is written to survive React StrictMode's double-invoke of effects (teardown flag checked before every async continuation; handlers nulled before the intentional close so it does not trigger the reconnect loop).

**UI event store.** `ui/src/state/reducer.ts` is a framework-free `applyFrame` projection of IPC frames; `store.ts` is the zustand wrapper plus UI-only navigation state. The orb and workspace windows each have their own store and WebSocket connection. `taskLogs` is a bounded volatile tail (500 chunks per task); the Brain remains authoritative for durable history and task/activity records.

## Repo conventions (public open-source repo)

- **Never commit or hardcode secrets** (API keys, tokens, credentials) anywhere, including doc examples - use placeholders (`<YOUR_API_KEY>`) or an env var/OS keystore name instead.
- Before staging or pushing, re-check `git status`/`git diff` for anything that looks like a real key, token, or personal path/credential.
- Commit messages and PR descriptions should stand on their own for outside contributors: explain *why*, avoid internal shorthand, do not assume prior conversation context.
- Prefer small, reviewable commits over large mixed ones.

## Picking a skill/plugin

Before doing non-trivial work, check the skills available in the current session and use the smallest matching skill. Repository policy requires `codexautopilot` for substantial requests (workspace changes, investigations, research, deliverables, meaningful tool use, or verification). Do not assume an optional plugin is installed; use the available fallback or request installation only when the user explicitly named a missing plugin and tool search is exhausted.

## Project memory

`mem/` holds a running project-memory system (bugs already hit, gotchas, patterns, decisions) - check it for context before debugging something that may have already been diagnosed, and update it (`/mem update memory`) at the end of a session with anything new worth persisting.

## Current progress

- Phase 0 — complete: authenticated three-process lifecycle, IPC contract, crash recovery, and reconnect behavior.
- Phase 1 — complete: mocked premium UI shell, orb/workspace windows, chat, activity, approvals, tasks, memory, skills/settings, voice presence, and automated/native verification.
- Phase 2 — COMPLETE (declared 2026-08-01). Steps 1–10 plus exit hardening are implemented: durable tasks (`TaskRuntime`, schema v5), authority separation, bounded admission, atomic batch undo, turn correlation, project-root repair, dependency locks, scoped checkpoint rehydration, Voice reconnect, and bounded UI turns. The unchecked native scenarios in `VERIFY.md` remain recommended, non-blocking follow-ups.
- Phase 3 — underway. The Phase-3a managed-command foundation is implemented; Codex/Claude adapters are next. Read `systemdesign/05-computer-control.md`, `systemdesign/07-coding-orchestration.md`, and `systemdesign/12-task-runtime.md` before extending it. PyInstaller packaging before 3c and per-client `stream_frame` subscription with 3d remain just-in-time prerequisites in `phases.md`.
- Recent hardening: the 2026-07-22 tool-result/confabulation fix makes data-returning file tools visible to the model, anchors relative paths at the user home, and documents accessible roots. Re-verify this behavior when changing the gate or file tools.
