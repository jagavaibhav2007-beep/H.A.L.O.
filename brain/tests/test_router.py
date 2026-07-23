"""Runnable self-check for brain/brain/llm.py (Phase 2 Step 3).

No test framework -- plain asyncio + assert, matching test_server.py style.
Run with:
    python brain/tests/test_router.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain import llm
from brain.llm import HEAVY, LIGHT, route, stream_chat, validate_key

# --- route() pure-function branch coverage ---

assert route("hi there", escalated=True) == HEAVY, "sticky escalation"

assert route("here's a bug:\n```python\nx = 1\n```") == HEAVY, "code fence"

assert route("it crashed:\nTraceback (most recent call last):\n  foo") == HEAVY, "traceback marker"

assert route("please think hard about this") == HEAVY, "explicit ask: think hard"
assert route("think deeply on this one") == HEAVY, "explicit ask: think deeply"
assert route("plan carefully before answering") == HEAVY, "explicit ask: plan carefully"

assert route("x" * 2001) == HEAVY, "length > 2000"
assert route("x" * 2000) == LIGHT, "length exactly 2000 stays light"

_plan_prompt = (
    "please refactor this project:\n"
    "1. inventory the modules\n"
    "2. plan the new structure\n"
    "3. migrate call sites\n"
)
assert route(_plan_prompt) == HEAVY, "numbered multi-step plan shape"

_bulleted_plan = (
    "let's design the new schema:\n"
    "- list tables\n"
    "- design columns\n"
    "- organize indexes\n"
)
assert route(_bulleted_plan) == HEAVY, "bulleted multi-step plan shape"

# numbered list without a planning verb stays light
_plain_numbered = "shopping list:\n1. milk\n2. eggs\n3. bread\n"
assert route(_plain_numbered) == LIGHT, "numbered list without planning verb"

assert route("hey what's up") == LIGHT, "plain short chat"

print("[brain.llm] route() branch coverage OK")

# --- stub-seam async behavior ---

os.environ["HALO_LLM_STUB"] = "1"


async def _run_async_checks() -> None:
    messages = [{"role": "user", "content": "hello world"}]
    usage: dict = {}
    words = []
    async for delta in stream_chat(messages, model=LIGHT, api_key="unused", usage_out=usage):
        words.append(delta)
    reply = " ".join(words)
    assert reply == f"stub reply from {LIGHT}: hello world", reply
    assert usage == {"cost": 0.0}, usage

    ok = await validate_key("unused")
    assert ok is True

    # OpenRouter reports failures that occur after streaming starts in-band:
    # HTTP remains 200 and the terminal SSE chunk carries a top-level error.
    midstream_error = {
        "error": {"code": 429, "message": "Rate limit exceeded"},
        "choices": [{"delta": {"content": ""}, "finish_reason": "error"}],
    }
    try:
        llm._raise_for_stream_error(midstream_error)
        raise AssertionError("mid-stream error chunk was accepted as success")
    except llm.OpenRouterStreamError as exc:
        assert "429" in str(exc) and "Rate limit exceeded" in str(exc), exc

    try:
        llm._raise_for_stream_error({"choices": [{"delta": {}, "finish_reason": "error"}]})
        raise AssertionError("finish_reason=error was accepted as success")
    except llm.OpenRouterStreamError:
        pass

    # The hot chat/tool loop shares one pool and closes it explicitly.
    first = llm._get_client()
    assert llm._get_client() is first, "HTTP client/pool was recreated between requests"
    await llm.aclose()
    assert first.is_closed, "shared HTTP client was not closed"
    assert llm._get_client() is not first, "closed HTTP client was reused"
    await llm.aclose()


asyncio.run(_run_async_checks())

print("[brain.llm] self-check OK")
