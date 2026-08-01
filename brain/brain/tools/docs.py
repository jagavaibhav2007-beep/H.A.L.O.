"""Lane-1 doc_digest tool (Layer 2 of systemdesign/13-document-ingestion.md).

Importing this module registers doc_digest into gate.TOOLS (same idiom as
brain.tools.files). Map-reduce digestion on the LIGHT model: extract each file
deterministically (Layer 0), one small LLM call per doc in parallel producing
a fixed JSON digest, one reduce call merging them -- the raw text never enters
chat history. Per-doc digests are cached in SQLite keyed by
(path, sha256, digest_version) so repeated asks over unchanged files cost ~0.
"""

from __future__ import annotations

import asyncio
import json
import os

from pathlib import Path

from brain import extract, gate, llm, secrets_store, store
from brain.tools.files import _PATH, _in_roots, _resolve, _schema, _sha

_PATHS_CAP = 16  # ponytail: inline hard cap until 12-task-runtime B2 lands and
                 # doc_digest becomes task-shaped; then raise/detach it there.
_EXTRACT_CAP = 100 * 1024  # chars of extracted text per file (~25k tokens)
_CHUNK_CHARS = 12 * 1024   # ~3k tokens at chars//4: bigger docs chunk + mini-reduce
_GIST_CAP = 1000  # honest-degrade path: raw reply trimmed to this as the gist
DIGEST_VERSION = 1  # bump to invalidate every cached digest (prompt changes etc.)

_MAP_PROMPT = (
    "You digest ONE document into STRICT JSON. Reply with ONLY a JSON object, no "
    'prose, no code fences: {"path": string, "gist": string, "key_points": '
    '[string], "entities": [string], "numbers": [string], "caveats": [string], '
    '"confidence": number 0-1}. Keep the whole object under 400 tokens.'
)
_CHUNK_PROMPT = (
    "Summarize this part of a larger document in under 200 words. Keep key "
    "facts, names, numbers, and caveats; drop filler."
)


async def _llm_text(messages: list[dict], api_key: str, ctx=None) -> str:
    """One non-streaming-to-the-user LIGHT call; _LLM_SEM in llm.py already
    bounds process-wide concurrency."""
    parts: list[str] = []
    stream = llm.stream_chat(messages, llm.LIGHT, api_key).__aiter__()
    while True:
        if ctx is not None:
            await ctx.checkpoint()
        next_delta = asyncio.create_task(anext(stream))
        cancel_wait = asyncio.create_task(ctx.cancelled.wait()) if ctx is not None else None
        waiters = {next_delta, *([cancel_wait] if cancel_wait is not None else [])}
        done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if cancel_wait is not None and cancel_wait in done:
            next_delta.cancel()
            await asyncio.gather(next_delta, return_exceptions=True)
            await ctx.checkpoint()  # raises TaskStopped
        if cancel_wait is not None:
            cancel_wait.cancel()
        for pending_task in pending:
            pending_task.cancel()
        try:
            parts.append(next_delta.result())
        except StopAsyncIteration:
            break
    return "".join(parts)


def _degraded(path: str, gist: str, confidence: float) -> dict:
    # "degraded" is an explicit flag, not a confidence threshold a future prompt
    # tweak could collide with: it is what keeps a parse failure out of a cache
    # that has no invalidation path short of bumping DIGEST_VERSION.
    return {
        "path": path, "gist": gist, "key_points": [], "entities": [],
        "numbers": [], "caveats": [], "confidence": confidence, "degraded": True,
    }


