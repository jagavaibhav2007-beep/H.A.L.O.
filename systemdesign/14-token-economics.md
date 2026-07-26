# System Design: Token Economics & Tool Answerability

Status: **design — not yet implemented.** Supersedes nothing; complements [13-document-ingestion](13-document-ingestion.md), which attacked a different term of the same cost equation and is corrected in-place by this doc's *Corrections to doc 13* section.

## Problem (measured)

Asking Halo to (1) find a specific `.pdf` in Downloads and (2) "list all the latest downloads" burned **>150,000 tokens** on the OpenRouter dashboard. Halo's own spend meter did not reflect it.

Doc 13 already fixed per-payload size (`file_read` 64KB → 8KB, extraction, projection-time tool-result stubbing) and those fixes **work** — instrumenting the real graph shows `_tool_stub` firing correctly, dropping round 2 from 2,844 → 760 tokens. The blowup happened anyway, because doc 13 optimized the wrong term.

### The cost equation

```
cost  =  requests-per-task  ×  tokens-per-request  ×  price-per-token
```

- Doc 13 attacked **tokens-per-request** only.
- This incident was **requests-per-task**: a bounded payload multiplied by a loop the model could not exit.
- `escalated` (below) silently attacks **price-per-token** and nothing guards it.

### Verified causes, ranked

1. **`dir_list` cannot answer the question that was asked.** `_dir_list` ([files.py:126](../brain/brain/tools/files.py)) returns only `{name, is_dir}`. `grep -rn "st_mtime|st_size|getmtime" brain/` returns **zero hits in source**. "Latest / newest / recent" is *structurally unanswerable*. `run_readonly_cmd` with `dir`/`ls` is no escape hatch — [files.py:178](../brain/brain/tools/files.py) routes through `_dir_list` and discards even `is_dir`. **No path in the app returns a timestamp.** The model retries, re-globs, re-lists; each retry re-sends the whole prompt. Measured: rounds 3–8 of a flail = **19,228 tokens producing no new information**.

2. **The cap keeps the wrong entries.** `nsmallest(500, key=name.casefold())` keeps the **alphabetically first** 500 — anti-correlated with "latest".

3. **Tool results reach the model as invalid JSON.** [gate.py:277](../brain/brain/gate.py) byte-slices the *serialized* payload at `_RESULT_CAP`. Verified: 500 realistic filenames → 38,890 chars, cut at 8,192 → **106 of 500 entries**, ending mid-object (`…"is_dir": false}, {"name": "some_realistic_download` + `…[truncated]`). The model cannot tell truncation from corruption, so it retries.

4. **`tool_specs()` rides every round.** Measured **5,614 chars = 1,403 tokens for 10 tools**, re-sent on every request including post-tool-result follow-ups. That is **49–58% of all conversational spend** in the measured runs. Doc 13's own `doc_digest` added the 10th tool to this per-round toll.

5. **Consolidation re-reads the full *unstubbed* span.** [memory.py:120](../brain/brain/memory.py) joins `messages[cursor:]` with tool results verbatim. Layer 1 is a `_prompt_messages` *projection* and does not apply here. Measured **12,656 tokens**, a 36% session surcharge. It did not fire mid-session this time (est 6,171 vs `_PRESSURE_TOKENS` 8,000); a slightly longer session pays it inline.

6. **`escalated` is a one-way latch.** Set on *any* mid-LIGHT stream exception ([graph.py:373](../brain/brain/graph.py)); the identifier appears only at lines 85, 147, 373 — **no reset exists**, and `run_turn`'s per-turn reset clears five other fields but not this one. Verified empirically: one forced blip, then three trivial messages, all routed to `deepseek/deepseek-v4-pro`. Permanent for the life of the checkpoint. A transport 5xx — which says nothing about model quality — permanently buys the expensive model.

7. **The meter cannot see any of it.** Five independent defects, zero token cost, total loss of visibility:
   - `usage_out["cost"] = …` is an **assignment**, and one `ctx["usage"]` dict is reused for every round ([llm.py:200](../brain/brain/llm.py), [graph.py:356](../brain/brain/graph.py)) → bills **round N only**. Measured undercount: turn 1 **1.94x**, turn 2 **3.95x**.
   - Only `cost` is read; `prompt_tokens` / `completion_tokens` / `cached_tokens` / `reasoning_tokens` are discarded — despite the request asking for them.
   - `_maybe_summarize` passes **no `usage_out`** ([graph.py:308](../brain/brain/graph.py)) — entirely unbilled.
   - `docs._llm_text` passes **no `usage_out`** — a 16-path `doc_digest` is 17+ invisible LIGHT calls.
   - `memory.py` calls `store.add_spend` directly, bypassing `_session_usd` → measured divergence **2.9x** (`session_usd` 0.00668 vs `month_spend` 0.01934).

