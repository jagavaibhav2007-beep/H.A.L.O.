# Ponytail Audit — 2026-08-01

Whole-repo over-engineering scan. Three parallel subagents, one per process tree
(brain / ui+rust / wiring+shared+voice), synthesized through the ponytail lens.
`.md` docs were ignored — code only. ~25K LOC of source scanned (brain 13.9K, ui
9.7K, shared 1.3K, voice 0.25K).

## Headline

**The codebase is already lean.** All three scans independently reported the same
thing: disciplined authors, 12+ `ponytail:` shortcut markers, dead code already
removed (`deriveOrbState`, `focusTarget`). Most "long" functions are load-bearing
— trust-boundary error handling, provenance/security rules, data-loss-safe writes,
token-cost accounting. Those stay.

The one real over-build is **the IPC contract, maintained as 5 hand-synced copies**
of the same 33-message shape. Collapsing that is ~490 LOC and *improves* robustness
(fewer copies that can silently drift). Everything else is small, clean dedup.

Honest total: **~285 LOC removable at near-zero risk; ~610 LOC with two med-risk
structural cuts; up to ~790 LOC if some dead dev/packaging scaffold is also retired.**
That's ~2.5–3% of source — this is polish, not a rescue. One large "clever" cut
(~250 LOC, deriving TS interfaces via mapped types) was found and **explicitly
rejected** as fighting a documented decision.

---

## The contract question (the crux — where two agents disagreed)

The same message contract is encoded **five times**:

| # | Representation | File | Runtime use? | Imported by anything? |
|---|---|---|---|---|
| 1 | JSON schema | `shared/ipc-contract.json` | No | Only the drift checker |
| 2 | Python `CONTRACT_SPEC` dict | `brain/brain/ipc/contract.py:325-435` | **Yes** (Brain validation) | Yes |
| 3 | Python `TypedDict`s | `brain/brain/ipc/contract.py:18-305` | No (typing only) | **No — grep-confirmed** |
| 4 | TS `CONTRACT_SPEC` object | `ui/src/ipc/contract.ts:346-435` | **Yes** (UI validation) | Yes |
| 5 | TS `interface`s | `ui/src/ipc/contract.ts:15-286` | No (typing only) | **Yes** — reducer/views use them |

`shared/check_contract_sync.py` reduces #2 and #4 to the same `{envelope, messages}`
dict shape and diffs each against #1 — i.e. **the JSON refereeing a comparison
between the two things it has to normalize to the same shape anyway.**

**The disagreement:** the wiring scan said *keep* the Python type/runtime split
(the `check_python_typeddicts` guard caught a real drift once). The brain scan
verified **nothing imports the Python TypedDicts** — so they provide zero static
value; the guard only protects a shadow copy nobody runs.

**Resolution (internally consistent):**
- **#1 JSON — delete.** Diff #2 against #4 directly. The referee sits between the
  only two copies that actually run; today a symmetric typo in *both* JSON and
  Python passes the schema↔py diff, while the two runtimes could silently agree-
  wrong. Diffing runtime-to-runtime is *stronger*. (~200 LOC, low risk.)
- **#3 Python TypedDicts — delete, and delete their guard with them.** They're an
  unused copy; the guard that keeps them honest only matters because the copy
  exists. Remove both → the drift they could suffer becomes impossible. Mitigation:
  keep `CONTRACT_SPEC` field-commented so Python side keeps human-readable docs.
  (~290 LOC, med risk — the only cost is losing per-message docstrings.)
- **#5 TS interfaces — keep, hand-written.** Unlike #3 these are *actually used*
  across the UI. Do **not** derive them from the spec via mapped types (rejected
  below).

Net: **5 representations → 3** (Py `CONTRACT_SPEC`, TS `CONTRACT_SPEC`, TS
interfaces). Every future contract edit touches 3 files instead of 5, and the
drift checker compares the two things that actually run. **Do not adopt codegen** —
at 33 messages a generator + staleness check is more complexity than the hand-
copies it removes.

---

## Ranked findings

### Tier 1 — do now (low risk, neutral-or-better functionality) · ~285 LOC

