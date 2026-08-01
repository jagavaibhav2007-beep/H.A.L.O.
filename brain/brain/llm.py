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

from brain import store

LIGHT = "google/gemma-4-26b-a4b-it"
HEAVY = "deepseek/deepseek-v4-pro"

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODELS_URL = "https://openrouter.ai/api/v1/models"
_TIMEOUT = httpx.Timeout(connect=10, read=120, write=10, pool=10)
_PLAN_VERBS = ("plan", "organize", "refactor", "design", "migrate")
_client: httpx.AsyncClient | None = None

# A5: one process-wide cap on outbound LLM calls -- turns AND background memory
# consolidation (extract/decide/summary) route through _stream_once, so this is
# the single choke point stopping consolidation from stampeding the provider
# concurrently with live turns. ponytail: fixed global cap; per-tier/per-key
# limits if provider quotas ever need to differ.
_LLM_SEM = asyncio.Semaphore(4)

# A5: cap on honoring a 429's Retry-After -- a provider asking for longer than
# this just fails the turn honestly instead of hanging it.
_MAX_RETRY_AFTER_S = 10.0


def _retry_after_seconds(resp: httpx.Response) -> float:
    raw = resp.headers.get("retry-after")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = 1.0
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_S)


class OpenRouterStreamError(RuntimeError):
    """An HTTP-200 OpenRouter stream terminated with an in-band error."""


def is_transport_error(exc: BaseException) -> bool:
    """A network / 5xx / 429 failure says nothing about whether LIGHT was
    capable of the task, so callers must NOT escalate the model on it
    (systemdesign/14 B2). Kept here so graph.py stays transport-agnostic and
    the httpx types live in one module. A mid-stream OpenRouterStreamError is
    deliberately NOT counted -- a generation that failed mid-flight may be a
    quality signal, and escalation now decays after one turn (B1) anyway."""
    return isinstance(exc, (httpx.TransportError, httpx.HTTPStatusError))


# Every outbound LLM call in the process funnels through _stream_once (the same
# property the semaphore relies on), so accounting lives THERE rather than in
# each caller. Threading a usage out-param through every call site is what let
# _maybe_summarize, docs._llm_text and memory's three calls each go unbilled --
# a caller can no longer make a call invisible by forgetting an argument.
_USAGE_FIELDS = ("cost", "prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens")
_session: dict[str, float] = dict.fromkeys(_USAGE_FIELDS, 0)


def session_totals() -> dict[str, float]:
    """Process-wide usage since start; resets on Brain restart by design."""
    return dict(_session)


def _record_usage(usage: dict, usage_out: dict | None) -> float:
    """Fold one response's usage into the session totals and (if given) the
    caller's out-param. ACCUMULATES -- a multi-round turn reuses one usage_out
    dict, and assigning there is what made `_bill_and_extract` bill only the
    final round (measured 3.95x undercount on a 6-round turn). A retried round
    genuinely IS billed twice by the provider, so counting both is correct.

    Pure bookkeeping -- returns the cost and writes no DB, so a retry's partial
    accounting can't land from inside this function."""
    details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    values = {
        "cost": float(usage.get("cost") or 0.0),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cached_tokens": int(details.get("cached_tokens") or 0),
        "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
    }
    for key, value in values.items():
        _session[key] += value
        if usage_out is not None:
            usage_out[key] = usage_out.get(key, 0) + value
    return values["cost"]


