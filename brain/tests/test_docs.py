"""Runnable self-check for doc_digest (Layer 2, systemdesign/13-document-ingestion.md).

No test framework -- plain asyncio + assert, matching test_files.py. Run with:
    python brain/tests/test_docs.py

Everything runs under HALO_LLM_STUB: map JSON parsing fails on the stub reply,
so the honest-degrade path produces deterministic per-doc gists.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-docs-")
os.environ["LOCALAPPDATA"] = _TMP  # store's default DB path lands in the temp dir
os.environ["HALO_LLM_STUB"] = "1"

from brain import gate, store  # noqa: E402
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


async def _counting_llm_text(messages, api_key):
    LLM_CALLS.append(messages[0]["content"][:20])
    return await _real_llm_text(messages, api_key)


docs._llm_text = _counting_llm_text


async def broadcast(msg_type: str, payload: dict) -> None:
    pass


def check_tier() -> None:
    inside = [str(A), str(B)]
    assert gate.classify("doc_digest", {"paths": inside}) == 1
    assert gate.classify("doc_digest", {"paths": inside + [str(OUT / "x.md")]}) == 3
    assert gate.classify("doc_digest", {"paths": []}) == 3  # fail closed
    assert gate.classify("doc_digest", {"paths": "not-a-list"}) == 3
    print("[check 1] tier: all-in-roots -> 1; any outside, empty, or malformed paths -> 3: OK")


async def check_digest_and_cache() -> None:
    LLM_CALLS.clear()
    out = await docs._doc_digest({"paths": [str(A), str(B)]})
    assert set(out) == {"digest", "docs"}, out
    assert out["digest"], "merged digest empty"
    assert len(out["docs"]) == 2
    for d, p in zip(out["docs"], (A, B)):
        assert d["path"] == str(p), d
        # Stub reply is not JSON -> honest degrade: raw reply as gist, 0.3.
        assert d["gist"].startswith("stub") and d["confidence"] == 0.3, d
    # Small files, no chunking: 2 map calls + 1 reduce.
    assert len(LLM_CALLS) == 3, LLM_CALLS
    assert store.get_digest(str(A), _sha(A), docs.DIGEST_VERSION) is not None
    assert store.get_digest(str(B), _sha(B), docs.DIGEST_VERSION) is not None
    print("[check 2] digest of 2 md files: merged output + per-doc degrade entries + cache rows: OK")

    LLM_CALLS.clear()
    out2 = await docs._doc_digest({"paths": [str(A), str(B)], "focus": "numbers"})
    assert len(out2["docs"]) == 2
    assert len(LLM_CALLS) == 1, f"cache hit should skip both map calls: {LLM_CALLS}"
    print("[check 3] second call hits the cache: only the reduce LLM call runs: OK")


async def check_cap_and_bad_file() -> None:
    too_many = [str(A)] * 17
    res = await gate.gated_execute(
        "doc_digest", {"paths": too_many}, conversation_id="docs-test", task_id=None, broadcast=broadcast
    )
    assert res["pending_tool_result"]["status"].startswith("error"), res
    assert "16" in res["messages"][0]["content"], res
    print("[check 4] >16 paths refused with an honest error naming the cap: OK")

    bad = ROOT / "broken.bin"
    bad.write_bytes(b"\xff\xfe\x00\x01binary junk")
    out = await docs._doc_digest({"paths": [str(bad), str(A)]})
    assert len(out["docs"]) == 2, "bad file killed the batch"
    broken = out["docs"][0]
    assert broken["confidence"] == 0.0 and "could not extract" in broken["gist"], broken
    assert out["docs"][1]["path"] == str(A)  # good file still digested (from cache)
    print("[check 5] broken/binary file degrades honestly without killing the batch: OK")


async def check_gated_run() -> None:
    res = await gate.gated_execute(
        "doc_digest", {"paths": [str(A)]}, conversation_id="docs-test", task_id=None, broadcast=broadcast
    )
    assert res["pending_tool_result"]["status"] == "ok", res
    content = res["messages"][0]["content"]
    assert "alpha.md" in content, content
    print("[check 6] doc_digest runs Tier-1 through the real gate; result reaches the tool message: OK")


async def main() -> None:
    check_tier()
    await check_digest_and_cache()
    await check_cap_and_bad_file()
    await check_gated_run()
    print("[brain.docs] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
