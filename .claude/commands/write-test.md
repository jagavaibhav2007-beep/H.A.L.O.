---
description: Write a test for H.A.L.O. code in this repo's own idiom (no framework)
---

Write a test for: $ARGUMENTS

This repo uses **no test framework** — CLAUDE.md: *"don't introduce pytest/jest
without a real reason."* Match the existing convention exactly. Pick the layer
that fits what you're testing:

## 1. Pure logic (Python) — plain `asyncio` + `assert`
- Live beside the code under `brain/tests/` (`test_*.py`) or as a `_self_check()`
  in the module run via `python -m <module>` (see `brain/brain/ipc/contract.py`).
- No pytest, no fixtures. A `def`/`async def` that asserts and prints `OK`, run
  directly. Follow `brain/tests/test_server.py` / `test_mock.py` for style
  (import shared helpers from `test_server` rather than re-rolling them).
- Run it: `python brain/tests/test_<x>.py`.

## 2. Pure logic (TypeScript) — a `*.selfcheck.ts`
- Live beside the code (`ui/src/state/reducer.selfcheck.ts`,
  `ui/src/ipc/queue.selfcheck.ts`). Replay canned input through the pure
  function, assert the projection, print `OK`.
- Run it: `npx tsx ui/src/<path>.selfcheck.ts`.

## 3. Rust logic — a `#[cfg(test)] mod tests` unit test
- In-file next to the pure function (see `clamp_axis`/`backoff_delay` in
  `ui/src-tauri/src/`). Run: `cargo test` from `ui/src-tauri`.

## 4. UI behaviour (rendering / interaction) — Playwright against `npm run dev`
- Use the Playwright plugin to drive the **browser fallback** (`npm run dev`,
  port 1420). Good for orb/capsule rendering, view routing, control states.
- **Caveat that bounds this:** `useHaloConnection` reads port/token via the
  Tauri `read_session` command, which only exists in the **native** window — so
  the browser fallback cannot exercise the WS/auth/reconnect path. For anything
  on the connection lifecycle, verify in the native app (`./dev.ps1 -Mock`) and
  read the **Brain log** for `client authenticated (role=ui)` — the native
  WebView2 window can't be headlessly driven.

## Priorities — test what actually breaks here
Per `mem/Bugs.md`, this repo's real bugs are *"green tests pass, real stack
broken"*: routing/visibility (Voice's frame subset), async serialization (the
per-`conversation_id` lock), rule-3 unlock-on-confirm under StrictMode, process
lifecycle. **Prefer one test that exercises the real path over many that mock it.**
Non-trivial logic (a branch, loop, parser, money/security path) leaves exactly
ONE runnable check behind — no framework, no per-function suites unless asked.

## After writing
Run the new test AND the relevant existing gate so you have a clean signal:
`python shared/check_contract_sync.py`, `./dev.ps1 -Smoke`, `npx tsc --noEmit`,
or `cargo test` as applicable. A red gate is stop-the-line.
