# System Design: Memory

Curated, short, self-maintaining. The "second brain." Local only.

## Three tiers
| Tier | Store | Lifespan | Purpose |
|---|---|---|---|
| **Session** | RAM (graph state) | one task/conversation | working context |
| **Curated beliefs** | SQLite + sqlite-vec | durable, decays | the few facts Halo *reasons from* (preferences, active projects, workflows, key decisions, failure lessons) |
| **Raw activity log** | SQLite | rolling window | searchable record of what Halo did; powers the activity feed + undo |

Only **beliefs** feed reasoning. The raw log is for audit/search, not stuffed into prompts.

## Schema (beliefs)
```
belief(id, text, kind, embedding, salience, last_used_at, created_at, source, superseded_by)
```
- `kind`: preference | project | workflow | decision | failure_lesson
- `salience`: bumped on use, decayed by the decay job
- `superseded_by`: set instead of hard-delete when auto-corrected (so nothing is truly lost)

## Write path (curated, not everything)
```
end of turn → light model extracts candidate durable facts
   → dedupe against existing beliefs (vector match)
   → new / update / supersede → write
```
Nothing durable? Nothing written. This keeps memory short by design.

## Auto-correction
- New info contradicting a belief → mark old `superseded_by` the new one. Autonomous, but reversible (visible in the memory panel).

## Provenance rule (who can overwrite whom)
- `source` distinguishes **user-stated** (the user said it) from **agent-inferred** (Halo concluded it).
- **User-stated beliefs can only be superseded by a newer user statement** — never by an agent inference. Inference may supersede inference.
- This blocks the classic poisoning path: Halo's own wrong conclusion silently overwriting something the user actually said. User corrections always win and stick.

## Decay (proposed defaults — tune against real usage)
- New belief starts `salience = 0.6`.
- Retrieved & used in a turn → `salience += 0.2` (cap 1.0).
- Background job applies **half-life decay: `salience ×= 0.5` per 30 days unused**.
- `salience < 0.2` → **soft-archive** (still searchable, no longer auto-injected) — never hard-deleted.
- These are calibration knobs, not fixed design; start here and adjust from observed retrieval quality.

## Retrieval (deeper thinking)
- On hard tasks the Brain vector-searches beliefs relevant to the request and injects the top few — reasoning *with* history instead of cold.
- Injection budget: **top-15 beliefs or ~1k tokens, whichever is smaller**, ranked by relevance × salience.

## Inspectable
- **Memory panel** (UI) lists/edits/deletes beliefs and shows superseded history. Because everything is visible and restorable, autonomous correction is safe.

## Failure handling
- Extraction error → skip the write, keep the raw log; never corrupt existing beliefs on a bad parse.
