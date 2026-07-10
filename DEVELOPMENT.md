# Development

Halo is three processes: `ui` (Tauri + React, the parent), `brain` (Python), and `voice` (Python). See [systemdesign/11-ipc-contract.md](systemdesign/11-ipc-contract.md) for how they talk.

## Layout

```
ui/      Tauri + Vite + React + TypeScript app (scaffolded via `npm create tauri-app@latest`)
brain/   Python package (brain/__main__.py) — LangGraph agent loop + WS server
voice/   Python package (voice/__main__.py) — Pipecat audio sidecar
shared/  IPC contract source of truth (JSON descriptor) + the TS/Python drift check
```

Phase 0 only has empty-shell processes and the shared contract types — no real WS server, auth, or UI wiring yet (that's Steps 3+ in [phase-0-plan.md](phase-0-plan.md)).

## Prerequisites

- Node.js + npm (for `ui/`)
- **Rust + cargo** (for `ui/src-tauri` — the native Tauri shell). Without this, `npm run tauri dev` cannot open a window; `npm run dev` (plain Vite, browser-only) still works for UI-only iteration.
- Python 3.11+ (for `brain/` and `voice/`)

## Running each process

```powershell
# UI (needs Rust/cargo installed for the native window; falls back to browser preview otherwise)
cd ui; npm install; npm run tauri dev
# or, without Rust, just the web layer in a browser tab:
cd ui; npm run dev

# Brain (Phase 0: starts, prints a line, exits)
cd brain; python -m brain

# Voice (Phase 0: starts, prints a line, exits)
cd voice; python -m voice
```

## Running all three together

```powershell
./dev.ps1          # launches brain, voice, ui each in their own window
./dev.ps1 -Only ui # launch just one
```

## Shared IPC contract

`shared/ipc-contract.json` is the single source of truth for every message `type` in the envelope. `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py` are hand-mirrored from it and must not drift — run the drift check:

```powershell
python shared/check_contract_sync.py
```

This fails if the TS and Python type sets (message names + required fields) don't match the schema. Run it whenever either side's contract file changes.

`voice/` will import the same contract module from `brain` (`brain.ipc.contract`) once it needs it — in dev that means `pip install -e ../brain` from `voice/`'s environment. Not wired up in Phase 0 since voice has no runtime logic yet.

Self-checks (round-trip a `user_msg`, confirm unknown/malformed frames are rejected):

```powershell
python -m brain.ipc.contract          # from brain/
node ui/src/ipc/contract.selfcheck.ts # from repo root
```
