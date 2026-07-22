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


def accumulate_tool_call_deltas(acc: dict[int, dict], deltas: list[dict]) -> None:
    """Fold one SSE chunk's `delta.tool_calls` into the per-index accumulator.

    Tool calls arrive FRAGMENTED: the first chunk for an index carries `id` and
    `function.name`, every later chunk carries only a slice of
    `function.arguments`. Parsing any single chunk yields `{}` -- the arguments
    string is only valid JSON once all fragments are concatenated, which is why
    finish_tool_calls() is the only place json.loads happens.
    """
    for d in deltas or ():
        slot = acc.setdefault(d.get("index", 0), {"id": None, "name": None, "args": ""})
        if d.get("id"):
            slot["id"] = d["id"]
        fn = d.get("function") or {}
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["args"] += fn["arguments"]


def finish_tool_calls(acc: dict[int, dict]) -> list[dict]:
    """Close the accumulator once the stream ends. Each entry keeps the raw
    OpenAI shape (`id`/`type`/`function`, replayed verbatim into the assistant
    message) plus the parsed `args` and an `error` the caller can report
    honestly -- a model that emits malformed JSON must not kill the turn."""
    calls = []
    for index in sorted(acc):
        slot = acc[index]
        raw = slot["args"] or "{}"
        error = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            parsed, error = {}, f"I couldn't read the arguments the model sent ({exc})."
        if not isinstance(parsed, dict):
            parsed, error = {}, "the model's arguments weren't a JSON object."
        calls.append({
            "id": slot["id"] or f"call_{index}",
            "type": "function",
            "function": {"name": slot["name"] or "", "arguments": raw},
            "args": parsed,
            "error": error,
        })
    return calls


async def _stream_once(messages, model, api_key, usage_out, tools=None, tool_acc=None) -> AsyncIterator[str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "usage": {"include": True},
    }
    if tools:
        # Must ride on EVERY request of the loop, the follow-up after tool
        # results included -- omitting them there makes the model forget it can
        # call anything and answer from thin air.
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if tool_acc is not None:
        tool_acc.clear()  # a retry re-streams from scratch; never double-count
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
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    if tool_acc is not None:
                        accumulate_tool_call_deltas(tool_acc, delta.get("tool_calls"))
                    if delta.get("content"):
                        yield delta["content"]


def _stub_tool_calls(messages: list[dict]) -> list[dict] | None:
    """Offline tool-calling seam. In the last user message:
      "CALL_TOOL <name> <json>"      -> call it once, then answer from the result
      "CALL_TOOL_LOOP <name> <json>" -> call it every round (proves the round cap)
    Multiple space-separated sentinels in one message become multiple calls in
    one round (the parallel-call path). Emitted in the SAME fragmented-and-
    reassembled shape a real provider produces, so the loop can't accidentally
    depend on stub-only tidiness.
    """
    last_user, last_role = "", (messages[-1].get("role") if messages else "")
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content") or ""
            break
    marker = "CALL_TOOL_LOOP " if "CALL_TOOL_LOOP " in last_user else "CALL_TOOL "
    if marker not in last_user:
        return None
    if marker == "CALL_TOOL " and last_role == "tool":
        return None  # already ran it: answer from the result instead of re-calling
    acc: dict[int, dict] = {}
    for index, part in enumerate(last_user.split(marker)[1:]):
        name, _, rest = part.partition(" ")
        raw = rest.strip() or "{}"
        # Deliberately fragmented: id+name first, arguments in 8-char slices.
        accumulate_tool_call_deltas(
            acc, [{"index": index, "id": f"call_{index}", "function": {"name": name.strip()}}]
        )
        for i in range(0, len(raw), 8):
            accumulate_tool_call_deltas(acc, [{"index": index, "function": {"arguments": raw[i:i + 8]}}])
    return finish_tool_calls(acc)


async def stream_chat(
    messages: list[dict],
    model: str,
    api_key: str,
    usage_out: dict | None = None,
    tools: list[dict] | None = None,
    tool_calls_out: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Yields TEXT deltas only. Any tool calls the model made are appended to
    `tool_calls_out` once the stream finishes (same out-param idiom as
    usage_out) -- the generator's yield type stays str so `token` frames are
    unaffected."""
    # ponytail: stub seam -- offline/deterministic path for tests and phase2_check,
    # avoids any real network call or paid usage.
    if os.environ.get("HALO_LLM_STUB"):
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        if usage_out is not None:
            usage_out["cost"] = 0.0
        if tools is not None and tool_calls_out is not None:
            calls = _stub_tool_calls(messages)
            if calls:
                tool_calls_out.extend(calls)
                return  # tool-call round: no visible text, same as a real provider
        reply = f"stub reply from {model}: {last_user}"
        for word in reply.split(" "):
            yield word
            await asyncio.sleep(0.01)
        return

    acc: dict[int, dict] = {}
    yielded = False
    try:
        async for delta in _stream_once(messages, model, api_key, usage_out, tools, acc):
            yielded = True
            yield delta
    except (httpx.TransportError, httpx.HTTPStatusError) as e:
        is_5xx = isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500
        # Retry only if the client saw nothing yet -- re-streaming after partial
        # yield would duplicate text (plan: "partial tokens already sent stay
        # sent, turn closes with error").
        if not yielded and (isinstance(e, httpx.TransportError) or is_5xx):
            await asyncio.sleep(random.uniform(0.5, 1.5))
            async for delta in _stream_once(messages, model, api_key, usage_out, tools, acc):
                yield delta
        else:
            raise
    if tool_calls_out is not None and acc:
        tool_calls_out.extend(finish_tool_calls(acc))


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
