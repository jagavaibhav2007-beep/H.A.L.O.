# Phase 2 — Backend Spine: Implementation Plan

The real Brain replacing the mock for the safe, useful core, per [phases.md](phases.md#phase-2--backend-spine-the-honest-core): LangGraph control loop, model router, permission gate, 3-tier memory, activity log + undo, Lane-1 local file control, OS-keystore secrets. Built strictly behind the **frozen** [systemdesign/11-ipc-contract.md](systemdesign/11-ipc-contract.md) — the mock proved every shape in Phase 1, so the UI must not change; this phase is a Brain swap, not a rewrite.

**Phase exit criteria (the whole phase is done when):**
1. Real chat: a typed message routes to a real model via OpenRouter and streams `token`s + `done` back through the existing chat view; the router uses the light model by default and demonstrably escalates on a reasoning-heavy prompt.
2. Memory is real: durable facts extracted at end of turn persist across Brain restarts, self-correct (supersede) on contradiction under the provenance rule, decay on schedule, and are editable from the memory panel via `memory_edit` round-trips.
3. Tiers behave per [04-permissions](systemdesign/04-permissions.md): Tier-1 runs silently + logs, Tier-2 runs + surfaces an `activity`, Tier-3 suspends via `interrupt()` → real `approval_request` → approve/deny/edit resume the graph correctly; unknown tools default to Tier 3.
4. Lane-1 local file ops work end to end (read/create/edit/move/organize, read-only commands) under the gate.
5. Activity log + undo are real: every action writes an `action` row; undo executes the recorded inverse; `undoable:false` is honest.
6. Kill the Brain mid-task → on restart the task reappears paused from its last checkpoint and can resume (reconcile-first on mid-tool death).
7. Everything but LLM calls stays on-device; the OpenRouter key lives in Windows Credential Manager, never on disk or in logs.
8. All existing gates stay green (`./dev.ps1 -Smoke`, contract sync, UI selfchecks, cargo) **plus** a new `shared/phase2_check.py` covering the real-Brain paths.

**Stack (from [techstack/](techstack/00-stack-summary.md)):** Python + LangGraph (SqliteSaver checkpointer), OpenRouter (`google/gemma-4-26b-a4b-it` light / `deepseek/deepseek-v4-pro` heavy), SQLite + sqlite-vec, fastembed (`bge-small-en-v1.5`), `keyring`, `httpx` (async). New Python deps live in `brain/pyproject.toml` only.

**Out of scope for all steps:** voice audio/STT/TTS (Voice stays the idle stub; `voice_state`/`transcript` remain mock-only), browser automation, GUI Lanes 2/3, coding-agent orchestration, the self-improvement/skill loop (the skills panel keeps rendering mock `skill_state`s — real skills are Phase 3e), sidecar packaging, multi-user, TLS.

---

## Architecture decisions

**D1 — The real Brain is a third handler behind the same server, not a new server.**
`brain/server.py` keeps its WS server, auth, single-instance lock, envelope validation, per-conversation locks, and role routing untouched — Phase 0/1 proved them. The dispatch table gains a real handler set alongside echo and mock: default launch = real Brain, `--mock` unchanged. The mock stays permanently (UI development, `phase1_check.py`, demo mode). *Rejected:* replacing the mock — it's the UI's test harness and the contract's living documentation.

**D2 — The contract is frozen for this phase.**
Phase 1's whole payoff is that the real Brain emits shapes the UI already renders. No new message types, no new required fields. If a real implementation genuinely can't express something, that's a design-gap conversation (fix the systemdesign doc first, per phases.md) — not a quiet field addition.

**D3 — One store module owns SQLite; LangGraph checkpoints get their own file.**
`brain/brain/store.py` is the single module that touches `%LOCALAPPDATA%\Halo\halo.db` (WAL mode): beliefs, actions, tasks, spend, settings. Every memory write routes through it — it *is* the "one choke point for memory writes" from the overview. LangGraph's SqliteSaver manages its own schema in a separate `checkpoints.db` — its migrations are the library's business, and separating files means a corrupt/wiped checkpoint store can never take beliefs with it. Schema changes append to [mem/MigrationLog.md](mem/MigrationLog.md).

**D4 — The gate is a wrapper around tool execution, not a graph branch per tool.**
One `classify(tool, args) -> tier` pure function (rule table, unit-tested) + one `gated_execute()` that every tool call passes through: Tier 1 → run + log, Tier 2 → run + log + `activity`, Tier 3 → log intent + `interrupt()`. Tools register in a single registry with their classification rules; a tool absent from the rule table is Tier 3 by construction. *Rejected:* per-tool checks scattered in tool code — exactly what the overview's "one enforcement point" principle forbids.

**D5 — Routing is rule-based, not model-based.**
The router picks light/heavy from cheap signals (explicit code/plan/multi-step markers, prompt length, tool-plan depth, a per-conversation escalation flag after a light-model failure). No LLM call to decide which LLM to call. Escalation reasons are logged to the activity feed (`narrate:false`) so routing is inspectable. Model IDs come from settings (defaults per techstack) so they're swappable without code changes.

**D6 — Checkpoint intent-before / result-after; reconcile on resume.**
Per the contract's cancellation section: the graph records tool *intent* before execution and *result* after. Resume after mid-tool death re-enters at the intent record and runs a read-only reconcile per tool type (does the target file exist / did the move happen?) before deciding to re-run or skip. This is what makes exit-criterion 6 honest rather than "re-run and hope".

**D7 — Undo is an inverse recorded at execution time, executed later.**
Each undoable action stores its inverse in the `action` row when it runs (move → move back; create → delete; edit → prior content, content-hash-guarded; organize → the full move list). `undo` looks up the token, checks the inverse's precondition still holds (file unmoved since), executes it as a normal gated Tier-1/2 action, and emits the reversal `activity`. Actions with no true inverse are `undoable:false` up front — never a fake undo.

**D8 — Secrets live in Windows Credential Manager via `keyring`; the key never crosses the IPC boundary.**
The UI's Settings key field sends `settings_update{key:"openrouter_key", value}` once over the authenticated loopback WS; the Brain immediately stores it in the keystore and thereafter reports only status (`set`/`missing`/`invalid`) — the key is never echoed back, logged, written to SQLite, or included in any frame. No key → chat returns a plain `error` frame with the way forward ("add your OpenRouter key in Settings"), not a crash.

**D9 — New dependencies, each earning its place (ponytail-gated):**
- `langgraph` + `langgraph-checkpoint-sqlite` — the control loop, checkpointer, and `interrupt()` are the reason this stack was chosen; hand-rolling resumable-interruptible graph state is the over-engineering trap.
- `httpx` — async OpenRouter streaming; `websockets` is already async, blocking `requests` would stall every conversation.
- `sqlite-vec` + `fastembed` — mandated by techstack for belief similarity; no cloud embeddings.
- `keyring` — mandated for secrets.
- **Not added:** an ORM (the schema is 5 tables — raw SQL in one module), a migration framework (versioned `schema_version` pragma + idempotent DDL), LangChain beyond LangGraph's core (no chains/agents abstractions — the graph is explicit), a scheduler lib (decay is a timestamp check on Brain start + a daily asyncio timer).

---

## Cross-cutting error-prevention rules (apply to every step)

1. **Never block the event loop:** every LLM call is async `httpx`; file/DB/embedding work runs in `asyncio.to_thread`. A slow tool in one conversation must not freeze another.
2. **A turn is never silently dropped:** every handler path ends in `done` or `error{recoverable}` — including OpenRouter timeouts, tool exceptions, and gate denials.
3. **Redact at the source:** `args_redacted` in `activity`/`approval_request` frames and `action` rows is produced by the tool registry's per-tool redactor before anything leaves the gate. Full args exist only inside the graph state.
4. **Secrets never in frames, logs, DB, or exceptions** (D8) — greppable rule: the string from keyring exists in exactly one call site (the OpenRouter client header).
5. **Transactional writes:** every store mutation is one transaction; a failed extraction or crash mid-write can never half-corrupt beliefs (memory doc: "never corrupt existing beliefs on a bad parse").
6. **Provenance is enforced in the store, not the caller:** `store.supersede()` itself rejects inference-over-user-stated — no caller can bypass the rule.
7. **Path safety at the tool boundary:** every file tool resolves paths, refuses traversal outside the user profile and configured project roots, and treats system/hidden dirs as Tier-3 territory.
8. **Fail closed:** unknown tool → Tier 3; unreadable settings → defaults; missing key → honest error; classification exception → Tier 3.
9. **Frames only via the existing validated `_send()` path** — the real Brain physically cannot emit a shape the mock couldn't, same as Phase 1.
10. **Spend is accumulated per call** from OpenRouter usage fields into the store and pushed as `spend_update` — cost visibility is a feature, not telemetry.

---

## Step 1 — Store foundation (SQLite + migrations)

**Intent:** The disk layer everything else writes to (D3), before any consumer exists.

**Design:**
- `brain/brain/store.py`: opens `%LOCALAPPDATA%\Halo\halo.db` (WAL, `busy_timeout`), runs idempotent versioned DDL. Tables: `belief(belief_id, text, kind, embedding, salience, provenance, status, superseded_by, created_at, last_used_at)` · `action(action_id, tool, args_redacted, tier, lane, result, undo_token, inverse_json, task_id, ts)` · `task(task_id, state, lane, title, step, steps_total, step_label, reason, thread_id, updated_at)` · `spend(day, usd)` · `settings(key, value)`.
- sqlite-vec virtual table for belief embeddings; embeddings computed by fastembed in a thread.
- Typed accessor functions only — no raw SQL outside this module. `supersede()` enforces provenance (rule 6).
- First run creates the directory next to the existing `session.json`.

**Edge cases:** DB locked by a stale process → `busy_timeout` + clear startup error, never a silent hang; fastembed model download on first use → happens once at startup with a logged note, not mid-turn; corrupt DB file → fail loudly with the file path (recovery is the user's call — no auto-wipe of memory).
**Deliverables:** store module, schema v1, `brain/tests/test_store.py` (plain assert: CRUD, provenance enforcement, supersede chains, vector search round-trip); MigrationLog.md entry.
**Acceptance:** tests green; two Brain starts in a row reuse the same DB without re-running DDL; a user-stated belief cannot be superseded by an inferred one at the store level.

---

## Step 2 — Secrets & key onboarding

**Intent:** The OpenRouter key path (D8) — first, because every LLM step after this needs it, and it unblocks the Settings panel's disabled placeholders.

**Design:**
- `brain/brain/secrets.py`: thin `keyring` wrapper (`get/set/delete("halo", "openrouter")`).
- `settings_update{key:"openrouter_key"}` handler: store to keystore, validate with one cheap OpenRouter `GET /models` call, then push a status-only `settings_update`-shaped state via the existing snapshot path (status string, never the key).
- Settings panel: the Phase-1 `●●●` placeholder becomes a real input wired to this flow; status dot reflects `set/missing/invalid`.

**Edge cases:** keystore unavailable (rare) → honest error frame, no plaintext fallback file; key deleted mid-session → next LLM call fails → `error{recoverable:true}` with the Settings pointer; validation call offline → store anyway, mark `unverified`, verify lazily on first real call.
**Acceptance:** key survives restart via Credential Manager only (`git grep` and DB dump show no key material); wrong key → visible `invalid` status + honest chat error.

---

## Step 3 — OpenRouter client & model router

**Intent:** One async client + the light/heavy chooser (D5). No graph yet — this is the LLM I/O layer.

**Design:**
- `brain/brain/llm.py`: async `httpx` streaming chat-completions client against OpenRouter; yields text deltas; captures usage (tokens, cost) per call; retries once on transient 5xx/timeout with jitter, then raises.
- `route(prompt, context) -> model_id`: rule table per D5. Signals: code fences/stack traces, planning verbs + multi-step shape, prompt length threshold, explicit user ask ("think hard"), sticky per-conversation escalation after a light-model quality failure (formatting/refusal heuristic), reset on new conversation.
- Spend: per-call cost accumulated into `spend(day)` and pushed as `spend_update{session_usd, month_usd}` (frame already exists; UI already renders it).

**Edge cases:** stream drops mid-reply → partial tokens already sent stay sent, turn closes with `error{recoverable:true}` (UI already renders in-bubble errors + input restore); 429 → single retry then honest error naming the model; model ID invalid (user changed settings) → error names the setting.
**Deliverables:** client + router + `brain/tests/test_router.py` (routing table cases, pure function); spend accumulation.
**Acceptance:** manual: a real prompt streams into the existing chat view; router test green; `spend_update` moves in Settings after a real call.

---

## Step 4 — LangGraph control loop (chat spine)

**Intent:** The graph from the overview — `perceive → route → plan → [gate] → execute → checkpoint → narrate → loop → done` — replacing echo as the default `user_msg` handler (D1). This step ships **chat with no tools**: the full graph shape with an empty tool registry, streaming real replies.

**Design:**
- `brain/brain/graph.py`: LangGraph `StateGraph` with SqliteSaver on `checkpoints.db`; **thread_id = `conversation_id`** — the contract's "one thread per conversation" is literally LangGraph's thread model. The existing per-conversation `asyncio.Lock` in `server.py` stays the serialization point; the graph runs inside it.
- Graph state: messages, pending tool intent/result (for D6), route decision, injected beliefs (Step 7 fills this), task linkage.
- `token` frames stream from the model callback through the existing broadcast `_send()`; `done` on graph completion.
- `interrupt` (inbound) → LangGraph interrupt of that conversation's run: suspend at last checkpoint, emit `task_state: paused` with `reason:"you said stop"`; a follow-up `user_msg` resumes with redirection (the "stop → what should I do differently? → resume" loop).
- Session-tier memory = the thread's message state, per the memory doc.

**Edge cases:** two `user_msg`s racing one conversation → already serialized by the existing lock (proven in Phase 0); Brain killed mid-turn → checkpoint holds; on restart the turn is *not* auto-resumed (a dead half-streamed chat turn resumes only on the user's next message — no surprise ghost replies); graph exception → `error` frame, checkpoint intact.
**Deliverables:** graph module; real handler wired as default dispatch; `interrupt` handling.
**Acceptance:** `./dev.ps1` (no `-Mock`) gives real streamed chat; interrupt mid-stream pauses cleanly and the next message redirects; kill-Brain mid-stream → reconnect → next turn works with history intact (checkpoint), existing smoke test still green.

---

## Step 5 — Permission gate & tool registry

**Intent:** The choke point (D4), landed **before** any tool exists to trip it — per phases.md, "build the choke point before the paths that use it."

**Design:**
- `brain/brain/gate.py`: `classify(tool, args) -> tier` — pure rule table keyed by tool name + arg predicates (e.g. `file_move` inside project roots = Tier 2, overwrite/delete = Tier 3, anything touching system dirs = Tier 3). Unknown → Tier 3.
- `gated_execute(tool_call)`: classify → log intent to `action` → Tier 1: run; Tier 2: run + `activity`; Tier 3: emit `approval_request` (summary sentence authored here — the Brain owns the human sentence, per Phase 1) + LangGraph `interrupt()`.
- `approval_response` resumes the graph: approve → execute; deny → the graph's denied branch (model told, continues what it can); edit → re-classify edited args (an edit can *raise* the tier, never assume it lowers), then execute.
- Destructive flag: rule table marks delete/overwrite/spend classes `destructive:true` (the UI's hold-to-approve variant is already built).
- Away flow: no user input 5 min (`GetLastInputInfo` via ctypes) → Tier-3 pauses emit the existing native toast path (`task_state: waiting_approval` already drives it in the UI).
- **Interrupt vs pending approval** (contract rule): `interrupt` while `waiting_approval` → implicit deny first, then suspend — the mock proved the UI side; now the real graph does it.

**Edge cases:** double `approval_response` for one approval → first wins, second gets recoverable `error` (same behavior the mock established); approval arriving after its conversation was stopped → recoverable error, no zombie resume; classification exception → Tier 3 (rule 8); approval cards wait forever — no timeout-approve, ever.
**Deliverables:** gate + registry + `brain/tests/test_gate.py` (classification table, edit-reclassify, unknown-tool default, implicit-deny ordering).
**Acceptance:** with a fake registered test tool: Tier-1/2/3 behaviors observable end to end in the real UI, all three approval branches resume correctly, gate tests green.

---

## Step 6 — Activity log & undo (real)

**Intent:** Exit criterion 5 — the flight recorder and its reversals become real (D7).

**Design:**
- Gate writes every executed action to `action` (already in Step 5's intent/result path); `activity` frames emit from the same write — one code path, log and feed can't drift.
- Inverse recording per tool type at execution time: `inverse_json` + `undo_token` (uuid) stored on the row; tools with no inverse write `undoable:false`.
- `undo` handler: look up token → precondition check (target unmoved/unchanged since, via stored content hash or path check) → execute inverse *through the gate* (an undo of a Tier-2 move is itself a gated Tier-2 move) → reversal `activity` referencing the same `task_id` (frame shape the UI already renders from `demo` scenarios).
- Feed history: on connect, recent `action` rows hydrate the snapshot's activity backlog (the UI's ring buffer caps at 10k; the DB is the full log).

**Edge cases:** undo of an undo → the reversal row gets its own inverse (it's just an action); precondition fails (file moved since) → recoverable error with the plain reason, no forced overwrite; unknown/expired token → recoverable error; double-click undo already guarded UI-side (rule 3) *and* idempotent here (token consumed on first success).
**Deliverables:** inverse recording, undo handler, snapshot hydration; `test_undo.py` (record→invert→precondition-fail matrix on temp files).
**Acceptance:** move a real file via chat → undo from the feed puts it back and the reversal renders; a sent-nowhere `undoable:false` action shows "not reversible"; undo tests green.

---

## Step 7 — Lane-1 local file tools

**Intent:** The first real capability under the gate: read/create/edit/move/organize + read-only commands, per phases.md item 6.

**Design:**
- `brain/brain/tools/files.py`, registered with classifications: `file_read`/`dir_list`/`file_search` T1 · `file_create`/`file_edit`/`file_move`/`dir_organize` T2 in project roots · overwrite/delete anywhere, or any write outside roots, T3 destructive · `run_readonly_cmd` (allowlist: `git status/log/diff`, `dir`, etc.) T1, anything else refused (not Tier-3 — arbitrary shell is out of scope until a sandbox exists).
- Project roots: `settings` table, default Desktop/Documents/Downloads; path resolution + traversal refusal per rule 7.
- Organize: the model plans a move list → one Tier-2/3 approval covering the whole batch (summary names counts + destinations) → each move logged individually with inverses → one undo token reverses the batch reverse-order.
- Long ops are cooperatively cancellable (check a cancel flag between files; ≤2s stop per the contract).
- Task linkage: multi-step file work runs as a `task` row with `task_state` progress frames (`step/steps_total/step_label` — shapes the tasks view already renders).

**Edge cases:** file vanishes mid-plan → reconcile (D6) skips it with an `activity` note, no crash; name collision on move → suffix, recorded in the inverse; huge dir organize → step-capped per approval (no thousand-file silent batch); symlinks resolved before root checks.
**Deliverables:** file tools + registry entries + `test_files.py` (temp-dir matrix: roots, traversal, collision, batch inverse).
**Acceptance:** "organize my Downloads by type" runs as a real task — plan, Tier-2/3 gate, stepped progress in the tasks view, per-move activities, batch undo restores everything; exit criteria 3/4/6 exercised live with this surface.

---

## Step 8 — Memory: extraction, retrieval, panel round-trips

**Intent:** The second brain becomes real per [03-memory](systemdesign/03-memory.md), wired to the panel the UI already built.

**Design:**
- **Write path:** end of turn → light-model extraction ("durable facts?" — skipped when the turn is trivially transient) → parse to candidates → embed → vector-dedupe against existing beliefs → new / update / supersede via `store` (provenance enforced there, rule 6). Extraction failure → skip write, log, never touch existing rows.
- **Auto-correct:** contradiction detection rides the same dedupe pass (high similarity + light-model "contradicts?" check) → supersede under the provenance rule → `belief_state` delta + `activity(narrate:true)` ("updated what I remember — …") — the delight moment Phase 1 scripted, now real.
- **Retrieval:** at `perceive`, vector-search beliefs by the user message; inject top-15-or-~1k-tokens ranked relevance × salience into graph state; bump `salience += 0.2` (cap 1.0) + `last_used_at` on injected beliefs.
- **Decay:** on Brain start + daily timer: `salience ×= 0.5` per 30 unused days; `< 0.2` → soft-archive (`belief_state` delta). Thresholds read from `settings` (calibration knobs per the doc).
- **Panel:** `memory_edit` edit/delete/restore handlers against the store, emitting the confirming `belief_state` deltas (the UI's rule-3 pending states already wait for exactly these); snapshot-on-connect pulls real beliefs.

**Edge cases:** extraction returning junk/malformed JSON → skip (rule 5); user edits a belief mid-turn while extraction runs → store transactions serialize, last write wins by timestamp; dedupe false-positive merging distinct facts → conservative threshold, prefer a near-duplicate belief over a wrong merge; embedding model unavailable → beliefs still writable, search degrades to recency, logged honestly.
**Deliverables:** extraction/retrieval/decay modules; `memory_edit` handlers; `test_memory.py` (dedupe, provenance supersede matrix, decay math, injection budget).
**Acceptance:** tell Halo "I switched to pnpm" → belief appears in the panel (user-stated); a later inferred contradiction cannot displace it, but a new user statement supersedes with visible chain; restart → memory intact; panel edit/delete/restore round-trip green against the real Brain; decay demonstrable with a shortened test half-life.

---

## Step 9 — Real snapshot, history summarization & spend

**Intent:** Close the truthfulness gaps: reconnect snapshot from real state, long-chat summarization, spend from real usage.

**Design:**
- **Snapshot-on-connect** (real): live `task`s, pending approvals, `belief_state`s, `spend_update`, recent activity backlog — from SQLite + graph state, same shapes and idempotence rules the mock established (upserts converge; `phase1_check.py`'s snapshot assertions become the spec here).
- **Summarization:** past a token threshold, the light model distills the oldest span into a summary message in graph state; spans distilled into a summary or belief are dropped from live history (the chat doc's no-double-injection rule).
- **Spend:** already accumulating (Step 3); month rollup query feeds `spend_update` on connect and after each call.

**Edge cases:** summarization call fails → keep full history this turn, retry next turn (never lose context to save tokens); snapshot during an open approval → card re-renders pending, not duplicated (approval_id upsert, proven UI-side).
**Acceptance:** kill+restart mid-everything → reconnected UI shows correct tasks/approvals/beliefs/spend with no duplicates; a deliberately long conversation stays coherent past the threshold with visibly bounded prompt sizes (logged token counts).

---

## Step 10 — Phase E2E gate & verification

**Intent:** Lock the exit criteria behind a repeatable check, mirroring Steps 8/15 of the prior phases.

**Deliverables:**
- `shared/phase2_check.py` (plain asyncio+assert, fake UI client against the **real** Brain with a stub LLM injected via env — no paid calls in the gate): real chat turn streams and completes; Tier-1/2/3 behaviors with a test tool (all three approval branches + implicit-deny-on-interrupt); file op + undo round-trip on a temp dir; belief write → restart → belief survives; provenance rejection; snapshot idempotence.
- A `HALO_LLM_STUB` hook in `llm.py` (env-gated fake streamer) so the gate and tests run offline and deterministic — the same seam the router tests use.
- `./dev.ps1 -Smoke` extended: Phase 0 → Phase 1 → Phase 2 checks in sequence.
- [VERIFY.md](VERIFY.md) gains a Phase-2 native checklist: real-key chat, escalation prompt, Downloads-organize + undo, memory panel round-trips against the real Brain, kill-mid-task resume, key-missing honesty.
- mem/ updates: MigrationLog (schema v1), Decisions (D1–D9), Patterns (gate/tool-registry pattern).

**Acceptance:** all three phase gates green in one `-Smoke` run; the manual checklist completes with no blocking finding; `python shared/check_contract_sync.py` still passes with zero contract diffs from Phase 1 (proving D2 held).

---

## Build order & dependencies

```
Step 1 (store) ──┬─> Step 4 (graph/chat) ──> Step 5 (gate) ──┬─> Step 6 (undo) ─┐
Step 2 (secrets) ┤                                           ├─> Step 7 (files) ├─> Step 9 (snapshot/summarize) ─> Step 10 (E2E)
Step 3 (LLM+router) ┘                                        └─> Step 8 (memory) ┘
```

Steps 1–3 are independent and can land in any order (2 and 3 pair naturally). Step 4 needs all three (checkpoint file beside the store, key, client). Step 5 needs 4 (the graph to interrupt). Steps 6–8 need 5 (everything routes through the gate) and are then independent of each other — schedule 7 (files) early: it's the surface that exercises the gate, tasks view, and undo for real. Step 9 needs real state to snapshot (6–8). Step 10 closes the phase.
