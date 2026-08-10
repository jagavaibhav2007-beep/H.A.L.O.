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

from brain import extract, extract_worker, gate, llm, secrets_store, store
from brain.task_runtime import TaskStopped
from brain.tools.files import _PATH, _in_roots, _resolve, _sha

_PATHS_CAP = 64
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
    bounds process-wide concurrency. llm.stream_until races each read against the
    stop; the checkpoints keep this path's own semantics -- pause suspends
    mid-stream and a stop raises TaskStopped (ctx is None only for the non-task
    cache-warm path, which never stops)."""
    parts: list[str] = []
    stream = llm.stream_chat(messages, llm.LIGHT, api_key)
    stop = ctx.cancelled if ctx is not None else asyncio.Event()  # unset = never stops
    if ctx is not None:
        await ctx.checkpoint()  # honor a pause/stop already pending before the first read
    async for delta in llm.stream_until(stream, stop):
        parts.append(delta)
        if ctx is not None:
            await ctx.checkpoint()  # pause suspends here; a stop raises TaskStopped
    if ctx is not None and ctx.cancelled.is_set():
        await ctx.checkpoint()  # a stop that raced the read: stream_until returned, now raise
    return "".join(parts)


def _degraded(path: str, gist: str, confidence: float) -> dict:
    # "degraded" is an explicit flag, not a confidence threshold a future prompt
    # tweak could collide with: it is what keeps a parse failure out of a cache
    # that has no invalidation path short of bumping DIGEST_VERSION.
    return {
        "path": path, "gist": gist, "key_points": [], "entities": [],
        "numbers": [], "caveats": [], "confidence": confidence, "degraded": True,
    }


def _failed(path: str, reason: str) -> dict:
    return {
        **_degraded(path, f"could not extract this file: {reason}", 0.0),
        "status": "failed",
    }


def _prepare_args(args: dict) -> dict:
    """Normalize either explicit paths or a confined folder/glob batch."""
    explicit = args.get("paths")
    folder = args.get("path")
    if (explicit is None) == (folder is None):
        raise ValueError("provide exactly one of paths or path")
    focus = args.get("focus")
    if focus is not None and not isinstance(focus, str):
        raise ValueError("focus must be a string")

    if explicit is not None:
        if not isinstance(explicit, list) or not explicit or not all(
            isinstance(raw, str) and raw.strip() for raw in explicit
        ):
            raise ValueError("paths must be a non-empty list of file paths")
        paths = sorted({str(_resolve(raw)) for raw in explicit})
    else:
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError("path must name a folder")
        base = _resolve(folder)
        if not base.is_dir():
            raise ValueError(f"folder does not exist or is not a directory: {folder}")
        pattern = args.get("glob", "*")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("glob must be a non-empty relative pattern")
        pattern_path = Path(pattern)
        if pattern_path.is_absolute():
            raise ValueError("glob must be a relative pattern")
        if ".." in pattern_path.parts:
            raise ValueError("glob must not contain .. traversal")
        try:
            matches = list(base.glob(pattern))
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid glob {pattern!r}: {exc}") from exc
        normalized: set[str] = set()
        for match in matches:
            resolved = match.resolve(strict=False)
            if not resolved.is_relative_to(base):
                raise ValueError(f"glob match escapes the folder: {match}")
            if resolved.is_file():
                normalized.add(str(resolved))
        paths = sorted(normalized)
        if not paths:
            raise ValueError(f"glob {pattern!r} matched no files in {base}")

    if len(paths) > _PATHS_CAP:
        raise ValueError(f"too many paths ({len(paths)}; max {_PATHS_CAP} per batch)")
    prepared = {"paths": paths}
    if focus is not None:
        prepared["focus"] = focus
    return prepared


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
    args = _prepare_args(args)
    paths = args["paths"]
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
            if ctx is not None and p.suffix.lower() == ".pdf":
                text = await extract_worker.extract_pdf_isolated(p, ctx.cancelled)
            else:
                text = await asyncio.to_thread(extract.extract_text, p)
        except TaskStopped:
            raise
        except Exception as exc:
            return _failed(str(p), str(exc))
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
        await ctx.progress(
            len(paths) + 1,
            len(paths) + 1,
            "Synthesizing final response",
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
    folder = args.get("path")
    if isinstance(paths, list) and paths and folder is None:
        return 1 if all(_in_roots(_resolve(p)) for p in paths) else 3
    if isinstance(folder, str) and folder and paths is None:
        return 1 if _in_roots(_resolve(folder)) else 3
    return 3  # fail closed, matching the gate's rule 8


gate.register(
    "doc_digest", _doc_digest, tier=_digest_tier,
    task=True, supports_pause=True,
    prepare=_prepare_args,
    title=lambda a: f"Digest {len(a.get('paths') or [])} documents",
    steps_total=lambda a: len(a.get("paths") or []) + 1,
    summary=lambda a: f"I want to digest {len(a.get('paths') or [])} document(s).",
    schema={
        "description": (
            "Read and summarize up to 64 documents in one call: each file is "
            "extracted, digested by a small model, and merged into one compact "
            "answer -- the raw contents never flood the conversation. ALWAYS "
            "prefer this over calling file_read per file when the user asks to "
            "read/summarize/compare several documents. Results are cached per "
            "unchanged file, so repeat calls are cheap. Use path plus an optional "
            "glob for a folder; glob defaults to direct children and recursion "
            "requires **/."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "description": "Explicit files to digest (max 64).",
                    "items": _PATH,
                },
                "path": {
                    **_PATH,
                    "description": "Folder whose matching files should be digested.",
                },
                "glob": {
                    "type": "string",
                    "description": "Relative file pattern. Defaults to *; use **/ for recursion.",
                },
                "focus": {
                    "type": "string",
                    "description": "Optional question or angle the merged digest should focus on.",
                },
            },
            "oneOf": [{"required": ["paths"]}, {"required": ["path"]}],
        },
    },
)