1. `delete:` **`shared/ipc-contract.json`** (182 lines) + simplify `check_contract_sync.py:31-33,156,160-175` to diff Python `CONTRACT_SPEC` vs TS `CONTRACT_SPEC` directly. **~200 LOC. Robustness ↑** (referee removed from between the two runtimes). Risk: low.
2. `shrink:` **`brain/brain/secrets_store.py`** — the `HALO_KEYRING_DIR` file-vs-keyring fork is copy-pasted in 5 functions (`_read_key`, `set_key`, `set_validation_status`, `_validation_status`, `delete_key`). Extract `_backend_get/_set/_del`; each public fn becomes one line. **~35 LOC.** Test seam can no longer drift from prod path. Risk: low.
3. `store.py` task-table DDL exists twice (`_V5_TASK_TABLE` at 146-166 + v1 create at 213-231) then re-declared in the v5 `additions` dict. One `_TASK_TABLE` constant for both creates; keep the ALTER loop only for the pre-v1 path. **~20 LOC.** Removes a "change schema in one place, forget the other" trap. Risk: low-med (migration code — keep the idempotent guards).
4. `delete:` **`brain/brain/tools/docs.py:104-119,184-187`** — dead `if ctx is None else` ternaries; `_llm_text(m,k,ctx)` is identical in both branches (default arg). **~15 LOC.** Risk: low.
5. `shrink:` task-progress string (`title · step N/M — label`) built 3× in `ui/src/orb/OrbRoot.tsx:80-84`, `ui/src/workspace/StatusStrip.tsx:84-89`, `ui/src/tasks/TasksView.tsx:139-142` — already diverged (`·` vs `—`, different falsy filtering). Extract `formatTaskProgress(task)` into `ui/src/lib/lanes.ts`. **~15 LOC.** Capsule/strip/card read identically. Risk: low.
6. `shrink:` **`ui/src/state/reducer.ts:22`** reimplements `operationCorrelationKey` from `ui/src/ipc/contract.ts:184` (a dep-free one-liner) to keep the Node selfcheck import-free. Import it instead (verify `reducer.selfcheck.ts` resolves under plain Node; if not, leave it). **~2 LOC + one less drift point** on the key format error-correlation depends on. Risk: low.

### Tier 2 — structural, med risk, needs care · +~320 LOC

7. `delete:` **`brain/brain/ipc/contract.py:18-305`** — the ~30 unused `*Msg` TypedDicts + `IpcMessage` union (see contract section above). Annotate `parse_ipc_message -> dict`. **~290 LOC.** Removes the guard too. Risk: med (loses field docstrings — mitigate by commenting `CONTRACT_SPEC`).
8. `shrink:` **`ui/src-tauri/src/supervisor.rs:403-421`** — 3× retry ladder around a `try_wait` *error* that essentially never fires for a live child handle. Collapse to "log once, treat as exited." **~12 LOC.** Risk: med (supervision loop).
9. `shrink:` **`brain/brain/tools/docs.py:41-65`** `_llm_text` re-implements `graph._stream_until_stopped`'s "race anext vs cancel Event" pattern. Extract a shared `stream_until(stream, stop_event)`. **~20 LOC net.** One tested cancellation path instead of two. Risk: med (different stop semantics — `ctx.checkpoint()` raises `TaskStopped`; needs care).

### Tier 3 — contingent on product decisions (confirm before cutting)

10. `yagni:` **`brain/brain/server.py:336-434`** per-principal inflight caps (`_owner_counts`, `max_tasks_per_owner=32`) — datacenter fairness for a loopback app with exactly 2 principals; global `max_tasks=128` already bounds memory. **~20 LOC.** Keep if multi-client is ever planned; else drop or leave a `ponytail:` note. Risk: med.
11. `delete:` **`ui/src-tauri/src/supervisor.rs:239-250`** `bundled_sidecar` — already `ponytail:`-marked as always-misses (the PyInstaller step doesn't exist). **~12 LOC.** Cut it *unless Phase-3 packaging is imminent* (it's a deliberate placeholder for that).
12. `delete:` **`ui/src/dev/TokensPreview.tsx`** (134 lines) — lazy-loaded dev QA route (`?dev=tokens`). **~134 LOC if abandoned.** Delete only if nobody uses the route. Risk: low (isolated).
13. `shrink:` **`ui/src/state/reducer.ts:203-237`** `reconcileHistory` (~35 lines) matters only in a narrow race (send into a restored thread *then* history lands). If that coexistence can't happen it collapses to "append server history, drop local." **~20 LOC.** Needs a trace of the race first. Risk: med.

