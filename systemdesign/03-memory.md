# System Design: Memory (v2 — consolidation redesign, 2026-07-23)

Curated, short, self-maintaining. The "second brain." Local only.

> **Status:** v2 is **implemented.** M1–M3 shipped — span consolidation with the
> AUDN decision matrix replaced the v1 per-turn extraction write path, and the
> schema migrated through v2 (consolidation/episodic/bi-temporal columns) to v3
> (the `doc_digest` content-hash cache). Sections marked **[v2 new]** describe
> live behavior, not a plan; see `mem/MigrationLog.md` for the schema history.
>
> **Why v2:** measured after 3 days of real use, the v1 write path had turned
> one user statement into ~8 near-duplicate belief rows, 60% of the table was
> dead (archived/superseded) yet every row was replayed to the UI on every
> connect, and extraction cost O(N) LLM calls per N-turn session. This is a
> known failure class: a production mem0 audit (mem0ai/mem0#4573) found 97.8%
> of stored memories were junk, root-caused to permissive per-turn extraction —
> a stronger extraction model made it *worse*. v2 adopts the cross-industry
> consensus (mem0's ADD/UPDATE/DELETE/NOOP pipeline, Letta's sleep-time
> consolidation, Graphiti's timestamp invalidation, LangMem's hot-path vs
> background split): extract once per session segment in the background, let an
> LLM decide against retrieved neighbors instead of inserting unconditionally,
> and never ship dead rows to the UI.

## Four tiers

| Tier | Store | Lifespan | Purpose |
|---|---|---|---|
| **Working** | RAM (LangGraph state) | one conversation | working context |
| **Episodic [v2 new]** | SQLite `session_summary` | durable | one compact summary per conversation-session: what happened, why, open loops |
| **Semantic (beliefs)** | SQLite + sqlite-vec | durable, decays | the few facts Halo *reasons from* (preferences, active projects, workflows, key decisions, failure lessons) |
| **Raw activity log** | SQLite | rolling window | searchable record of what Halo did; powers the activity feed + undo |

Only **beliefs** and (sparingly) **session summaries** feed reasoning. The raw
log is for audit/search, not stuffed into prompts.

Design note: a per-session summary is the **episodic layer, not a replacement
for the belief store**. No surveyed production system (mem0, Letta/MemGPT,
Zep/Graphiti, LangMem) uses summary blobs as primary memory — summaries lose
per-fact retrieval granularity, per-fact provenance (the rule-6 poisoning
defense), and per-fact edit/delete (the memory panel). Summaries answer "what
were we doing"; beliefs answer "what is true now." Both stay small.

## Schema

```
belief(id, text, kind, embedding, salience, last_used_at, created_at, source,
       superseded_by, valid_at, invalid_at)          -- [v2 new: valid_at/invalid_at]
session_summary(id, conversation_id, text, key_points_json,
                created_at, embedding)                -- [v2 new]
conversation_meta(conversation_id, consolidation_cursor,
                  last_activity_at)                   -- [v2 new]
```

- `kind`: preference | project | workflow | decision | failure_lesson
- `salience`: bumped on use, decayed by the decay job
- `superseded_by`: set instead of hard-delete when auto-corrected (so nothing is truly lost)
- **[v2 new]** `valid_at`/`invalid_at` (Graphiti pattern): a contradicted or
  merged-away belief gets `invalid_at = now()` instead of deletion. Retrieval
  and UI hydration filter `WHERE invalid_at IS NULL`. History stays queryable;
  the live set stays small. The UI-facing `status` enum
  (active/archived/superseded) is unchanged — the columns are the storage
  truth, `status` is derived for the frozen IPC contract.
- `key_points_json`: small structured JSON — `{"key_points": [...],
  "open_loops": [...], "artifacts": [...]}` — 2–3 sentences of prose in
  `text`, the rest structured. Embedded for vector retrieval like beliefs.

## Write path [v2 — replaces v1 per-turn extraction]

**v1 (retired):** every turn → extract over last 10 messages → distance-only
dedup → insert. Caused paraphrase duplicates (0.94-cosine gate misses
rephrasings), fragment spam (one statement → many rows), and O(N) extraction
calls per session.

**v2: consolidation runs off the hot path, once per session segment.**

```
trigger (idle / shutdown / startup-recovery / pressure)
  → read transcript since conversation_meta.consolidation_cursor
  → ONE light-model extraction call over that whole span
      (prompt carries negative examples + the IDs of beliefs that were
       injected into the conversation, so restatements of Halo's own
       memory are never re-extracted — the mem0#4573 feedback-loop fix)
  → for each candidate: vector-fetch top-5 existing neighbors
  → ONE light-model decision call per candidate (mem0 AUDN):
        ADD              no equivalent exists
        UPDATE(id)       complements/corrects an existing belief
        INVALIDATE(id)   contradicts an existing belief → close its window,
                         insert the new fact (never delete)
        NOOP             duplicate or not durable
  → same pass writes the session_summary row
  → advance consolidation_cursor  (idempotent, crash-safe)
```

**Triggers** (all funnel into the same consolidation function):
- **Idle:** no message on the conversation for `memory_idle_seconds`
  (default 1800; settings knob — doubles as the test seam, tests set it to ~0).
- **Shutdown:** Brain graceful close consolidates all dirty conversations.
- **Startup recovery:** any conversation whose checkpoint has messages past
  its cursor is consolidated on next start — a crash never loses a session,
  it just consolidates late.
- **Pressure (Letta pattern):** if the un-consolidated span exceeds ~8k
  estimated tokens mid-session, consolidate the older segment early.
  Consolidation is triggered by the thing it relieves, not only by a clock.

**Cost:** an N-turn session goes from N extraction calls to 1 extraction +
0–3 decision calls + 1 summary. Duplicates die at the decision step instead
of accumulating.

**Provenance enforcement is defense-in-depth:** the decision model's
UPDATE/INVALIDATE verdicts are *proposals*; `store.py` still refuses any write
where an inferred candidate would displace a user-stated belief (rule 6). A
wrong LLM verdict degrades to "kept both," never to poisoned memory.

Nothing durable in the span? One NOOP-ish extraction reply, a summary row,
cursor advances. Memory stays short by design.

## Auto-correction

- New info contradicting a belief → INVALIDATE: old row keeps its text with
  `invalid_at` + `superseded_by` set; new row inserted. Autonomous, but
  reversible (visible in the memory panel).

## Provenance rule (who can overwrite whom) — unchanged from v1

- `source` distinguishes **user-stated** from **agent-inferred**.
- **User-stated beliefs can only be superseded by a newer user statement** —
  never by an agent inference. Inference may supersede inference.
- This blocks the classic poisoning path: Halo's own wrong conclusion silently
  overwriting something the user actually said. Enforced in the store, below
  the LLM decision layer.

## Decay — unchanged mechanics, clarified interaction

- New belief starts `salience = 0.6`; retrieved & used → `+0.2` (cap 1.0).
- Background job: half-life decay `×0.5` per 30 days unused.
- `salience < 0.2` → **soft-archive** (status change; `invalid_at` stays NULL —
  archived is "not auto-injected," not "false"). Restorable from the panel.
- Session summaries do not decay; they are already one bounded row per session.

## Retrieval

- Beliefs: vector search, top-15 or ~1k tokens (whichever smaller), ranked
  relevance × salience, filtered to live rows
  (`invalid_at IS NULL AND status = 'active'`). Injected belief IDs are
  recorded in the turn context so consolidation can skip restatements.
- **[v2 new]** Episodic: the most recent session summary for the active
  conversation's predecessor (~100 tokens) is prepended for continuity; up to
  2 older summaries join only on vector relevance. Separate ~300-token budget.
  Summaries never enter the prompt wholesale-by-count — relevance only.

## UI hydration [v2 — replaces replay-everything]

- Connect snapshot sends **live beliefs only** (`invalid_at IS NULL`,
  status active), capped at 50, salience-ranked. v1 replayed up to 100 rows of
  every status — 60% dead — on every connect; that is retired.
- Superseded/archived history is fetched on demand: new inbound message
  `memory_query` (panel opens history → Brain replies with the same
  `belief_state` frames). Contract addition mirrored in
  `shared/ipc-contract.json`, both contract mirrors, and the mock, per the
  standard new-message-type checklist.
- Permanent removal is a separate, explicit archived-view action. The Brain
  rejects purge requests for active or superseded history.
- The memory panel gains a "Sessions" section (Phase 3 UI) listing session
  summaries; summaries are not part of the connect snapshot.

## Inspectable — unchanged

- Memory panel lists/edits/deletes beliefs and shows superseded history.
  Because everything is visible and restorable, autonomous correction is safe.
  Panel edits remain provenance-user and re-embed (v1 behavior).

## Failure handling

- Extraction/decision error → skip the write, **do not advance the cursor**;
  the next trigger retries the same span. Never corrupt existing beliefs on a
  bad parse (rule 5).
- Consolidation must never block or fail a live turn: it runs as supervised
  background work (`_ServerRuntime`), same as v1's fire-and-forget contract.
- `HALO_EXTRACT_STUB` seam is retargeted at the consolidation path so
  phase2_check stays offline-deterministic; tests drive triggers via
  `memory_idle_seconds ≈ 0` plus the shutdown hook.

## Rollout (each step independently shippable)

1. **M1 — stop the bleeding:** snapshot filters to live+capped rows; add
   `conversation_meta` cursor + idle/shutdown/startup triggers; retire the
   per-turn extraction call.
2. **M2 — AUDN decisions:** neighbor-fetch + decision call replaces
   distance-only dedup and the first-3-words `_contradicts` heuristic.
3. **M3 — episodic layer:** `session_summary` table + retrieval prepend +
   `valid_at`/`invalid_at` migration (schema v2 in mem/MigrationLog.md).
4. **M4 — panel history:** `memory_query` and archived/superseded belief
   hydration are shipped. The Sessions UI remains Phase 3.

## References (research 2026-07-23)

- mem0 two-phase extract→decide pipeline and token/latency numbers:
  arXiv 2504.19413; junk audit mem0ai/mem0#4573.
- Letta/MemGPT sleep-time consolidation and pressure triggers:
  docs.letta.com/guides/agents/architectures/sleeptime, arXiv 2504.13171.
- Zep/Graphiti bi-temporal edge invalidation: github.com/getzep/graphiti.
- LangMem hot-path vs background memory and profile-vs-collection tradeoff:
  langchain-ai.github.io/langmem.
- Hindsight, "The Consolidation Problem in Agent Memory" (2026-05-21):
  summaries are lossy compaction, not consolidation.
