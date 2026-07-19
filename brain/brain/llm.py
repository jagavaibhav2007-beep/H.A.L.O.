"""OpenRouter streaming client + rule-based model router (Phase 2 Step 3).

No LangChain, no classes -- raw httpx against the chat/completions API.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from typing import AsyncIterator

import httpx

LIGHT = "google/gemma-4-26b-a4b-it"
HEAVY = "deepseek/deepseek-v4-pro"

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODELS_URL = "https://openrouter.ai/api/v1/models"
_TIMEOUT = httpx.Timeout(connect=10, read=120, write=10, pool=10)
_PLAN_VERBS = ("plan", "organize", "refactor", "design", "migrate")


def route(text: str, escalated: bool = False) -> str:
    """Pure rule-based light/heavy chooser (D5) -- no LLM call to decide."""
    if escalated:
        return HEAVY
    if "```" in text or "Traceback (most recent call last)" in text:
        return HEAVY
    lower = text.lower()
    if any(ask in lower for ask in ("think hard", "think deeply", "plan carefully")):
        return HEAVY
    if len(text) > 2000:
        return HEAVY
    # multi-step planning shape: >=3 lines starting with "N." or "- ", plus a planning verb
    numbered_or_bulleted = sum(
        1
        for line in text.splitlines()
        if (line.lstrip()[:1].isdigit() and line.lstrip()[1:2] == ".")
        or line.lstrip().startswith("- ")
    )
    if numbered_or_bulleted >= 3 and any(verb in lower for verb in _PLAN_VERBS):
        return HEAVY
    return LIGHT


async def _sse_lines(response: httpx.Response) -> AsyncIterator[str]:
    async for line in response.aiter_lines():
        if not line or line.startswith(":"):
            continue
        if line.startswith("data: "):
            yield line[len("data: "):]


async def _stream_once(messages, model, api_key, usage_out) -> AsyncIterator[str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "usage": {"include": True},
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream("POST", _API_URL, headers=headers, json=body) as resp:
            if resp.status_code == 401:
                raise RuntimeError("openrouter key rejected — check Settings")
            if resp.status_code >= 400:
                text = await resp.aread()
                snippet = text.decode("utf-8", "replace")[:200]
                resp_ = httpx.Response(resp.status_code, request=resp.request, content=text)
                raise httpx.HTTPStatusError(
                    f"openrouter {resp.status_code}: {snippet}", request=resp.request, response=resp_
                )
            async for data in _sse_lines(resp):
                if data.strip() == "[DONE]":
                    continue
                chunk = json.loads(data)
                usage = chunk.get("usage")
                if usage and usage_out is not None:
                    usage_out["cost"] = float(usage.get("cost", 0.0) or 0.0)
                    usage_out["prompt_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
                    usage_out["completion_tokens"] = int(usage.get("completion_tokens", 0) or 0)
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {}).get("content")
                    if delta:
                        yield delta


async def stream_chat(
    messages: list[dict],
    model: str,
    api_key: str,
    usage_out: dict | None = None,
) -> AsyncIterator[str]:
    # ponytail: stub seam -- offline/deterministic path for tests and phase2_check,
    # avoids any real network call or paid usage.
    if os.environ.get("HALO_LLM_STUB"):
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        reply = f"stub reply from {model}: {last_user}"
        for word in reply.split(" "):
            yield word
            await asyncio.sleep(0.01)
        if usage_out is not None:
            usage_out.update({"cost": 0.0, "prompt_tokens": 1, "completion_tokens": 1})
        return

    yielded = False
    try:
        async for delta in _stream_once(messages, model, api_key, usage_out):
            yielded = True
            yield delta
    except (httpx.TransportError, httpx.HTTPStatusError) as e:
        is_5xx = isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500
        # Retry only if the client saw nothing yet -- re-streaming after partial
        # yield would duplicate text (plan: "partial tokens already sent stay
        # sent, turn closes with error").
        if not yielded and (isinstance(e, httpx.TransportError) or is_5xx):
            await asyncio.sleep(random.uniform(0.5, 1.5))
            async for delta in _stream_once(messages, model, api_key, usage_out):
                yield delta
        else:
            raise


async def validate_key(api_key: str) -> bool:
    if os.environ.get("HALO_LLM_STUB"):
        return True
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(_MODELS_URL, headers=headers)
    if resp.status_code == 200:
        return True
    if resp.status_code in (401, 403):
        return False
    resp.raise_for_status()
    return False