### Rejected — found and deliberately NOT recommended

- `ui/src/ipc/contract.ts:15-286` — deriving the ~30 TS interfaces from `CONTRACT_SPEC` via mapped types (~250 LOC). **No.** Fights CLAUDE.md's documented no-codegen decision, the interface doc-comments are load-bearing, and spec-derived mapped types are exactly the "clever thing decoded at 3am" ponytail warns against.
- Codegen the whole contract from the JSON. **No** — trades hand-copies for a generator + staleness check; complexity up at this scale.

---

## Clean implementation plan

Ordered so each step is independently shippable and gate-verifiable. Run
`python shared/check_contract_sync.py`, `./dev.ps1 -Smoke`, `npx tsc --noEmit`,
and `cargo test` as the guardrails they already are.

**Step 1 — Contract collapse (the main event).** ~490 LOC.
- Delete `shared/ipc-contract.json`. Rewrite `check_contract_sync.py` to load
  Python `CONTRACT_SPEC` and TS `CONTRACT_SPEC` and diff them directly (drop the
  schema-as-third-party plumbing; version check becomes `python == typescript`).
- Delete `brain/brain/ipc/contract.py:18-305` (TypedDicts + union) and
  `check_python_typeddicts`; annotate `parse_ipc_message -> dict`; add field
  comments to `CONTRACT_SPEC`.
- Keep TS interfaces untouched.
- Verify: `check_contract_sync.py` green, `python -m brain.ipc.contract` self-check
  green, `./dev.ps1 -Smoke` green. Update CLAUDE.md's "one schema, two hand-
  mirrored implementations" paragraph to describe the new 3-representation reality.

**Step 2 — Backend dedup.** ~70 LOC, low risk. Findings 2, 3, 4 (secrets_store
backend helpers, `store.py` `_TASK_TABLE` constant, `docs.py` dead ternaries).
Each is a self-contained refactor with existing tests (`test_store.py`,
`test_files.py`) as the check.

**Step 3 — UI dedup.** ~17 LOC, low risk. Findings 5, 6 (`formatTaskProgress` in
`lanes.ts`, import `operationCorrelationKey`). Verify `reducer.selfcheck.ts` still
runs under plain Node after the import change.

**Step 4 — Structural (optional, med risk).** Findings 8, 9 (supervisor try_wait
collapse, shared `stream_until` helper). Do these only with `cargo test` /
`test_docs.py` / `test_graph.py` green before and after; they touch the
supervision loop and cancellation semantics.

**Step 5 — Product-gated cleanups.** Findings 10–13. Each needs a yes/no first:
is multi-client planned (10)? is Phase-3 packaging imminent (11)? is the
`?dev=tokens` route still used (12)? can the history race happen (13)? Cut the
ones whose feature is dead; leave the rest with a `ponytail:` note.

**Do not do:** the two rejected items. If anyone proposes them, this section is why.

---

## Scoreboard (ponytail-gain — benchmark medians, not this repo)

```
  Lines of code   no-skill  ████████████████████  100%
                  ponytail  ██▌·················   6–20%   ▼ 80–94%
  Cost            no-skill  ████████████████████  100%
                  ponytail  █████▌··············   23–53%  ▼ 47–77%
  Speed           ponytail  ▸ 3–6× faster
```

Those are the published 5-task / 3-model medians for *writing new code lazily* —
they do not describe this already-built repo. The only real per-repo number here
is the ~285–790 LOC above, and most of the value is robustness (5→3 contract
copies), not raw line count.
