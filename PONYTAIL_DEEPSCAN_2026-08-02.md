# Ponytail deep scan — 2026-08-02

Whole-repository over-engineering and dead-code scan of branch
`token-cost-reduction`, including the intentionally dirty working tree. This
report covers the current source, not just the historical audit findings.

## Outcome

- Removed about **165 net code/test lines attributable to this scan** before
  adding this report and the project-memory note.
- Collapsed belief creation onto one transactional production path.
- Removed three obsolete store APIs, one obsolete keystore wrapper, one dead
  gate helper, two unused CSS helpers, and three unused elevation tokens.
- Replaced repeated exception-swallow boilerplate with `contextlib.suppress`
  without broadening any caught exception.
- Repaired two full-gate portability/reliability defects found while building
  the baseline: TypeScript self-check execution under Node 25 and parallel
  Rust linker races under Windows antivirus.
- Updated the Phase 0 smoke fixture for the current split UI/Voice credentials.

The main conclusion is that the repository is already substantially lean after
the July and August Ponytail passes. There is no honest multi-thousand-line
source deletion left without removing current product, test, mock, security,
or Phase-3 prerequisite behavior.

## Coverage and method

The scan covered 188 tracked source/document files across:

- Brain runtime, graph, memory, persistence, permission gate, tools, and tests;
- React state, transport, views, styles, component tests, and self-checks;
- Rust/Tauri lifecycle, windows, logging, commands, and tests;
- Voice transport/reconnect worker and tests;
- shared contract, launcher, phase gates, docs, and project memory.

Evidence used:

- current `git status`, `git diff`, `git log`, prior audit commits, and the
  pre-existing dirty-tree baseline;
- import/caller tracing across process and language boundaries;
- direct dependency-to-import checks for Python, npm, and Cargo manifests;
- TypeScript `--noUnusedLocals --noUnusedParameters`;
- Rust `cargo check --all-targets -j 1`;
- Ruff `F`, `B`, `SIM`, and `PERF` review;
- Vulture production and whole-tree passes, with every hit manually checked;
- CSS selector, custom-property, and keyframe reference scans;
- focused regressions followed by the full repository gate.

## Implemented findings

### P1 — one belief creation and supersession path

`store.add_belief`, `store.supersede`, and `store.invalidate_belief` had no
runtime callers. They were older CRUD paths retained only by tests after
`add_candidate_belief` became the atomic production operation. Keeping them
duplicated insertion, provenance, validity-window, and vector-index logic.

They were removed. Tests now seed through `add_candidate_belief`; the matrix
asserts all four old/new provenance combinations, `superseded_by`,
`invalid_at`, live-row state, and rollback after an injected index failure.

### P1 — obsolete query/status wrappers

- `list_session_summaries` was test-only; runtime only needs the latest
  summary. Tests now assert through `latest_session_summary`.
- `keystore_available` was test-only and duplicated `_read_key`. The real UI
  path intentionally uses strict `key_status`, while `get_key` degrades to
  `None`; the broken-vault test still verifies both behaviors directly.
- `gate.is_mutating` had zero callers; the authoritative logic remains inside
  `classify_for_request` where request evidence is checked.

### P2 — dead styling surface

`.halo-elevation-panel`, `.halo-elevation-card`, `--z-companion`, `--z-panel`,
and `--z-modal` had no static or dynamic consumers. They were removed.
`--z-card` remains because menus, drawers, and overlays use it.

### P2 — standard-library simplification

Narrow `try/except/pass` blocks in Brain, TaskRuntime, Graph, and Voice now use
`contextlib.suppress`. Cancellation, timeout, connection-close, and malformed
JSON behavior is unchanged. Nested async context managers in task continuation
were combined without changing acquisition order.

### P1 — verification and fixture correctness

- `verify.ps1` now runs every TypeScript self-check through the installed
  Vite/Vitest runner. Raw Node 25 cannot resolve the application’s extensionless
  TypeScript imports.
- Rust tests run with `-j 1`, avoiding the reproduced Windows antivirus linker
  lock while preserving the same test set.
- `shared/smoke_test.py` now writes both session credentials and uses the UI
  token for UI reconnects and the Voice token for Voice authentication.

## Rejected cuts after verification

- **Per-principal request admission:** tested resource isolation, not YAGNI.
- **TaskRuntime, `TaskContext.log`, and task query helpers:** callback-driven or
  required by durable task verification and Phase-2 gates.
- **Mock `handle_*` functions:** dynamically referenced by `_MOCK_DISPATCH` and
  covered by dispatch-enumeration tests.
- **LangGraph `State` fields, SQLite `row_factory`, and aiosqlite `daemon`:**
  framework/attribute use that lexical dead-code tools cannot see.
- **`reconcileHistory`:** protects local unsynced turns during authoritative
  history hydration and has race-focused tests.
- **`TokensPreview`:** explicit lazy-loaded design-QA route (`?dev=tokens`).
- **Sidecar packaging seam:** current Phase-3 C3 prerequisite; removing it now
  would create near-term churn.
- **UI self-checks versus Vitest:** complementary protocol replay and
  persistence/race coverage, not byte-for-byte duplication.
- **Per-tool result caps, permission/error branches, accessibility behavior,
  and security checks:** intentionally retained even where abstractions could
  be made shorter.
- **`--midnight`, `--canvas`, and `--motion-slow`:** live design-system tokens;
  canvas and motion have concrete consumers, and Midnight remains the named
  palette anchor.
- **Historical `DEEPSCAN_AUDIT.md` and `AUDIT_PLAN.md`:** deliberately retained
  audit evidence per the existing project decision; they are not open plans.

## Verification

Focused store, memory, secrets, snapshot, task-runtime, Voice, TypeScript
unused-symbol, Rust all-target, and diff checks passed.

Fresh full command:

```powershell
.\verify.ps1 -PythonCommand '.\.venv\Scripts\python.exe'
```

Result: **exit 0 in 202.6 seconds**. Contract sync, all Brain/Voice suites,
five UI self-checks, Vitest, UI production build, serial Rust tests, and Phase
0/1/2 protocol gates passed. The first sandboxed attempt stopped at Vitest
because esbuild was denied access while resolving `vite.config.ts`; rerunning
the identical command with normal workspace access passed.
