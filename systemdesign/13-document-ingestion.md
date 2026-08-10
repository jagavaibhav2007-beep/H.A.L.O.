# System Design: Document Ingestion & Token-Lean File Reading

Status: **implemented.** All three layers have code — Layer 0 [`extract.py`](../brain/brain/extract.py) plus the `file_read` cap, Layer 1 `_prompt_messages` tool-result stubbing in [`graph.py`](../brain/brain/graph.py), and task-shaped Layer 2 [`tools/docs.py`](../brain/brain/tools/docs.py) (`doc_digest`) running through [12-task-runtime](12-task-runtime.md).

## Problem (measured)

Asking Halo to read and summarize 8 markdown files burned ~68k tokens — ~5–6× what a single careful pass needs. Verified causes in the **original** code (all since addressed — see Status above; figures below are the pre-fix baseline):

1. **No extraction layer.** `file_read` ([brain/tools/files.py](../brain/brain/tools/files.py)) returned up to **64KB of raw text (~16k tokens) per file** straight into the chat history (now capped at 8KB — see Layer 0). PDFs weren't parsed at all — raw bytes were decoded `utf-8/replace`, i.e. garbage in, tokens billed (Layer 0's `extract.py` now parses PDF/DOCX/XLSX/HTML).
2. **Quadratic retransmission.** The tool loop ([graph.py](../brain/brain/graph.py) `respond ⇄ gate`) re-sends the **entire message history on every round**. Read 8 files across 8 rounds and file #1's content is transmitted 8 times. This, not the reads themselves, is where most of the 68k went.
3. **Blanket escalation.** Any mid-stream LIGHT failure sets `escalated=True`, so the HEAVY model then re-processes the same bloated history — maximum tokens at maximum price.
4. The history summarizer (`_maybe_summarize`) can't help mid-turn: it keeps the last 6 messages verbatim and never cuts inside a single huge multi-tool turn.

## Design: three layers

Each layer attacks a different term of the cost. They are independent — ship in order, each is useful alone.

### Layer 0 — deterministic extraction + pagination at the tool boundary

*Attacks the per-payload size. No LLM involved; zero marginal cost.*

