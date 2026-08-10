"""Runnable self-check for doc_digest (Layer 2, systemdesign/13-document-ingestion.md).

No test framework -- plain asyncio + assert, matching test_files.py. Run with:
    python brain/tests/test_docs.py

Everything runs under HALO_LLM_STUB. The stub's raw reply is prose, which is the
honest-degrade path -- and a degraded digest is deliberately never cached, so map
calls are answered with canned JSON here and MAP_REPLIES_JSON flips that off for
the one check that exercises the degrade path itself.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-docs-")
os.environ["LOCALAPPDATA"] = _TMP  # store's default DB path lands in the temp dir
os.environ["HALO_LLM_STUB"] = "1"

from brain import extract, gate, store  # noqa: E402
from brain.tools import docs  # noqa: E402  -- registers doc_digest
from brain.tools.files import _sha  # noqa: E402

ROOT = Path(tempfile.mkdtemp(prefix="halo-docs-root-")).resolve()
OUT = Path(tempfile.mkdtemp(prefix="halo-docs-out-")).resolve()

store.connect()
store.set_setting("project_roots", [str(ROOT)])

A = ROOT / "alpha.md"
B = ROOT / "beta.md"
A.write_text("# Alpha\n\nRevenue grew 12% to $3.4M.", encoding="utf-8")
B.write_text("# Beta\n\nHeadcount dropped from 40 to 31.", encoding="utf-8")

LLM_CALLS: list[str] = []
_real_llm_text = docs._llm_text
# Map calls answer with real JSON so the cache path can be tested at all: the
# raw stub reply is prose, which is the DEGRADED path, and a degraded digest is
# deliberately never cached. Flip this to exercise that instead.
MAP_REPLIES_JSON = True


async def _counting_llm_text(messages, api_key, ctx=None):
    system = messages[0]["content"]
    LLM_CALLS.append(system[:20])
    if system is docs._MAP_PROMPT or system == docs._MAP_PROMPT:
        if MAP_REPLIES_JSON:
            return json.dumps({
                "path": "ignored -- the caller's path wins",
                "gist": "a well-formed digest", "key_points": ["kp"],
                "entities": ["e"], "numbers": ["1"], "caveats": [], "confidence": 0.9,
            })
    return await _real_llm_text(messages, api_key, ctx)


docs._llm_text = _counting_llm_text

# Same trick for extraction, so "a cache hit does not re-parse" is an assertion
# rather than an inference.
EXTRACTS: list[str] = []
_real_extract_text = extract.extract_text


def _counting_extract_text(path):
    EXTRACTS.append(str(path))
    return _real_extract_text(path)


extract.extract_text = _counting_extract_text


async def broadcast(msg_type: str, payload: dict) -> None:
    pass


def check_tier() -> None:
    inside = [str(A), str(B)]
    assert gate.classify("doc_digest", {"paths": inside}) == 1
    assert gate.classify("doc_digest", {"paths": inside + [str(OUT / "x.md")]}) == 3
    assert gate.classify("doc_digest", {"paths": []}) == 3  # fail closed
    assert gate.classify("doc_digest", {"paths": "not-a-list"}) == 3
    print("[check 1] tier: all-in-roots -> 1; any outside, empty, or malformed paths -> 3: OK")


def check_folder_glob_preparation() -> None:
    folder = ROOT / "reports"
    nested = folder / "archive"
    nested.mkdir(parents=True)
    for path in (folder / "b.pdf", folder / "a.pdf", nested / "c.pdf"):
        path.write_bytes(b"fixture")

    direct = docs._prepare_args({"path": str(folder), "glob": "*.pdf", "focus": "totals"})
    assert direct == {
        "paths": [str(folder / "a.pdf"), str(folder / "b.pdf")],
        "focus": "totals",
    }, direct
    recursive = docs._prepare_args({"path": str(folder), "glob": "**/*.pdf"})
    assert recursive["paths"] == sorted({
        str(folder / "a.pdf"), str(folder / "b.pdf"), str(nested / "c.pdf"),
    }), recursive

    invalid = [
        ({"paths": [str(A)], "path": str(folder)}, "exactly one"),
        ({}, "exactly one"),
        ({"path": str(folder), "glob": str(folder / "*.pdf")}, "relative"),
        ({"path": str(folder), "glob": "../*.pdf"}, ".."),
        ({"path": str(folder), "glob": "*.docx"}, "matched no files"),
    ]
    before = (len(EXTRACTS), len(LLM_CALLS))
    for args, message in invalid:
        try:
            docs._prepare_args(args)
            raise AssertionError(f"invalid arguments were accepted: {args}")
        except ValueError as exc:
            assert message in str(exc), (args, exc)
    cap_dir = ROOT / "cap"
    cap_dir.mkdir()
    for index in range(65):
        (cap_dir / f"{index:02d}.pdf").write_bytes(b"fixture")
    try:
        docs._prepare_args({"path": str(cap_dir), "glob": "*.pdf"})
        raise AssertionError("65 matching files were accepted")
    except ValueError as exc:
        assert "max 64" in str(exc), exc
    assert (len(EXTRACTS), len(LLM_CALLS)) == before
    print("[check 1b] folder/glob preparation is deterministic, confined, and capped before work: OK")


async def check_digest_and_cache() -> None:
    LLM_CALLS.clear()
    out = await docs._doc_digest({"paths": [str(A), str(B)]})
    assert set(out) == {"digest", "docs"}, out
    assert out["digest"], "merged digest empty"
    assert len(out["docs"]) == 2
    for d, p in zip(out["docs"], (A, B)):
        assert d["path"] == str(p), d  # the caller's path, never the model's echo
        assert d["gist"] == "a well-formed digest" and d["confidence"] == 0.9, d
        assert not d.get("degraded"), d
    # Small files, no chunking: 2 map calls + 1 reduce.
    assert len(LLM_CALLS) == 3, LLM_CALLS
    assert store.get_digest(str(A), _sha(A), docs.DIGEST_VERSION) is not None
    assert store.get_digest(str(B), _sha(B), docs.DIGEST_VERSION) is not None
    print("[check 2] digest of 2 md files: merged output + per-doc entries + cache rows: OK")

    LLM_CALLS.clear()
    EXTRACTS.clear()
    out2 = await docs._doc_digest({"paths": [str(A), str(B)], "focus": "numbers"})
    assert len(out2["docs"]) == 2
    assert len(LLM_CALLS) == 1, f"cache hit should skip both map calls: {LLM_CALLS}"
    # Hash-before-extract: the content is hashed and looked up FIRST, so a hit
    # never re-parses. The old order extracted, then hashed, then looked up --
    # two reads either side of an LLM round-trip, so saving the file in between
    # cached the OLD content's digest under the NEW content's sha, permanently.
    assert EXTRACTS == [], f"cache hit re-extracted the file: {EXTRACTS}"
    print("[check 3] cache hit skips both the map call and the extraction entirely: OK")


async def check_degraded_digest_is_not_cached() -> None:
    """A parse failure is a fact about one reply, not about the file. Caching it
    pins the degraded answer under that sha forever -- there is no invalidation
    short of bumping DIGEST_VERSION."""
    global MAP_REPLIES_JSON
    C = ROOT / "gamma.md"
    C.write_text("# Gamma\n\nMargin held at 22%.", encoding="utf-8")
    MAP_REPLIES_JSON = False  # raw stub reply is prose -> the degrade path
    try:
        out = await docs._doc_digest({"paths": [str(C)]})
    finally:
        MAP_REPLIES_JSON = True
    d = out["docs"][0]
    assert d["confidence"] == 0.3 and d["degraded"] is True, d
    assert store.get_digest(str(C), _sha(C), docs.DIGEST_VERSION) is None, (
        "a degraded digest was cached -- it would be served forever"
    )
    # A later good reply for the same unchanged file still caches normally.
    out2 = await docs._doc_digest({"paths": [str(C)]})
    assert not out2["docs"][0].get("degraded"), out2
    assert store.get_digest(str(C), _sha(C), docs.DIGEST_VERSION) is not None
    print("[check 7] a degraded digest is not cached; a later good one is: OK")


async def check_cap_and_bad_file() -> None:
    cap_dir = ROOT / "explicit-cap"
    cap_dir.mkdir(exist_ok=True)
    too_many = []
    for index in range(65):
        path = cap_dir / f"{index:02d}.md"
        path.write_text("fixture", encoding="utf-8")
        too_many.append(str(path))
    res = await gate._execute_tail(
        "doc_digest", {"paths": too_many}, 1, "docs-test", broadcast
    )
    assert res["pending_tool_result"]["status"].startswith("error"), res
    assert "64" in res["messages"][0]["content"], res
    print("[check 4] >64 paths refused with an honest error naming the cap: OK")

    bad = ROOT / "broken.bin"
    bad.write_bytes(b"\xff\xfe\x00\x01binary junk")
    out = await docs._doc_digest({"paths": [str(bad), str(A)]})
    assert len(out["docs"]) == 2, "bad file killed the batch"
    broken = next(item for item in out["docs"] if item["path"] == str(bad))
    assert broken["status"] == "failed", broken
    assert broken["confidence"] == 0.0 and "could not extract" in broken["gist"], broken
    assert any(item["path"] == str(A) for item in out["docs"])  # good file still digested (from cache)
    print("[check 5] broken/binary file degrades honestly without killing the batch: OK")


async def check_gated_run() -> None:
    res = await gate._execute_tail(
        "doc_digest", {"paths": [str(A)]}, 1, "docs-test", broadcast
    )
    assert res["pending_tool_result"]["status"] == "ok", res
    content = res["messages"][0]["content"]
    assert "alpha.md" in content, content
    print("[check 6] doc_digest runs Tier-1 through the real gate; result reaches the tool message: OK")


class _FakeCtx:
    """Minimal stand-in for task_runtime.TaskContext: the only surface _llm_text
    touches is `.cancelled` and `.checkpoint()` (raises TaskStopped once stopped)."""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()

    async def checkpoint(self) -> None:
        from brain.task_runtime import TaskStopped

        if self.cancelled.is_set():
            raise TaskStopped()


async def check_stop_raises_taskstopped() -> None:
    """A stop mid-stream must cancel the LIGHT stream and propagate TaskStopped --
    the docs task's stop semantics, distinct from graph's clean-return one."""
    from brain.task_runtime import TaskStopped

    ctx = _FakeCtx()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.005)  # after the first stub word, before the stream ends
        ctx.cancelled.set()

    messages = [
        {"role": "system", "content": "summarize"},
        {"role": "user", "content": "one two three four five six seven"},
    ]
    canceller = asyncio.create_task(cancel_soon())
    raised = False
    try:
        await _real_llm_text(messages, "stub-key", ctx)
    except TaskStopped:
        raised = True
    await canceller
    assert raised, "a stop during _llm_text did not raise TaskStopped"
    print("[check 8] a stop mid-stream cancels the stream and raises TaskStopped: OK")


async def check_stalled_pdf_worker_is_reaped_on_stop() -> None:
    from brain import extract_worker
    from brain.task_runtime import TaskStopped

    pdf = ROOT / "slow.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% fixture")
    os.environ["HALO_EXTRACT_STUB_DELAY"] = "30"
    ctx = _FakeCtx()
    running = asyncio.create_task(extract_worker.extract_pdf_isolated(pdf, ctx.cancelled))
    await asyncio.sleep(0.15)
    started = time.monotonic()
    ctx.cancelled.set()
    try:
        await running
        raise AssertionError("stopped extraction returned text")
    except TaskStopped:
        pass
    finally:
        os.environ.pop("HALO_EXTRACT_STUB_DELAY", None)
    assert time.monotonic() - started < 2.0
    assert not any(child.name.startswith("halo-extract-") for child in multiprocessing.active_children())
    print("[check 9] stalled PDF extraction stops and reaps its child in under two seconds: OK")


async def check_stalled_pdf_worker_times_out() -> None:
    from brain import extract_worker

    pdf = ROOT / "deadline.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% fixture")
    os.environ["HALO_EXTRACT_STUB_DELAY"] = "30"
    try:
        await extract_worker.extract_pdf_isolated(pdf, asyncio.Event(), timeout=0.1)
        raise AssertionError("stalled extraction did not time out")
    except ValueError as exc:
        assert pdf.name in str(exc) and "0.1" in str(exc), exc
    finally:
        os.environ.pop("HALO_EXTRACT_STUB_DELAY", None)
    assert not any(child.name.startswith("halo-extract-") for child in multiprocessing.active_children())
    print("[check 10] stalled PDF extraction has a named deadline and leaves no child: OK")


async def main() -> None:
    check_tier()
    check_folder_glob_preparation()
    await check_digest_and_cache()
    await check_cap_and_bad_file()
    await check_gated_run()
    await check_degraded_digest_is_not_cached()
    await check_stop_raises_taskstopped()
    await check_stalled_pdf_worker_is_reaped_on_stop()
    await check_stalled_pdf_worker_times_out()
    print("[brain.docs] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