### Where the 150k went — honest decomposition

| term | tokens | confidence |
|---|---|---|
| ≤16 requests × (206 system + 1,403 tools + ≤1,300 memory + history) | 48k–128k | mechanism CONFIRMED; round count PLAUSIBLE |
| Consolidation over the unstubbed span | 12,656 | CONFIRMED |
| Output + reasoning tokens on HEAVY once latched | unmeasured | UNVERIFIED |
| Retry re-sends ([llm.py:288](../brain/brain/llm.py)) | unmeasured | mechanism CONFIRMED, count UNVERIFIED |

A measured 6-round floor of **35,411** was reproduced offline. The exact 150k is **not** reconstructed here and must not be claimed — the instrumentation in Track C is what will close this gap.

## Design: four tracks

Ordered by the term each attacks. Track C ships **first** despite being worth 0% by itself — without it, no other claim in this doc is verifiable.

### Track C — make cost visible (prerequisite)

*Attacks nothing. Enables measuring everything.*

OpenRouter already returns, on every response, with no extra request and no flag ([usage accounting](https://openrouter.ai/docs/use-cases/usage-accounting)):

```json
"usage": { "prompt_tokens": 194, "completion_tokens": 2, "total_tokens": 196,
  "prompt_tokens_details": { "cached_tokens": 0, "cache_write_tokens": 100 },
  "completion_tokens_details": { "reasoning_tokens": 0 },
  "cost": 0.95 }
```

- **C1.** `_stream_once` reads `prompt_tokens`, `completion_tokens`, `cached_tokens`, `reasoning_tokens` and **accumulates with `+=`**, never assigns. A retried round genuinely *is* billed twice, so accumulating both attempts is correct and makes retry cost visible for the first time.
- **C2.** Thread `usage_out` into `_maybe_summarize` and `docs._llm_text`. Every LLM call in the process must route its usage somewhere.
- **C3.** `memory.py` accumulates into the same session total instead of calling `add_spend` blind, so `session_usd` stops lying by 2.9x.
- **C4.** `spend_update` gains **optional** `session_tokens` and `last_turn_tokens`. Optional = no UI or contract breakage; requires the three-file mirror + `shared/check_contract_sync.py`.
- **C5.** Drop the deprecated `"usage": {"include": true}` ([llm.py:171](../brain/brain/llm.py)); `stream_options.include_usage` is the current form.

**Acceptance:** `cached_tokens > 0` on a second identical-prefix request tells us empirically whether `google/gemma-4-26b-a4b-it` caches at all on its OpenRouter route — the research could **not** confirm this from docs (the caching table lists "Google Gemini 2.5", not Gemma). Measure; do not assume.

> **Open risk, gates Track A's measurement.** `HALO_LLM_STUB` short-circuits `stream_chat` before `_stream_once`, so **no automated gate exercises the live usage-capture path at all** — `_record_usage` is unit-tested, but the code reading `usage` off a real SSE stream has never run. The specific unknown: **does a round ending in `tool_calls` emit a usage chunk?** If OpenRouter only sends usage on the final content-bearing chunk, tool rounds — precisely the rounds that caused this incident — record nothing, and per-turn `prompt_tokens` undercounts by the tool-round count. That would make Track A's before/after comparison measure the wrong quantity. **Resolve with one real-key run of the repro before Track A's measurement step**, which also answers the Gemma-4 caching question in the same pass.

### Track A — tool answerability (the dominant term)

*Attacks requests-per-task. The single highest-value change in this doc.*

**Principle, copied from Claude Code's `Glob`:** *"Results are sorted by modification time and capped at 100 files."* mtime is spent on the **ordering**, not shipped as a **field per entry**. The tool does the filtering; it does not ship 500 rows and hope.

- **A1. `dir_list` returns newest-first, with metadata, bounded.** Sort `st_mtime` descending, new `limit` (default 50, max 500). One flat text line per entry — `name`, size, ISO-ish mtime, `/` suffix for directories — instead of one JSON object per entry. Truncation states the total and how to widen.

  **Use `os.scandir()`, not `iterdir()` + `stat()`, and skip entries whose `stat()` raises.** The current code never calls `stat()`, so it has never hit this; the moment we sort by mtime, every entry needs one. A Downloads folder routinely contains items that raise `OSError` on `stat()` — a partially-written `.crdownload`, a OneDrive placeholder, a broken junction, or a file deleted between the listing and the stat. One unguarded raise fails the whole tool call, and the model responds to a tool error by *retrying* — introducing the exact loop this doc exists to remove. `scandir` caches stat data from the directory walk on Windows, so it is also cheaper than the `iterdir()` path it replaces, which matters now that `nsmallest` becomes a full sort. Report the skipped count in the truncation line rather than hiding it.

  Measured on the **real** Downloads folder from the incident (134 entries):

  | shape | tokens |
  |---|---|
  | current `[{"name":…,"is_dir":…}, …]` | **1,926** |
  | flat, name-only | 921 |
  | **newest 20, flat, with size** | **165** |

  A ~12x payload reduction — and, unlike the current 1,926, it *contains the answer*.

  > `ponytail:` no `sort` parameter. mtime-desc plus the returned mtime lets the model re-sort the rows it got; name-ordered lookup is what `file_search` is for. Add `sort` only if a real request needs alphabetical paging through >`limit` entries.

- **A2. `file_search` gets the same treatment** — currently `islice(p.glob(pattern), 200)` in arbitrary filesystem order. Sort matches by mtime descending, add `limit`, return flat lines.

- **A3. `run_readonly_cmd`'s `dir`/`ls` path routes through the same formatter** instead of stripping `is_dir` and everything else. One code path returns listings, so no future caller can regress to a timestamp-free one. (Root-cause rule: fix it where all callers route through.)

- **A4. Tool descriptions state the ordering**, since descriptions are what steer the model: "newest first", "call again with a larger `limit`". Descriptions are the cheapest steering surface in the system.

### Track D — bound the worst case

*Attacks requests-per-task when Track A is insufficient or the model flails for an unforeseen reason.*

- **D1. Per-turn cumulative input-token ceiling.** `_MAX_TOOL_ROUNDS = 8` caps *rounds*, not *cost* — round cost grows with history, so 8 rounds still bought 150k. Accumulate `prompt_tokens` (Track C) in `_turn_ctx`; when the turn crosses `turn_token_budget` (new setting, default 40,000, sits beside the existing `history_token_budget`), take the **existing** soft-cap exit: one more call with no tool calls permitted, so the user still gets a real answer grounded in the results so far. This is a second trigger for a path that already exists, not a new mechanism.

- **D2. Identical-call suppression, read-only tools only.** Keep a set of `(tool_name, canonical_json(args))` per turn in `_turn_ctx`. A repeat within the same turn returns `"identical call already made this turn — its result is above"` instead of re-running and re-paying. This is the generic form of the incident: a model that cannot make progress repeats itself. `file_read` with different `offset`s has different args and is unaffected.

  **Scope: `dir_list`, `file_search`, `file_read`, `run_readonly_cmd`.** Never writes. A repeated `file_edit` or `dir_organize` with identical args can be legitimate — a retry after a failed first attempt — and, critically, **a Tier-3 denial comes back to the model as a tool result**. The model may reasonably re-request the same action after explaining itself; suppressing that would turn a denial into a silent dead end, which is exactly the dishonesty the gate exists to prevent.

- **D3. Keep `_MAX_TOOL_ROUNDS`** unchanged. It is a cheap sanity net; D1 is the real guard.

### Track B — price and cache discipline

*Attacks price-per-token. Small diffs, large multipliers.*

- **B1. Escalation decays — covers the next turn only.** Implemented as a one-shot in `_route_node`: it reads `escalated` to route (HEAVY this turn), then returns `{"escalated": False}` so the flag clears once consumed. **Note:** resetting it in `run_turn`'s input dict instead (an earlier draft of this line) does not work — LangGraph merges that input into checkpoint state *before* `_route_node` runs, wiping the escalation to zero turns. `respond` re-sets it only on a LIGHT quality failure (B2).
- **B2. Escalate on quality failures, not transport failures.** A `TransportError` / 5xx / 429 says nothing about whether LIGHT was capable. Only escalate on non-transport exceptions.
- **B3. Final round keeps its tools, and sends `tool_choice: "none"`.** Today `tools=None` at the soft cap changes the request body at byte 0 — tool definitions serialize *ahead of* messages — invalidating the whole cached prefix on that request, and silently dropping 1,403 tokens of stable, cheap-to-cache prefix. `tool_choice: "none"` preserves the prefix **and** still forces the tools-free answer the soft cap exists to produce.

  > Adopting the research's raw suggestion ("stop dropping tools") without `tool_choice` would break the soft cap — the model would keep calling tools and never answer.

- **B4. Volatile content last — CONTINGENT, do not ship before Track C measures.** The prompt is assembled `[system] + ctx["memory"] + history` ([graph.py:351](../brain/brain/graph.py)). `ctx["memory"]` is rebuilt every turn from `retrieve` + `episodic_prepend` (≤1,300 tokens, bounded — verified) and sits **before** the growing history, so across turns the shared prefix ends after the ~206-token system message.

  The fix is *not* the one-line move it looks like. `ctx["memory"]` is assembled outside the graph and injected at stream time; `history` is `_prompt_messages(state)`, a projection whose indices we do not own, and during tool rounds its last element is a `tool` message, not the user's. Precise rule: insert the memory message at `len(history) - 1` **only when `history[-1]["role"] == "user"`**, otherwise append — during tool rounds the tail is tool messages and the position no longer affects that turn's caching.

  **Its entire value is contingent on an unverified fact.** If `google/gemma-4-26b-a4b-it` does not cache on its OpenRouter route (Track C's open question), B4 buys nothing on LIGHT — and HEAVY only runs when escalated, which B1/B2 are making rare. Measure `cached_tokens` first, then decide whether this is worth touching at all.
- **B5. Consolidation skips tool results.** [memory.py:120](../brain/brain/memory.py) joins the full unstubbed span. Beliefs are extracted from what the *user and assistant said*, not from `dir_list` payloads — filter `role == "tool"` out of the excerpt. Removes most of the measured 12,656-token surcharge with a one-line predicate, and needs no import from `graph.py` (no circular dependency).

## Ranked interventions

| # | Track | Intervention | Est. reduction | Cost |
|---|---|---|---|---|
| 1 | A1–A4 | Tools answer the question (mtime, newest-first, `limit`, flat text) | **~55–70%** | ~30 lines, 0 deps |
| 2 | C1–C3 | Accumulate real token counts from every call | 0% direct — **prerequisite for proving 1–6** | ~15 lines, 0 deps |
| 3 | B5 | Consolidation skips tool results | ~30% of session total | 1 line |
| 4 | gate | Cap the structure, never byte-slice JSON | ~8–12% standalone; also fixes a correctness bug | ~6 lines |
| 5 | D1–D2 | Token ceiling + identical-call suppression | Bounds worst case | ~12 lines |
| 6 | B1–B3 | Escalation decay, transport-vs-quality trigger, `tool_choice: "none"` | 0% tokens; **~5–10x price** on wrongly-pinned turns | ~5 lines |
| — | B4 | Volatile-content-last prompt ordering | **contingent — unknown until Track C measures `cached_tokens`** | ~5 lines, gated |

Total: **~70 lines, zero new dependencies.** Rows 1–6 are unconditionally correct; B4 is deliberately excluded from the total until measured.

## Provenance

| piece | copied from | adapted how |
|---|---|---|
| mtime-sorted, capped listing; flat paths not JSON | Claude Code `Glob`/`Grep` (mtime-sorted, cap 100, truncation flag) | mtime kept as a visible column since Halo's user asks about recency directly |
| tool does the filtering, fits a token budget | Aider `RepoMap(map_tokens=1024)` — binary-searches content into a fixed budget | far simpler: sort + `limit`, no ranking graph |
| output overflow → reference, not payload | Claude Code Bash (30,000 chars, overflow spills to a file the model can re-read) | Halo already has the better form in `_tool_stub`; extend the idea to listings |
| per-request usage fields | OpenRouter usage accounting (returned free on every response) | read four fields already being paid for and discarded |
| token budget as the loop guard, not step count | `tokencap`; the widely-cited 11-day / $47k agent loop | reuses the existing soft-cap exit, no new control flow |
| volatile-content-last prefix discipline | OpenRouter prompt-caching docs (DeepSeek reads bill at 0.1x, automatic) | one message moves position |

**Evaluated and rejected:** LangChain `ContextEditingMiddleware` / `ClearToolUsesEdit` (Halo's `_tool_stub` is strictly better — a *restorable reference* vs `"[cleared]"`, and triggers far earlier than its `trigger=100_000`); Anthropic Tool Search (85% measured against a **77K-token, 58-tool** baseline; Halo's is 1,403 tokens across 10 tools ≈ 7% of the failure, and a search round-trip per turn would eat most of it — **revisit past ~40 tools**); smolagents/CodeAct (30% fewer *steps*, but needs a Python sandbox — violates the dependency-light Windows-first constraint); LiteLLM, Langfuse, Helicone, `tokencost`, `tiktoken` (all re-derive, with dependencies or a server, a number OpenRouter hands back in the response body — and `tiktoken` has the wrong tokenizer for both configured models); LLMLingua (torch); GPTCache (tool-loop prompts never repeat verbatim); RouteLLM (trained router + torch — `route()`'s 18 rule-based lines are the right call at this scale).

## What this deliberately does not do

- **No new dependency, no framework, no service.** Every intervention is stdlib plus fields OpenRouter already returns.
- **No change to `_tool_stub` or the Layer 1 projection.** Instrumentation proved it works; each tool result goes out verbatim exactly once, then stubbed. Leave it alone.
- **No change to `route()`'s rules.** Only the `escalated` latch and its trigger condition change.
- **No tool-schema reduction.** Real, measured at 49–58% of conversational spend — but the fix costs more than it saves at 10 tools. Revisit past ~40.
- **No new IPC frames.** Track C4 adds two *optional* fields to an existing frame.
- **No sort parameter on `dir_list`** (see the `ponytail:` note in A1).

## Corrections to doc 13

[13-document-ingestion.md](13-document-ingestion.md) is now factually stale in four places and must be corrected alongside this work:

1. *"Status: design — not yet implemented"* — all three layers have code (`extract.py`, `_prompt_messages` stubbing, `tools/docs.py`).
2. *"`file_read` returns up to 64KB (~16k tokens)"* — `_READ_CAP` is now 8KB; the doc describes its own pre-fix state as current.
3. *"No change to `route()` … the escalation mechanism itself stays as the honest fallback"* — **proven false.** It is a one-way latch with no reset path.
4. **Its acceptance test cannot work.** Doc 13 says "compare `spend_update` before/after with a real key." `spend_update` under-reports multi-round turns by ~4x and omits consolidation and `doc_digest` entirely. Track C is a hard prerequisite for validating doc 13's own claims, not just this doc's.

It also never counted `tool_specs()` per-round cost (~half of conversational spend) and does not address metadata-shaped asks at all — Layers 0–2 do nothing for "which of these is newest".

## Implementation order

1. **Track C** (C1–C3, C5). Instrument first. Re-run the user's exact repro and record the real per-round table — this replaces guesswork with a baseline.
2. **Track A** (A1–A4) + the `gate.py` structure-cap fix. Re-run the repro; the delta against step 1 is the headline number. Update `brain/tests/test_files.py` for the new shape.
3. **Track B5** (consolidation excerpt filter) — one line, largest remaining single term.
4. **Track D** (D1–D2) + **Track B** (B1–B3). B4 only if step 1 showed non-zero `cached_tokens`.
5. **C4** (`spend_update` optional fields + three-file contract mirror + `shared/check_contract_sync.py`), and the UI surface for it.
6. `shared/phase2_check.py` gains a **token-ceiling assertion**: a scripted multi-round turn must keep cumulative input tokens under a fixed bound. This is the regression test the repo currently lacks — `phase2_check` drains `spend_update` as *noise* today.
7. Update `mem/Decisions.md`, `mem/Gotchas.md` (the `escalated` latch is a textbook gotcha), and `MigrationLog.md` if `turn_token_budget` needs a settings row.

**Acceptance test is the user's own repro:** search Downloads for a specific PDF, then list the latest downloads. Target: **one tool round each**, cumulative input tokens under 10k for both turns combined, and a `session_usd` that matches the OpenRouter dashboard.

Per the repo's mock rule: any changed tool result shape that a UI affordance renders needs its `brain/brain/mock.py` handler updated in the same change, or the UI hangs waiting for a confirmation that never arrives.