**New module `brain/brain/extract.py`:** an extension→converter dispatcher (markitdown's architecture, minus its weak pdfminer PDF path — see Provenance):

| format | converter | notes |
|---|---|---|
| `.pdf` | **pypdfium2** (already installed, BSD) | fast C++ text extraction; `pypdf` (installed) as fallback + metadata |
| `.docx` | **mammoth** (new, MIT, pure Python) | headings/lists/tables survive as markdown |
| `.xlsx` | **openpyxl** (new, MIT) → markdown pipe tables | cap rows per sheet |
| `.html` | **markdownify** (new, MIT) | strips tags/boilerplate — largest single token win |
| `.md` `.txt` code | passthrough | |
| anything else | honest error naming the format | never feed decoded binary to a model |

Output is always markdown text. Deterministic, offline, licence-clean (no AGPL — see Rejected).

**`file_read` becomes format-aware and paginated:**
- Routes through `extract.py` first, then applies the cap to the *extracted* text.
- Default cap drops **64KB → 8KB (~2k tokens)**, with new optional `offset`/`limit` args so the model pages instead of losing data. Truncation note names the remainder and how to fetch it (Claude Code's Read-tool pattern).
- `run_readonly_cmd` stdout gets the same treatment: head + tail (errors and totals live at the end), middle elided.

### Layer 1 — evict stale tool results at prompt-projection time

*Attacks the retransmission term — the actual quadratic.*

`_prompt_messages()` in graph.py already projects state → prompt (the summary/`dropped_before` mechanism). Extend it: a `tool` message **older than the current tool round** has its content replaced by a stable stub:

```
[file_read C:\...\notes.md — 6.2KB returned this turn; call file_read again (offset/limit) if you need it back]
```

- **Restorable, not lossy** (Manus's "restorable compression"): the file is still on disk; the path is the reference. No scratch store needed.
- **Pure projection change.** The append-only reducer and the checkpoint keep the full transcript; only the outbound prompt shrinks. No `RemoveMessage` surgery.
- **Invariants:** never break an assistant-`tool_calls` ↔ `tool`-message pair (same rule `_maybe_summarize` already enforces), and keep stub text byte-stable across rounds so provider prompt caching still hits.
- Eviction rule: keep the current round's tool results verbatim; stub everything older. Anthropic measured **84% token reduction** with exactly this mechanism (`clear_tool_uses`).

With Layers 0+1, the 8-file scenario becomes ~8 × 2k sent approximately once each: **~68k → ~18–20k**, all on LIGHT, before Layer 2 even exists.

### Layer 2 — `doc_digest`: map-reduce digestion on the LIGHT model

*Attacks the job shape: "read/summarize N documents" should never run through the chat loop at all.*

New Lane-1 read-only tool `doc_digest(paths | path+glob, focus?)`:

- `paths` accepts an explicit file list. `path` plus optional `glob` expands a
  folder deterministically; the default `*` reads direct children only and
  recursive traversal must be explicit (`**/*.pdf`). Absolute and parent-path
  glob patterns are refused. Resolved files are deduplicated and sorted.
- Admission freezes the exact batch after permission and before persistence or
  submission. The hard cap is 64 files, enforced before extraction and spend.

1. **Extract** each file via Layer 0 (per-file extract cap ~100KB). PDF parsing
   runs in a spawned worker process with a 60-second default deadline. Stop or
   timeout terminates, then kills if needed, and always joins/reaps the worker;
   a parser blocked in native code therefore cannot make Stop cosmetic.
2. **Map:** one LIGHT call **per document**, in parallel (`asyncio.gather`, already bounded by `_LLM_SEM`). A doc over ~3k tokens is chunked and mini-reduced within the doc first. Every call is small enough that a flash-class model cannot choke — which removes the escalation trigger, not just the cost.
3. Each map call returns a **fixed JSON digest**, not prose (schema-shaped digests beat prose — parseable, mergeable, no restating):
   `{path, gist, key_points[], entities[], numbers[], caveats[], confidence}` — ~200–400 tokens per doc.
4. **Reduce:** one call merges the digests (LIGHT by default; `focus` present and reduce large → HEAVY is acceptable, it sees only ~2–3k tokens).
5. A failed file produces a structured `status: "failed"` outcome and does not
   abort healthy siblings. Progress names each completed file and includes a
   final synthesis step. The tool result the conversation sees is the merged
   digest (~1–2k tokens). **The raw 64k never enters chat history.**

**Digest cache:** SQLite table keyed by `(path, sha256, digest_version)` — repeated asks over the same files cost ~0. (Schema change → MigrationLog v3.)

**Task-runtime alignment:** `doc_digest` is task-shaped (seconds-to-minutes),
detaches from the interactive turn, reports per-file progress, and participates
in the origin-turn completion barrier. One request therefore yields one final
assistant conclusion after all selected files are terminal, not one reply per
file.

**Offline test seam:** map/reduce calls route through `llm.stream_chat`, so `HALO_LLM_STUB` already covers them; phase2_check gets a digest section asserting the conversation-visible result stays under a token ceiling while the answer cites content from every file.

**Token math for the original scenario:** extraction 0 (no LLM) · map ~16k input on LIGHT only · reduce + conversation ~2–3k. HEAVY input: ~0 (was ~68k).

## Provenance — which open-source design each piece copies

| piece | copied from | adapted how |
|---|---|---|
| extension→converter dispatcher | microsoft/markitdown (MIT, ~169k★) | same shape, but PDF via pypdfium2 instead of its flat pdfminer path |
| read pagination + hard tool-output caps | Claude Code Read/Bash tools; gptme head+tail truncation | 8KB default, offset/limit, head+tail for command output |
| projection-time tool-result eviction | Anthropic context editing (`clear_tool_uses`); Manus "restorable compression"; LangChain `ContextEditingMiddleware` | done inside `_prompt_messages` — zero reducer/checkpoint changes |
| per-doc map + JSON digest + reduce | LangGraph map-reduce summarization (`Send` fan-out, `token_max`, recursive collapse); Anthropic multi-agent "subagents as intelligent filters" | one flash call per doc; digests are schema-shaped |
| digest cache keyed by content hash | LlamaIndex DocumentSummaryIndex (build-time summaries, query-time routing) | idea only, no library — one SQLite table |
| condenser as backstop | OpenHands `LLMSummarizingCondenser` (~2× cost cut, no SWE-bench regression) | already exists here as `_maybe_summarize`; unchanged |

**Evaluated and rejected:** pymupdf4llm (**AGPL** — drags this MIT-distributed repo into AGPL obligations; Artifex sells the exit), docling (best PDF quality but ~1GB torch install, CPU-slow — revisit as an optional lazily-installed "deep parse" tier), unstructured (system binaries poppler/tesseract/libreoffice — hostile on Windows), LLMLingua (torch/GPU-heavy, and compressed-degraded prompts hurt small models most — wrong tool), CrewAI-style multi-agent frameworks (topology without benefit at one-call-per-doc scale).

## What this deliberately does not do

- **No OCR / scanned PDFs.** Future path exists without new design: pypdfium2 already rasterizes pages → send PNG to an OpenRouter vision model, or the optional docling tier. Until then a scanned PDF returns an honest "no extractable text."
- **No embedding/RAG index by default.** Digests answer most follow-ups; if pointed Q&A over big corpora becomes real, add fastembed chunks (256–512 tokens, top-k 5) into sqlite-vec — the memory system already owns that exact stack. Keep inference outside the SQLite operation lock and use the existing embedder-construction lock so first use cannot block unrelated store operations or race model initialization.
- **No new IPC frames.** Tools are Brain-internal; `doc_digest` is Tier 1 inside roots under the existing gate. Contract untouched.
- **`route()` escalation was a one-way latch — since fixed in [14-token-economics](14-token-economics.md) (Track B).** Layer 2 removes the failure that *caused* escalation; but the escalation mechanism itself had no reset path (once a conversation went HEAVY it stayed HEAVY forever), so the "honest fallback" claimed in earlier drafts of this doc was false. Track B1/B2 make escalation decay after a single turn and fire only on quality failures (not transport/5xx/429). Do not rely on escalation as a standing fallback.

## Implementation order

1. `extract.py` + converters + a plain-assert `test_extract.py` (repo's no-framework idiom). New deps: `mammoth`, `openpyxl`, `markdownify` (~a few MB, pure Python).
2. `file_read` format-aware + `offset`/`limit` + 8KB cap; update its schema description (it steers the model); trim `run_readonly_cmd` output to head+tail.
3. Layer 1 eviction in `_prompt_messages` + a phase2_check assertion that prompt size stays bounded across a multi-read turn (reuse the `_tokens` estimator).
4. `doc_digest` + digest cache table (MigrationLog v3) + phase2_check digest section.
5. Update mem/ and VERIFY.md; re-run the original 8-file scenario with a real key and compare `spend_update` before/after — the acceptance test is the user's own repro. **Prerequisite:** [14-token-economics](14-token-economics.md) Track C (usage accounting) must be in place first — before it, `spend_update` under-reported multi-round turns by ~4x and omitted background consolidation and `doc_digest` spend entirely, so a before/after on it would have measured the meter, not the fix.

Per the repo's mock rule: `doc_digest` needs a `mock.py` handler before any UI surface exercises it, or the UI hangs waiting for a confirmation that never comes.
