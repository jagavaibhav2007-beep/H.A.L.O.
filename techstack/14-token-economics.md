# Tech Stack: Token Economics & Tool Answerability

Companion to [systemdesign/14-token-economics.md](../systemdesign/14-token-economics.md).

## Choices

| concern | choice | why |
|---|---|---|
| Per-request token counts | **OpenRouter `usage` fields, already in the response** | `prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens` come back free on every response with no extra call. `llm.py` currently reads `cost` and discards the rest. |
| Token estimation for thresholds | **existing `_tokens()` chars//4** | Already good enough as a safety-net threshold, and a real tokenizer would be *wrong* here — neither `gemma-4` nor `deepseek-v4` uses a `tiktoken` vocabulary. Real counts now come from the provider anyway. |
| File metadata | **`Path.stat()` (stdlib)** | `st_mtime` + `st_size`. Nothing to install. |
| Listing sort/cap | **`sorted(..., key=st_mtime, reverse=True)[:limit]`** | Replaces `heapq.nsmallest` by name. At personal folder scale (~10²–10³ entries) a full sort is free; `nsmallest` was solving a problem that does not exist here, and solving it in the wrong direction. |
| Prompt caching | **OpenRouter automatic caching — nothing to implement** | Both configured models are on the automatic side: DeepSeek reads bill at **0.1x**, no `cache_control` needed. The work is *discipline* (don't bust the prefix), not code. |
| Result truncation | **line-boundary cut on text; entry-count cut on structures** | Never `payload[:N]` on serialized JSON. Truncation must leave parseable output and state what was dropped. |
| Turn budget storage | **existing `store.get_setting`** | `turn_token_budget` sits beside the existing `history_token_budget`. No schema change; `get_setting(key, default)` already handles absent rows. |
| Identical-call suppression | **`set` of `(name, json.dumps(args, sort_keys=True))` in `_turn_ctx`** | Per-turn, in memory, discarded with the turn. Nothing persisted, nothing to migrate. |
| Cost surfacing | **two optional fields on the existing `spend_update` frame** | Optional keeps every existing client valid. Requires the standard three-file mirror + `shared/check_contract_sync.py`. |

## Explicitly not in the stack

- **LiteLLM** — a large dependency tree to abstract over the one provider we use, and to re-derive a cost number OpenRouter already returns.
- **Langfuse (self-hosted)** — Postgres + ClickHouse + Docker to observe a single-user local desktop app. Wildly disproportionate.
- **Helicone** — a hosted proxy would ship the user's prompts (and their file paths) off-box. Directly contradicts the PRD's local-first principle.
- **`tokencost` / `tiktoken`** — estimate what the provider now reports exactly, with the wrong tokenizers for both configured models.
- **LangChain `ContextEditingMiddleware` / `ClearToolUsesEdit`** — a dependency to get a *worse* version of what `_tool_stub` already does: it substitutes `"[cleared]"`, we substitute a restorable reference naming the tool, path, and size; its default `trigger=100_000` would never have fired in this incident.
- **LangMem** — overlaps `memory.py` wholesale for no new capability.
- **LLMLingua** — torch and a compressor model, to degrade prompts in a way that hurts small models most.
- **GPTCache / semantic response caching** — tool-loop prompts never repeat verbatim; the hit rate would be ~0.
- **RouteLLM / LiteLLM Router / `openrouter/auto`** — a trained router (torch), a dependency, or an opaque remote decision, replacing 18 lines of rules that are the right design at this scale. The bug is the `escalated` latch, not the routing policy.
- **Anthropic Tool Search / programmatic tool calling** — measured 85% against a 77K-token, 58-tool baseline. Ours is 1,403 tokens across 10 tools, and a search round-trip per turn would consume most of the saving. **Revisit past ~40 tools.**
- **smolagents / CodeAct** — needs a Python execution sandbox; incompatible with dependency-light Windows-first.

## Install weight

**Zero new dependencies.** Every change is stdlib (`Path.stat`, `sorted`, `json`, `set`) plus response fields already being paid for and thrown away.

## Unverified — measure, do not assume

- Whether OpenRouter's `google/gemma-4-26b-a4b-it` route does implicit caching at all. The provider caching table lists "Google Gemini 2.5", not Gemma. **Track C1 answers this directly** via `cached_tokens` on a second identical-prefix request. If it is zero, LIGHT-model turns get no cache benefit and the volatile-last ordering (B4) matters only for HEAVY.
- Per-model pricing and whether `deepseek-v4-pro` emits reasoning tokens — both feed the real cost of the `escalated` latch, and neither was verifiable offline.