def _parse_digest(reply: str, path: str) -> dict:
    """STRICT JSON in, honest degrade out: a model that answers prose (or the
    offline stub) becomes a low-confidence gist -- never a crashed batch."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`\n")
        if text.startswith("json"):
            text = text[4:]
    try:
        digest = json.loads(text)
        if not isinstance(digest, dict):
            raise ValueError("not an object")
    except (ValueError, TypeError):
        return _degraded(path, reply.strip()[:_GIST_CAP], 0.3)
    digest["path"] = path  # the caller's truth, not the model's echo
    for key, default in (
        ("gist", ""), ("key_points", []), ("entities", []),
        ("numbers", []), ("caveats", []), ("confidence", 0.5),
    ):
        digest.setdefault(key, default)
    return digest


async def _digest_one(p: Path, text: str, api_key: str, ctx=None) -> dict:
    if len(text) > _CHUNK_CHARS:
        chunks = [text[i:i + _CHUNK_CHARS] for i in range(0, len(text), _CHUNK_CHARS)]
        summaries = await asyncio.gather(*(
            _llm_text(
                [{"role": "system", "content": _CHUNK_PROMPT},
                 {"role": "user", "content": f"{p.name} (part {i + 1}/{len(chunks)}):\n\n{c}"}],
                api_key, ctx,
            )
            for i, c in enumerate(chunks)
        ))
        text = "\n\n".join(summaries)
    messages = [{"role": "system", "content": _MAP_PROMPT},
                {"role": "user", "content": f"path: {p}\n\n{text}"}]
    reply = await _llm_text(messages, api_key, ctx)
    return _parse_digest(reply, str(p))


async def _doc_digest(args: dict, ctx=None) -> dict:
    paths = args.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list of file paths")
    if len(paths) > _PATHS_CAP:
        raise ValueError(f"too many paths ({len(paths)}; max {_PATHS_CAP} per call)")
    # ponytail: laziest correct key plumbing -- tool fns don't receive turn
    # context, so mirror run_turn's own key fetch (stub shortcut included)
    # instead of threading api_key through gate/graph signatures.
    stubbed = bool(os.environ.get("HALO_LLM_STUB"))
    api_key = "stub-key" if stubbed else await asyncio.to_thread(secrets_store.get_key)
    if not api_key:
        raise ValueError("no OpenRouter API key -- add one in Settings first")

    async def one(raw: str) -> dict:
        if ctx is not None:
            await ctx.checkpoint()
        p = _resolve(raw)
        try:
            # Hash BEFORE extracting, and look up before extracting too. The
            # old order read the file twice either side of an LLM round-trip:
            # save the file in between and the OLD content's digest gets cached
            # under the NEW content's sha -- a permanent hit no invalidation can
            # reach. This also skips re-parsing entirely on a cache hit.
            sha = await asyncio.to_thread(_sha, p)
            cached = await asyncio.to_thread(store.get_digest, str(p), sha, DIGEST_VERSION)
            if cached is not None:
                return cached
            text = await asyncio.to_thread(extract.extract_text, p)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the batch
            return _degraded(str(p), f"could not extract this file: {exc}", 0.0)
        digest = await _digest_one(p, text[:_EXTRACT_CAP], api_key, ctx)
        # A parse failure is a transient fact about one reply, not about the
        # file's content -- caching it would pin the degraded answer forever.
        if not digest.get("degraded"):
            await asyncio.to_thread(store.put_digest, str(p), sha, DIGEST_VERSION, digest)
        return digest

    if ctx is None:
        digests = await asyncio.gather(*(one(raw) for raw in paths))
    else:
        digests = []
        for index, raw in enumerate(paths, start=1):
            digest = await one(raw)
            digests.append(digest)
            await ctx.progress(
                index,
                len(paths) + 1,
                f"Digested {Path(raw).name}",
                checkpoint={"docs": digests},
            )
    focus = args.get("focus")
    reduce_ask = (
        "Merge these per-document JSON digests into one concise markdown answer "
        "(under 500 words), citing which file each point came from."
        + (f" Focus on: {focus}." if focus else "")
    )
    reduce_messages = [
        {"role": "system", "content": reduce_ask},
        {"role": "user", "content": json.dumps(digests, ensure_ascii=False)},
    ]
    merged = await _llm_text(reduce_messages, api_key, ctx)
    # gate._RESULT_CAP (8KB) is the hard ceiling on what reaches chat history;
    # this return is the merged digest + the structured per-doc list.
    return {"digest": merged.strip(), "docs": digests}


def _digest_tier(args: dict) -> int:
    paths = args.get("paths")
    if not isinstance(paths, list) or not paths:
        return 3  # fail closed, matching the gate's rule 8
    return 1 if all(_in_roots(_resolve(p)) for p in paths) else 3


gate.register(
    "doc_digest", _doc_digest, tier=_digest_tier,
    task=True, supports_pause=True,
    title=lambda a: f"Digest {len(a.get('paths') or [])} documents",
    steps_total=lambda a: len(a.get("paths") or []) + 1,
    summary=lambda a: f"I want to digest {len(a.get('paths') or [])} document(s).",
    schema=_schema(
        "Read and summarize up to 16 documents in one call: each file is "
        "extracted, digested by a small model, and merged into one compact "
        "answer -- the raw contents never flood the conversation. ALWAYS "
        "prefer this over calling file_read per file when the user asks to "
        "read/summarize/compare several documents. Results are cached per "
        "unchanged file, so repeat calls are cheap.",
        {
            "paths": {
                "type": "array",
                "description": "Files to digest (max 16 per call).",
                "items": _PATH,
            },
            "focus": {
                "type": "string",
                "description": "Optional question or angle the merged digest should focus on.",
            },
        },
        ["paths"],
    ),
)