def _get_client() -> httpx.AsyncClient:
    """One process-wide pool for the hot chat/tool loop.

    Construction contains no await, so concurrent tasks on the one event loop
    cannot interleave between the check and assignment.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


async def aclose() -> None:
    """Close the shared HTTP pool during Brain/test shutdown."""
    global _client
    client, _client = _client, None
    if client is not None and not client.is_closed:
        await client.aclose()


def _raise_for_stream_error(chunk: dict) -> None:
    """OpenRouter cannot change HTTP status after streaming has started, so
    provider failures arrive as a terminal HTTP-200 SSE chunk. Treat both the
    documented top-level error and finish_reason=error as failed generations.
    """
    error = chunk.get("error")
    choices = chunk.get("choices") or []
    finish_error = any(choice.get("finish_reason") == "error" for choice in choices)
    if not error and not finish_error:
        return
    if isinstance(error, dict):
        code = error.get("code", "unknown")
        message = error.get("message") or "generation failed"
        raise OpenRouterStreamError(f"openrouter stream error {code}: {message}")
    raise OpenRouterStreamError("openrouter stream ended with finish_reason=error")


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


async def _stream_once(messages, model, api_key, usage_out, tools=None, tool_acc=None, tool_choice="auto") -> AsyncIterator[str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    # `usage: {include: true}` was the deprecated spelling; stream_options is
    # the current one and already returns prompt/completion/cached/reasoning
    # token counts plus cost on the final chunk, at no extra charge.
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        # Must ride on EVERY request of the loop, the follow-up after tool
        # results included -- omitting them there makes the model forget it can
        # call anything and answer from thin air. At the soft cap the caller
        # passes tool_choice="none": tools STILL serialize (ahead of messages),
        # so the cached prefix survives, while "none" forces the tools-free
        # final answer (systemdesign/14 B3). Dropping tools entirely there
        # invalidated the whole cached prefix on that request.
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    if tool_acc is not None:
        tool_acc.clear()  # a retry re-streams from scratch; never double-count
    client = _get_client()
    # A5: every outbound LLM call (turns + background memory consolidation)
    # funnels through here, so this is the one place that needs the semaphore.
    async with _LLM_SEM, client.stream("POST", _API_URL, headers=headers, json=body) as resp:
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
            if usage:
                # Unconditional: no usage_out is not a reason to lose the call.
                cost = _record_usage(usage, usage_out)
                if cost:
                    # ponytail: bills inline, so this one-row upsert runs while
                    # still holding an _LLM_SEM slot and an open HTTP stream. The
                    # usage chunk is the LAST chunk, so the window is tiny; if
                    # store contention ever shows up (see the Phase 3 readiness
                    # audit's embedding-lock finding), accumulate into a pending
                    # total and flush it in stream_chat after the stream closes.
                    await asyncio.to_thread(store.add_spend, cost)
            _raise_for_stream_error(chunk)
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
    tool_choice: str = "auto",
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
        # Route the stub through the same recorder: assigning cost=0.0 here would
        # WIPE the accumulation from earlier rounds of the same turn now that
        # usage_out is additive, and callers rely on the key being present.
        _record_usage({"cost": 0.0}, usage_out)
        if tools is not None and tool_calls_out is not None and tool_choice != "none":
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
        async for delta in _stream_once(messages, model, api_key, usage_out, tools, acc, tool_choice):
            yielded = True
            yield delta
    except (httpx.TransportError, httpx.HTTPStatusError) as e:
        is_5xx = isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500
        is_429 = isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
        # Retry only if the client saw nothing yet -- re-streaming after partial
        # yield would duplicate text (plan: "partial tokens already sent stay
        # sent, turn closes with error").
        if not yielded and (isinstance(e, httpx.TransportError) or is_5xx):
            await asyncio.sleep(random.uniform(0.5, 1.5))
            async for delta in _stream_once(messages, model, api_key, usage_out, tools, acc, tool_choice):
                yield delta
        elif not yielded and is_429:
            # A5: one bounded retry honoring Retry-After (capped) instead of
            # failing the turn outright on a rate limit.
            await asyncio.sleep(_retry_after_seconds(e.response))
            async for delta in _stream_once(messages, model, api_key, usage_out, tools, acc, tool_choice):
                yield delta
        else:
            raise
    if tool_calls_out is not None and acc:
        tool_calls_out.extend(finish_tool_calls(acc))


async def stream_until(stream, stop: asyncio.Event):
    """Yield from an async stream while racing every blocked read against `stop`.

    Cancelling the pending anext() unwinds _stream_once's response context,
    closing the HTTP stream instead of waiting for its 120-second read timeout.
    Callers own what "stopped" means: graph's turn watches ctx["stop"] and stops
    yielding; docs' task passes ctx.cancelled and re-raises TaskStopped after.
    """
    iterator = stream.__aiter__()
    stop_task = asyncio.create_task(stop.wait())
    try:
        while True:
            next_task = asyncio.create_task(anext(iterator))
            done, _ = await asyncio.wait({next_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if stop_task in done:
                next_task.cancel()
                await asyncio.gather(next_task, return_exceptions=True)
                return
            try:
                yield next_task.result()
            except StopAsyncIteration:
                return
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


async def validate_key(api_key: str) -> bool:
    if os.environ.get("HALO_LLM_STUB"):
        return True
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = await _get_client().get(_MODELS_URL, headers=headers)
    if resp.status_code == 200:
        return True
    if resp.status_code in (401, 403):
        return False
    resp.raise_for_status()
    return False
