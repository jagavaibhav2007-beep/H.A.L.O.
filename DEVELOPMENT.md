# Development

Halo is three processes: `ui` (Tauri + React, the parent), `brain` (Python), and `voice` (Python). See [systemdesign/11-ipc-contract.md](systemdesign/11-ipc-contract.md) for how they talk.

## Layout

```
ui/      Tauri + Vite + React + TypeScript desktop app
brain/   Python package (brain/__main__.py) — LangGraph agent loop + WS server
voice/   Python package (voice/__main__.py) — authenticated idle sidecar; real voice is Phase 3
shared/  IPC contract source of truth (JSON descriptor) + the TS/Python drift check
```

Phases 0, 1, and 2 are complete (declared 2026-08-01), including the Phase 2
exit-hardening tranche. A human/native checklist remains in `VERIFY.md` as a
recommended, non-blocking follow-up. Voice remains an authenticated idle
worker until Phase 3.

Brain uses an OS-level lock to prevent multiple instances from competing for `session.json`. After sending `hello`, UI and Voice wait for `hello_ack` before sending application messages.

## Prerequisites

- Node.js + npm (for `ui/`)
- **Rust + cargo** (for `ui/src-tauri` — the native Tauri shell). Without this, use `./dev.ps1 -Browser` for a functional browser workspace. Plain `npm run dev` remains UI-only.
- Python 3.11+ (for `brain/` and `voice/`)

Python dependency graphs are pinned with hashes in `brain/requirements.lock`
and `voice/requirements.lock`. Install those with `python -m pip install
--require-hashes -r requirements.lock` from each worker directory. Regenerate
with `uv pip compile pyproject.toml --universal --python-version 3.11
--generate-hashes`.

## Running each process

```powershell
# UI (needs Rust/cargo installed for the native window; falls back to browser preview otherwise)
cd ui; npm install; npm run tauri dev
# or, without Rust, the real Brain plus browser workspace:
./dev.ps1 -Browser

# Brain (starts the authenticated WebSocket server)
cd brain; python -m brain

# Voice (connects, authenticates, and idles)
cd voice; python -m voice
```

## Running all three together

```powershell
./dev.ps1             # launches Tauri; Tauri starts and supervises Brain + Voice
./dev.ps1 -Browser    # launches the real Brain + functional browser workspace
./dev.ps1 -Only brain # standalone worker debugging (brain | voice | ui)
```

Browser mode is development-only. Vite fresh-reads
`%LOCALAPPDATA%\Halo\session.json` through a loopback-only, no-store endpoint;
the UI still performs the normal authenticated WebSocket `hello` handshake.
The endpoint returns 404 unless `HALO_BROWSER_DEV=1`, which `dev.ps1 -Browser`
sets only while Vite runs. Production Tauri behavior is unchanged.

## Shared IPC contract

The contract is two hand-mirrored `CONTRACT_SPEC` dicts — `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py` — that must not drift (the old `shared/ipc-contract.json` was removed in commit `e41d77b`; the two mirrors are now the whole contract). Run the drift check, which diffs them directly:

```powershell
python shared/check_contract_sync.py
```

This fails if the TS and Python type sets (message names + required fields) don't match the schema. Run it whenever either side's contract file changes.

`voice/` imports the same contract module from `brain` (`brain.ipc.contract`), so install Brain into Voice's environment with `pip install -e ../brain`.

Self-checks (round-trip a `user_msg`, confirm unknown/malformed frames are rejected):

```powershell
python -m brain.ipc.contract          # from brain/
ui/node_modules/.bin/vite-node.cmd ui/src/ipc/contract.selfcheck.ts # from repo root
ui/node_modules/.bin/vite-node.cmd ui/src/ipc/queue.selfcheck.ts    # from repo root
```

## Phase 0 smoke test

`shared/smoke_test.py` is the one repeatable check that all four Phase 0 exit criteria (see [phases.md](phases.md#phase-0--skeleton--contract-the-plumbing)) hold, run from the repo root:

```powershell
python shared/smoke_test.py
# or
./dev.ps1 -Smoke
```

Prints a `PASS`/`FAIL` line per criterion plus a summary, and exits non-zero if any criterion fails (so CI can gate on it later). Requires `brain` importable, and for the Voice criterion, `voice` with `pip install -e ../brain` done in voice's environment (same prerequisite as `voice/tests/test_client.py`).

**Scope boundary:** this test drives the WS-protocol contract with real in-process Brain servers (ephemeral ports, no packaged binaries). It does **not** drive the Tauri GUI or the actual OS-process supervision (spawn/kill/respawn) — a native WebView2 window and Rust process supervision can't be headlessly driven here. Those are covered separately:
- the backoff ladder (1s/5s/30s, then 30s repeatedly) is unit-tested in `ui/src-tauri/src/supervisor.rs` (`cargo test`, run from `ui/src-tauri`).
- actual kill-Brain → OS-respawn → UI-window-reconnect was verified manually.

The smoke test's criterion 2 (kill Brain → respawn on a new port → reconnect) proves the *protocol* contract that makes that manual recovery correct — a client that re-reads `session.json` fresh, never caching a port, exactly like `ui/src/ipc/useHaloConnection.ts`'s `connect()`.

The smoke test writes its reconnect fixture to a temporary session file and does not overwrite the live `%LOCALAPPDATA%\Halo\session.json`.
