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

# --- A5: 429 Retry-After bounded retry + global outbound semaphore ---
# Below exercises the real HTTP path (via httpx.MockTransport), so the stub
# seam must be off -- HALO_LLM_STUB short-circuits before _stream_once runs.

import json  # noqa: E402
import httpx  # noqa: E402

os.environ.pop("HALO_LLM_STUB", None)


def _sse_body(text: str) -> bytes:
    chunk = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {chunk}\n\ndata: [DONE]\n\n".encode()


async def _run_a5_checks() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, content=b"")
        return httpx.Response(200, content=_sse_body("recovered"))

    llm._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        words = []
        async for delta in llm.stream_chat([{"role": "user", "content": "hi"}], model=LIGHT, api_key="k"):
            words.append(delta)
        assert "".join(words) == "recovered", words
        assert len(calls) == 2, "expected exactly one bounded retry after the 429"
    finally:
        await llm.aclose()

    # Retry-After is honored but capped, and defaults sanely when absent/bad.
    assert llm._retry_after_seconds(httpx.Response(429, headers={"retry-after": "9999"})) == llm._MAX_RETRY_AFTER_S
    assert llm._retry_after_seconds(httpx.Response(429, headers={})) == 1.0
    assert llm._retry_after_seconds(httpx.Response(429, headers={"retry-after": "2"})) == 2.0

    # The module-level semaphore is the one choke point every caller (turns +
    # background memory consolidation) inherits.
    assert isinstance(llm._LLM_SEM, asyncio.Semaphore)
    in_flight = 0
    max_in_flight = 0

    async def one_call() -> None:
        nonlocal in_flight, max_in_flight
        async with llm._LLM_SEM:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1

    await asyncio.gather(*(one_call() for _ in range(10)))
    assert max_in_flight <= 4, f"semaphore did not cap concurrency, saw {max_in_flight}"


asyncio.run(_run_a5_checks())

print("[brain.llm] A5 429/Retry-After + semaphore self-check OK")
