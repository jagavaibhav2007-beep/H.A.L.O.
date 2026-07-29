"""LangGraph chat spine (Phase 2 Step 4, D1).

The real Brain's user_msg handler: a small StateGraph (route -> respond) with
an AsyncSqliteSaver checkpointer on checkpoints.db (D3), thread_id ==
conversation_id. Perceive is the messages reducer itself (input append), and
gate/execute nodes arrive in Step 5 -- no empty placeholder nodes here.

Error ownership: run_turn emits every error frame for its turn (no_api_key,
turn_failed). server.py calls it bare -- no wrapper, no double emission.
Rule 2: every path out of run_turn ends in exactly one `done` or `error`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from operator import add
from pathlib import Path
from typing import Annotated, TypedDict

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from brain import gate, llm, memory, secrets_store, store
import brain.tools.files  # noqa: F401 -- import registers the Lane-1 file tools into gate.TOOLS
import brain.tools.docs  # noqa: F401 -- import registers doc_digest (Layer 2, systemdesign/13)

logger = logging.getLogger("brain.graph")

_REDIRECT_NOTE = "(the user stopped your previous answer midway; adjust course) "

_MAX_TOOL_ROUNDS = 8

# Sent at stream time (never checkpointed), so it can change without
# rewriting history. Without this the model has no idea the app gates its
# actions, so it "helpfully" asks for permission in prose and never calls the
# tool -- which silently bypasses the entire approval UI. Observed live: asked
# to delete a file, it replied "please confirm" and raised no approval card.
_SYSTEM_PROMPT = (
    "You are Halo, a desktop companion running locally on the user's Windows machine. "
    "You have real tools that act on their real files.\n\n"
    "Use your tools instead of describing what you would do. When a request needs a file "
    "read, listed, searched, created, edited, moved, organized, or deleted, call the "
    "matching tool.\n\n"
    "Do NOT ask the user for permission in your reply. Halo shows its own approval card "
    "for anything risky or destructive, and the user approves or denies it there. If an "
    "action needs consent, calling the tool IS how you ask -- writing 'please confirm' "
    "instead just stalls, because it never reaches the approval step. A denial comes back "
    "to you as a tool result; accept it and move on.\n\n"
    "Never claim you lack permission or access before actually trying the tool. If a tool "
    "returns an error, report what it actually said."
)


def _roots_note() -> str:
    """One sentence naming the folders the file tools can reach, so the model
    stops guessing paths. Built per turn (not baked into _SYSTEM_PROMPT) since
    project_roots is a runtime setting; derived from _roots() so it's correct
    on any machine and never hardcodes a personal path."""
    try:
        roots = ", ".join(str(r) for r in brain.tools.files._roots())
    except Exception:  # noqa: BLE001 - a missing store must not break the turn
        return ""
    return (
        f"\n\nYou can read and act on files under: {roots}. Bare or relative paths "
        "resolve against the user's home folder, so prefer absolute paths."
    ) if roots else ""
# ponytail: two layers, on purpose. _MAX_TOOL_ROUNDS is the SOFT cap -- at it we
# still answer, tools-free. _RECURSION_LIMIT is the hard net LangGraph enforces
# over super-steps (route + respond*(rounds+1) + gate*total_calls), which the
# soft cap alone can't bound because one round may return several calls. Sized
# well clear of 8 single-call rounds (~18 steps); hitting it means something
# pathological and surfaces as a normal turn_failed error.
_RECURSION_LIMIT = 60


class State(TypedDict, total=False):
    messages: Annotated[list[dict], add]  # role/content dicts; reducer appends
    model: str
    escalated: bool  # sticky per-conversation: route() picks HEAVY next turn
    redirected: bool  # sticky: user interrupted the previous answer
    error: str | None  # set by respond on failure; cleared on each turn's input
    # D6 carry fields -- present now, consumed by Step 5's gate. Untouched here.
    pending_tool_result: dict | None
    task_id: str | None
    # Step 10 tool-calling loop. `pending_tool_calls` is a QUEUE: the gate node
    # pops exactly one per visit, so a Tier-3 suspension partway through a
    # multi-call round re-runs only the call it suspended on (an interrupting
    # node commits no state, so anything already executed is never repeated).
    # `tool_rounds` counts respond->gate hops: 0 means the legacy CALL_TOOL
    # sentinel drove this (single-shot, gate -> END), >0 means the model did.
    pending_tool_calls: list[dict] | None
    tool_rounds: int
    # Step 9 history summarization. `messages` has an append-only `add`
    # reducer, so an old span can't be replaced in place; instead the span
    # messages[:dropped_before] is represented by `summary` and simply not
    # sent to the model (the chat doc's no-double-injection rule).
    # ponytail: RemoveMessage-style surgery would shrink the checkpoint too;
    # this keeps the full transcript on disk and only bounds the prompt.
    summary: str | None
    dropped_before: int


_graph = None
_saver_conn: aiosqlite.Connection | None = None
# Session spend/token totals live in llm.session_totals() -- accumulated at the
# one choke point every LLM call passes through, so background consolidation and
# doc_digest can no longer spend invisibly (measured: session_usd was 2.9x under).
_recovery_started = False  # startup consolidation recovery runs once per process
_recovery_task: asyncio.Task | None = None  # retained so the once-per-process task can't be GC'd mid-flight
# conversation_id -> live-turn context while its stream runs. Callables stay
# out of the graph config so the checkpointer never tries to serialize them.
_turn_ctx: dict[str, dict] = {}


def _after_gate(state: State) -> str:
    # "Stop this task" resumes a suspended approval with redirected=True.
    # Cancellation is terminal for this turn: returning to respond would let
    # the model request the same denied tool again and spam approval cards.
    if state.get("redirected"):
        return END
    if state.get("pending_tool_calls"):
        return "gate"  # more calls in this round; drain them one at a time
    return "respond" if state.get("tool_rounds") else END


def _checkpoint_path() -> Path:
    override = os.environ.get("HALO_CHECKPOINT_DB")
    if override:
        return Path(override)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return base / "Halo" / "checkpoints.db"


def _route_node(state: State, config) -> dict:
    text = state["messages"][-1]["content"]
    # ponytail: Step-5 test seam kept alive -- under the LLM stub a message
    # STARTING with "CALL_TOOL <name> <json-args>" runs that tool single-shot
    # (route -> gate -> END, tool_rounds stays 0), which is what the gate/undo
    # suites assert. The same sentinel anywhere ELSE in the text falls through
    # to respond, where the stub emits it as a real model tool call and the
    # full loop runs. Real mode never takes this branch.
    if os.environ.get("HALO_LLM_STUB") and text.startswith("CALL_TOOL "):
        import json as _json

        name, _, rest = text[len("CALL_TOOL "):].partition(" ")
        args = _json.loads(rest) if rest.strip() else {}
        return {"pending_tool_calls": [{"id": "sentinel", "name": name, "args": args, "error": None}]}
    # B1: consume escalation as a ONE-SHOT. The prior turn set escalated=True so
    # this turn routes HEAVY; clearing it here -- AFTER route reads it -- makes
    # escalation cover the next turn only, instead of latching HEAVY forever with
    # no reset path (systemdesign/14 B1). Resetting it in run_turn's input dict
    # would wipe it BEFORE route runs, so escalation would never take effect at
    # all. respond re-sets it only on a LIGHT quality failure (B2).
    return {"model": llm.route(text, state.get("escalated", False)), "escalated": False}


async def _gate_node(state: State, config) -> dict:
    """Runs the HEAD of the pending-tool-call queue through gate.gated_execute
    -- Tier 3 raises GraphInterrupt from interrupt(), which MUST propagate to
    the checkpointer (no try/except here; see gate.py). One call per visit: the
    conditional edge sends us straight back here while the queue is non-empty."""
    cid = config["configurable"]["thread_id"]
    ctx = _turn_ctx[cid]
    queue = list(state.get("pending_tool_calls") or [])
    call = queue.pop(0)
    seen = ctx.setdefault("seen_readonly", set())
    if call.get("error"):
        # Malformed arguments from the model: report it, never guess. The
        # result goes back as a tool message so it can correct itself.
        update: dict = {"pending_tool_result": {"tool": call["name"], "status": "error: bad arguments"}}
        content = f"I couldn't run {call['name']} — {call['error']}"
    elif call["name"] in _READONLY_TOOLS and _call_key(call) in seen:
        # D2: the same read-only call with identical args already ran THIS turn.
        # Re-running re-pays for a result the model already has -- the generic
        # form of the incident (a model that can't progress repeats itself).
        # Read-only only: a repeated write may be a legitimate retry, and a
        # Tier-3 denial comes back as a tool result the model may re-request
        # after explaining itself (systemdesign/14 D2). No gate, no action row --
        # nothing actually ran. ponytail: per-turn set; a Tier-3 suspend/resume
        # builds a fresh ctx and forgets it -- the incident was an uninterrupted
        # loop, so carrying it through checkpointed State isn't worth it.
        update = {"pending_tool_result": {"tool": call["name"], "status": "ok"}}
        content = "identical call already made this turn — its result is above"
    else:
        update = await gate.gated_execute(
            call["name"],
            call["args"],
            conversation_id=cid,
            task_id=state.get("task_id"),
            broadcast=ctx["broadcast"],
        )
        content = (update.get("messages") or [{}])[0].get("content") or ""
        # Only suppress a repeat whose result is still VERBATIM above. A result
        # over _TOOL_STUB_MIN gets Layer-1 stubbed to "call the tool again" once
        # an assistant message follows it, so refusing that re-call with "its
        # result is above" contradicts the stub the model is obeying (D2 vs
        # Layer 1). Below the stub floor the result stays put, so "it's above"
        # is true and the loop guard still holds.
        if call["name"] in _READONLY_TOOLS and len(content) <= _TOOL_STUB_MIN:
            seen.add(_call_key(call))
    if state.get("tool_rounds"):
        # Model-driven: the result must come back as a `tool` message keyed to
        # the call id, or the provider rejects the assistant tool_calls message
        # it answers. The legacy sentinel path keeps gate's plain assistant text.
        update["messages"] = [{"role": "tool", "tool_call_id": call["id"], "content": content}]
    update["pending_tool_calls"] = queue or None
    return update


_KEEP_VERBATIM = 6  # most recent messages never summarized
_SUMMARY_SYSTEM = (
    "Condense this conversation excerpt into a compact third-person summary. "
    "Keep facts, decisions, names, and open threads; drop pleasantries. No preamble."
)


# ponytail: 500-char floor. Below it a tool result ("ok", a short error) is
# cheaper to keep than to stub -- eliding it saves nothing and drops signal.
_TOOL_STUB_MIN = 500


def _tool_stub(content: str, fn: dict) -> str:
    """Deterministic, byte-stable placeholder for an already-answered tool
    result (Layer 1, systemdesign/13): derived ONLY from the tool name, the
    call's path/args, and the original length, so provider prompt caching still
    hits across rounds. Restorable, not lossy -- the file is still on disk, so
    the path is the reference the model re-fetches with offset/limit."""
    name = fn.get("name") or "tool"
    ref = name
    try:
        parsed = json.loads(fn.get("arguments") or "{}")
        ref = parsed.get("path") or name  # raw model string: may be non-dict/invalid
    except (ValueError, TypeError, AttributeError):
        pass
    return (
        f"[{name} result for {ref} ({len(content)} chars) elided — "
        "call the tool again (offset/limit) if you need it]"
    )


def _prompt_messages(state: State) -> list[dict]:
    """The bounded history actually sent to the model: the running summary
    stands in for messages[:dropped_before], and every tool result the model
    has ALREADY answered is replaced by a short restorable stub (Layer 1).

    Staleness: a `tool` message is stale iff an `assistant` message follows it
    -- the model produced a response having seen that result, so re-sending its
    payload every round is the quadratic. Results in the tail past the last
    assistant are not yet answered and stay verbatim. Pure projection: new dicts
    for stubbed entries only; state["messages"], the reducer and the checkpoint
    are untouched, and no message is dropped -- assistant/tool pairing is safe."""
    live = state["messages"][state.get("dropped_before") or 0:]
    last_assistant = max(
        (i for i, m in enumerate(live) if m.get("role") == "assistant"), default=-1
    )
    if last_assistant > 0:
        fns = {
            tc.get("id"): tc.get("function") or {}
            for m in live for tc in (m.get("tool_calls") or ())
        }
        live = [
            {**m, "content": _tool_stub(m["content"], fns.get(m.get("tool_call_id"), {}))}
            if (i < last_assistant and m.get("role") == "tool"
                and len(m.get("content") or "") > _TOOL_STUB_MIN)
            else m
            for i, m in enumerate(live)
        ]
    summary = state.get("summary")
    if not summary:
        return live
    return [{"role": "system", "content": f"Summary of earlier conversation:\n{summary}"}, *live]


def _tokens(messages: list[dict]) -> int:
    # ponytail: chars//4 estimate, no tokenizer dependency -- swap in the real
    # count if the threshold ever needs to be tight rather than a safety net.
    # Count the complete provider payload: assistant tool-call arguments can be
    # much larger than content (often empty) and are sent back on every round.
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"), default=str)
    return len(serialized) // 4


async def _stream_until_stopped(stream, stop: asyncio.Event):
    """Yield from an async stream while racing every blocked read against stop.

    Cancelling the pending anext() unwinds llm._stream_once's response context,
    closing the HTTP stream instead of waiting for its 120-second read timeout.
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


async def _maybe_summarize(state: State, ctx: dict) -> dict:
    """Past the token budget, distill the oldest not-yet-summarized span into
    the running summary and advance `dropped_before`. Returns a state update,
    or {} to keep full history. Failure is never data loss: on any error we
    return {} and the same span is retried next turn."""
    if _tokens(_prompt_messages(state)) <= await asyncio.to_thread(_history_budget):
        return {}
    old_cut = state.get("dropped_before") or 0
    new_cut = len(state["messages"]) - _KEEP_VERBATIM
    # Never cut between an assistant tool_calls message and its `tool` replies
    # -- an orphan tool message is a hard provider error. Walk the cut back
    # onto the assistant that owns them.
    while new_cut > old_cut and state["messages"][new_cut].get("role") == "tool":
        new_cut -= 1
    if new_cut <= old_cut:
        return {}  # nothing older than the verbatim tail; a single huge turn
    span = state["messages"][old_cut:new_cut]
    prior = state.get("summary")
    excerpt = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in span)
    if prior:
        # Fold the previous summary back in -- it stands for everything before
        # old_cut, and overwriting without it would silently drop that history.
        excerpt = f"Summary so far:\n{prior}\n\n{excerpt}"
    try:
        parts: list[str] = []
        stream = llm.stream_chat(
            [{"role": "system", "content": _SUMMARY_SYSTEM}, {"role": "user", "content": excerpt}],
            llm.LIGHT,
            ctx["api_key"],
            ctx["usage"],  # counts toward the turn -- was entirely unbilled
        )
        async for delta in _stream_until_stopped(stream, ctx["stop"]):
            parts.append(delta)
        summary = "".join(parts).strip()
    except GraphInterrupt:
        raise  # C2: never swallow the suspension signal (guard, not a live path)
    except Exception:  # noqa: BLE001 - keep full history this turn, retry next
        logger.exception("history summarization failed; keeping full history")
        return {}
    if not summary:
        return {}
    logger.info("summarized %d messages; history now %d messages + summary", len(span), _KEEP_VERBATIM)
    return {"summary": summary, "dropped_before": new_cut}


def _history_budget() -> int:
    store.connect()
    return int(store.get_setting("history_token_budget", 6000))


def _turn_token_budget() -> int:
    # D1: cumulative-input-token ceiling for ONE turn (sits beside
    # history_token_budget). _MAX_TOOL_ROUNDS caps rounds, not cost; this caps
    # cost (systemdesign/14 D1).
    store.connect()
    return int(store.get_setting("turn_token_budget", 40000))


_READONLY_TOOLS = {"dir_list", "file_search", "file_read", "run_readonly_cmd"}


def _call_key(call: dict) -> str:
    return json.dumps([call["name"], call.get("args") or {}], sort_keys=True, default=str)


async def _respond_node(state: State, config) -> dict:
    cid = config["configurable"]["thread_id"]
    ctx = _turn_ctx[cid]
    update: dict = await _maybe_summarize(state, ctx)
    if ctx["stop"].is_set():
        update["redirected"] = True
        return update
    history = _prompt_messages({**state, **update})
    parts: list[str] = []
    calls: list[dict] = []
    rounds = state.get("tool_rounds") or 0
    # Two soft caps, ONE exit. Both keep the tool specs on the request (a stable,
    # cheap-to-cache prefix) and send tool_choice="none" so the model gives a
    # real final answer grounded in the results so far instead of looping.
    # Dropping tools entirely (the old tools=None) invalidated the cached prefix
    # on that request (systemdesign/14 B3).
    #  - rounds cap (_MAX_TOOL_ROUNDS): a model that isn't converging.
    #  - D1 token ceiling: a round's cost grows with history, so 8 rounds still
    #    bought 150k tokens once. Cap CUMULATIVE input tokens (Track C's real
    #    prompt_tokens, accumulated in ctx["usage"]) at turn_token_budget.
    over_budget = ctx["usage"].get("prompt_tokens", 0) >= await asyncio.to_thread(_turn_token_budget)
    if over_budget and rounds < _MAX_TOOL_ROUNDS:
        logger.info(
            "turn token budget reached for %s (%d input tokens); forcing a tools-free answer",
            cid, ctx["usage"].get("prompt_tokens", 0),
        )
    tools = gate.tool_specs()
    tool_choice = "none" if (rounds >= _MAX_TOOL_ROUNDS or over_budget) else "auto"
    try:
        # Retrieved beliefs prepend at stream time only -- never into graph
        # state, so they're recomputed per turn (no-double-injection rule).
        system = {"role": "system", "content": _SYSTEM_PROMPT + _roots_note()}
        stream = llm.stream_chat(
            [system] + ctx.get("memory", []) + history,
            state["model"],
            ctx["api_key"],
            ctx["usage"],
            tools=tools,
            tool_calls_out=calls,
            tool_choice=tool_choice,
        )
        async for delta in _stream_until_stopped(stream, ctx["stop"]):
            parts.append(delta)
            await ctx["broadcast"]("token", {"text": delta, "conversation_id": cid})
    except GraphInterrupt:
        raise  # C2: interrupt() signals by raising -- never swallow it here
    except Exception as exc:  # noqa: BLE001 - caught here so the state update
        # (escalated flag) still commits to the checkpoint; run_turn turns it
        # into the error frame.
        logger.exception("stream failed for conversation_id=%s", cid)
        update["error"] = str(exc)
        if state.get("model") == llm.LIGHT and not llm.is_transport_error(exc):
            # B2: escalate the NEXT turn to HEAVY only on a quality/parse-style
            # failure -- a transport/5xx/429 says nothing about whether LIGHT was
            # capable, and B1 now decays this after a single turn.
            update["escalated"] = True
    if ctx["stop"].is_set():
        update["redirected"] = True
    if calls and not ctx["stop"].is_set():
        # The assistant message MUST carry its tool_calls verbatim -- it's what
        # the following `tool` messages answer.
        update["messages"] = [{
            "role": "assistant",
            "content": "".join(parts),
            "tool_calls": [{"id": c["id"], "type": c["type"], "function": c["function"]} for c in calls],
        }]
        update["pending_tool_calls"] = [
            {"id": c["id"], "name": c["function"]["name"], "args": c["args"], "error": c["error"]}
            for c in calls
        ]
        update["tool_rounds"] = rounds + 1
    elif parts:
        # ponytail: deltas joined verbatim; the stub yields words without
        # spaces so stub history reads squashed -- cosmetic, tests account for it.
        update["messages"] = [{"role": "assistant", "content": "".join(parts)}]
    return update


async def _ensure_graph():
    global _graph, _saver_conn
    if _graph is not None:
        return _graph
    path = _checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = aiosqlite.connect(str(path))
    # aiosqlite (>=0.21) runs each connection on a non-daemon worker thread;
    # without daemonizing it, any interpreter that finishes with the
    # checkpointer still open (tests, smoke scripts) hangs forever on exit.
    # ponytail: private attr poke -- aiosqlite has no public daemon knob; the
    # thread exists but isn't started until the await below, so this is safe.
    conn._thread.daemon = True
    _saver_conn = await conn
    builder = StateGraph(State)
    builder.add_node("route", _route_node)
    builder.add_node("respond", _respond_node)
    builder.add_node("gate", _gate_node)
    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route",
        lambda s: "gate" if s.get("pending_tool_calls") else "respond",
        {"gate": "gate", "respond": "respond"},
    )
    # START -> route -> respond <=> gate -> END. respond loops out to gate while
    # it keeps asking for tools; gate drains its queue one call per visit and
    # hands control back for the next round (or ends, on the legacy sentinel
    # path where nothing asked for a follow-up answer).
    builder.add_conditional_edges(
        "respond",
        lambda s: "gate" if s.get("pending_tool_calls") else END,
        {"gate": "gate", END: END},
    )
    builder.add_conditional_edges(
        "gate",
        _after_gate,
        {"gate": "gate", "respond": "respond", END: END},
    )
    _graph = builder.compile(checkpointer=AsyncSqliteSaver(_saver_conn))
    global _recovery_started, _recovery_task
    if not _recovery_started:
        _recovery_started = True
        _recovery_task = asyncio.create_task(_recover_consolidation())
    return _graph


async def _recover_consolidation() -> None:
    """Crash-safety net: any conversation left dirty (checkpoint has messages
    past its cursor) is consolidated once on process start -- a crash that
    skipped the graceful flush just consolidates late, never loses the session.

    # ponytail: runs once per process; unverified by the automated gates
    # (phase2_check flushes on its simulated restart, so nothing is dirty
    # afterward). The shared consolidate_span path IS gate-covered. Recovery has
    # no connected client, so it broadcasts to a no-op -- the recovered beliefs
    # surface on the next connect's snapshot."""
    try:
        stubbed = bool(os.environ.get("HALO_LLM_STUB"))
        api_key = "stub-key" if stubbed else await asyncio.to_thread(secrets_store.get_key)
        if not api_key:
            return  # no key, not stubbed: skip silently (can't extract anyway)
        dirty = await asyncio.to_thread(store.list_dirty_conversations)
        if not dirty:
            return
        graph = await _ensure_graph()

        async def _noop(_type, _payload):
            return

        for row in dirty:
            cid = row["conversation_id"]
            snap = await graph.aget_state({"configurable": {"thread_id": cid}})
            messages = snap.values.get("messages", [])
            if messages:
                await memory.consolidate_span(cid, messages, api_key, _noop)
    except Exception:  # noqa: BLE001 - recovery is best-effort, never fatal
        logger.exception("startup consolidation recovery failed")


async def aclose() -> None:
    """Close the checkpointer (tests' simulated restart; process exit doesn't
    need it -- SQLite WAL survives a kill, which is the restart semantics:
    history persists, a dead half-streamed turn is never auto-resumed)."""
    global _graph, _saver_conn
    try:
        # Consolidate any dirty conversations before the saver closes (flush
        # needs nothing from it) so a graceful shutdown doesn't lose a session's
        # worth of un-consolidated memory. Order: flush -> close saver -> llm.
        await memory.flush_all()
    except Exception:  # noqa: BLE001 - shutdown flush must not block teardown
        logger.exception("memory flush on shutdown failed")
    try:
        if _saver_conn is not None:
            await _saver_conn.close()
    finally:
        _graph = None
        _saver_conn = None
        await llm.aclose()


def _month_spend_sync() -> float:
    # llm._stream_once already recorded this call's cost against the month at
    # the choke point -- adding it again here is what double-counted.
    store.connect()
    return store.month_spend()


async def _bill_and_extract(cid: str, result: dict, ctx: dict, api_key: str | None, broadcast) -> None:
    """Shared completed-turn tail (run_turn and resume_turn): fire-and-forget
    memory note_turn when the turn didn't error (it swallows its own failures,
    rule 5, and consolidation runs later off the hot path), then roll up cost and
    broadcast spend. resume owes this as
    much as run_turn: since the tool-calling loop landed, a resumed invoke is no
    longer just `gate -> END` -- it continues `gate -> respond -> ...` and streams
    a real (billable) final answer, so skipping it would undercount spend and skip
    extraction on approved-tool turns. The `api_key and` guard is harmless in
    run_turn (it early-returns without a key)."""
    if api_key and not result.get("error"):
        # v2: no per-turn extraction. Mark the conversation dirty + (re)arm the
        # idle consolidation timer; the actual extract/decide/summarize runs off
        # the hot path. note_turn swallows its own failures (rule 5).
        memory._spawn(memory.note_turn(cid, result.get("messages") or [], api_key, broadcast))
    usage = ctx["usage"]
    if usage:
        # The turn's REAL totals, summed over every round (see llm._record_usage).
        # Logged because this is the only place the per-turn shape is visible
        # until spend_update carries token counts.
        logger.info(
            "turn usage conversation_id=%s prompt=%d cached=%d completion=%d reasoning=%d cost=%.6f",
            cid, usage.get("prompt_tokens", 0), usage.get("cached_tokens", 0),
            usage.get("completion_tokens", 0), usage.get("reasoning_tokens", 0),
            usage.get("cost", 0.0),
        )
        month = await asyncio.to_thread(_month_spend_sync)
        totals = llm.session_totals()
        await broadcast("spend_update", {
            "session_usd": totals["cost"],
            "month_usd": month,
            "session_tokens": int(totals["prompt_tokens"] + totals["completion_tokens"]),
            "last_turn_tokens": int(usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)),
        })


# Every super-step writes a checkpoint carrying the whole serialized `messages`
# channel, so a thread's disk cost is quadratic in its length and nothing ever
# deleted a row (689 checkpoints / 41 threads / 7.1MB after ~2 weeks of light
# use). The NEWEST checkpoint already holds the full cumulative conversation --
# older ones only serve time-travel/replay, which Halo exposes nowhere (no
# aget_state_history/alist caller in the repo). 20 is well clear of the ~3-8
# super-steps one turn writes, so a few turns of parent chain survive for
# LangGraph's own resume path; pruning only ever runs after a turn has ended, so
# no in-flight (Tier-3 suspended) checkpoint is ever a candidate.
_CHECKPOINT_KEEP = 20


async def _prune_checkpoints(cid: str) -> int:
    """Delete all but the newest _CHECKPOINT_KEEP checkpoints of one thread.

    checkpoint_id is a time-ordered UUID and the saver itself selects the live
    checkpoint with `ORDER BY checkpoint_id DESC`, so lexicographic order is
    recency order. Runs on the saver's own lock/connection -- it is shared and
    actively in use. Pruned rows leave a dangling parent_checkpoint_id on the
    oldest survivor, which is fine for aget_state (latest-only)."""
    graph = await _ensure_graph()
    saver = graph.checkpointer
    conn = saver.conn
    async with saver.lock:
        keep_q = (
            "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? "
            "ORDER BY checkpoint_id DESC LIMIT ?"
        )
        # writes first: it references checkpoint_ids the second statement drops.
        for table in ("writes", "checkpoints"):
            cur = await conn.execute(
                f"DELETE FROM {table} WHERE thread_id = ? AND checkpoint_id NOT IN ({keep_q})",
                (cid, cid, _CHECKPOINT_KEEP),
            )
        await conn.commit()
    return cur.rowcount  # checkpoints deleted (last statement of the loop)


async def _finish_turn(result: dict, cid: str, broadcast) -> bool:
    """Close one graph invocation: emit approval_request (suspended -> True),
    or error/done (-> False). Shared by run_turn and resume_turn -- a resumed
    run can hit another interrupt (edit re-raised the tier) and repeat."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        gate.register_pending(payload, cid)
        await broadcast("approval_request", payload)
        if result.get("task_id"):
            # A real task context exists (Step 7+): surface the pause.
            await broadcast(
                "task_state", {"task_id": result["task_id"], "state": "waiting_approval", "lane": 1}
            )
        return True
    if result.get("error"):
        # e.g. a 401's "openrouter key rejected — check Settings" passes
        # through verbatim -- the message already names the way forward.
        await broadcast(
            "error",
            {"code": "turn_failed", "message": result["error"], "recoverable": True, "conversation_id": cid},
        )
    else:
        payload = {"conversation_id": cid}
        if result.get("redirected"):
            payload["interrupted"] = True
        await broadcast("done", payload)
    # The turn is over, so both rolling windows can be trimmed now -- and only
    # now: mid-turn, this thread's checkpoints are the resume state.
    try:
        await _prune_checkpoints(cid)
        await asyncio.to_thread(store.prune_actions)
    except Exception:  # noqa: BLE001 - rule 5: retention is housekeeping, never a turn error
        logger.exception("retention sweep failed after turn on %s", cid)
    return False


async def rehydrate_pending() -> None:
    """Rebuild gate's approval_id -> conversation_id map from the checkpoints
    after a Brain restart. Without this a Tier-3 approval that was open when
    the process died is either invisible (task stuck forever) or shown but
    unresumable -- both dishonest.

    Source of truth is the checkpoint itself, so nothing can drift out of sync
    with it. Idempotent: safe to call on every connect (two windows do), and a
    live entry is never clobbered."""
    graph = await _ensure_graph()
    saver = graph.checkpointer
    await saver.setup()
    # ponytail: one row per suspended thread via a direct DISTINCT query --
    # alist(None) would deserialize every checkpoint in the file just to read
    # thread ids. Personal-scale table; add an index if it ever gets big.
    async with saver.lock, saver.conn.execute("SELECT DISTINCT thread_id FROM checkpoints") as cur:
        thread_ids = [row[0] async for row in cur]
    for cid in thread_ids:
        snap = await graph.aget_state({"configurable": {"thread_id": cid}})
        for intr in snap.interrupts:
            payload = intr.value
            if not isinstance(payload, dict) or "approval_id" not in payload:
                continue
            if gate.peek_conversation(payload["approval_id"]) is not None:
                continue  # already registered (live, or a prior rehydrate)
            logger.info("rehydrated pending approval %s for %s", payload["approval_id"], cid)
            gate.register_pending(payload, cid)


_SNAPSHOT_TASK_STATES = ["running", "paused", "waiting_approval"]


def _task_frame(row: dict) -> dict:
    frame = {"task_id": row["task_id"], "state": row["state"], "lane": row["lane"]}
    for key in ("title", "step", "steps_total", "step_label", "reason"):
        if row.get(key) is not None:
            frame[key] = row[key]
    return frame


async def snapshot(send) -> None:
    """Real snapshot-on-connect (D6): everything the reconnecting UI needs to
    render truthfully, to that ONE client -- never broadcast, or a second
    window's reconnect would spam the first. Id-keyed frames and one frame per
    entity, so reconnecting converges instead of duplicating.

    `spend_update` is always last -- the same "snapshot done" sentinel the mock
    established, which tests drain against."""
    try:
        key_status = await asyncio.to_thread(secrets_store.key_status)
    except Exception as exc:  # noqa: BLE001 - snapshot must resolve Settings honestly
        logger.warning("openrouter key status read failed (%s)", type(exc).__name__)
        await send("settings_state", {"key": "openrouter_key", "status": "invalid"})
        await send(
            "error",
            {
                "code": "keystore_unavailable",
                "message": "the system credential store is unavailable; retry from Settings",
                "recoverable": True,
            },
        )
    else:
        await send("settings_state", {"key": "openrouter_key", "status": key_status})

    await send(
        "capabilities_state",
        {
            "voice_input": False,
            "task_controls": False,
            "skill_controls": False,
            "demo_scenarios": False,
        },
    )

    store.connect()
    tasks = await asyncio.to_thread(store.list_tasks, _SNAPSHOT_TASK_STATES)
    for row in tasks:
        await send("task_state", _task_frame(row))

    try:
        await rehydrate_pending()
    except Exception:  # noqa: BLE001 - a broken rehydrate must not block the
        # rest of the snapshot; the UI still gets tasks/beliefs/activity.
        logger.exception("pending-approval rehydration failed")
    for payload in gate.pending_payloads():
        await send("approval_request", payload)

    await gate.push_activity_backlog(send)
    await memory.push_beliefs(send)
    totals = llm.session_totals()
    await send(
        "spend_update",
        {
            "session_usd": totals["cost"],
            "month_usd": await asyncio.to_thread(store.month_spend),
            # No last_turn_tokens on a fresh connect -- it's per-turn (optional).
            "session_tokens": int(totals["prompt_tokens"] + totals["completion_tokens"]),
        },
    )


async def run_turn(msg: dict, broadcast) -> None:
    """One chat turn, called by server.py inside the conversation lock."""
    cid = msg["conversation_id"]
    try:
        graph = await _ensure_graph()
        stubbed = bool(os.environ.get("HALO_LLM_STUB"))
        api_key = "stub-key" if stubbed else await asyncio.to_thread(secrets_store.get_key)
        if not api_key:
            await broadcast(
                "error",
                {
                    "code": "no_api_key",
                    "message": "add your OpenRouter key in Settings",
                    "recoverable": True,
                    "conversation_id": cid,
                },
            )
            return

        config = {"configurable": {"thread_id": cid}, "recursion_limit": _RECURSION_LIMIT}
        prior = await graph.aget_state(config)
        content = msg["text"]
        if prior.values.get("redirected"):
            content = _REDIRECT_NOTE + content

        try:
            beliefs = await asyncio.to_thread(memory.retrieve, msg["text"])
        except Exception:  # noqa: BLE001 - memory degrades, never breaks the turn
            logger.exception("belief retrieval failed; continuing without memory")
            beliefs = []
        try:
            summary = await asyncio.to_thread(memory.episodic_prepend, cid)
        except Exception:  # noqa: BLE001 - episodic is best-effort continuity
            logger.exception("episodic retrieval failed; continuing without it")
            summary = None
        mem_lines: list[str] = []
        if summary:
            mem_lines.append(f"Last session: {summary}")
        if beliefs:
            mem_lines.append(
                "Things I remember about the user:\n" + "\n".join(f"- {b['text']}" for b in beliefs)
            )
        mem_msgs = [{"role": "system", "content": "\n\n".join(mem_lines)}] if mem_lines else []
        ctx = {"broadcast": broadcast, "stop": asyncio.Event(), "api_key": api_key, "usage": {}, "memory": mem_msgs}
        _turn_ctx[cid] = ctx
        try:
            result = await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": content}],
                    "error": None,
                    "redirected": False,
                    "pending_tool_result": None,
                    "pending_tool_calls": None,
                    "tool_rounds": 0,  # per-turn: the cap is not cumulative
                },
                config,
            )
        finally:
            _turn_ctx.pop(cid, None)

        if await _finish_turn(result, cid, broadcast):
            return  # suspended on a Tier-3 approval: no done, lock releases
        await _bill_and_extract(cid, result, ctx, api_key, broadcast)
    except Exception as exc:  # noqa: BLE001 - rule 2: never a silent drop
        logger.exception("turn failed for conversation_id=%s", cid)
        await broadcast(
            "error",
            {"code": "turn_failed", "message": str(exc), "recoverable": True, "conversation_id": cid},
        )


async def resume_turn(approval_id: str, decision: str, edited_args: dict | None, broadcast) -> bool:
    """Resume a turn suspended on a Tier-3 approval. Caller must hold that
    conversation's lock. Returns False if the approval is unknown/already
    handled (first response wins). Re-establishes _turn_ctx before ainvoke --
    run_turn popped it on suspend, and the resumed run may stream tokens and
    emit the confirming activity."""
    entry = gate.pop_pending(approval_id)
    if entry is None:
        return False
    cid = entry["conversation_id"]
    try:
        graph = await _ensure_graph()
        stubbed = bool(os.environ.get("HALO_LLM_STUB"))
        api_key = "stub-key" if stubbed else await asyncio.to_thread(secrets_store.get_key)
        resume_val: dict = {"decision": decision}
        if edited_args is not None:
            resume_val["edited_args"] = edited_args
        ctx = {"broadcast": broadcast, "stop": asyncio.Event(), "api_key": api_key or "", "usage": {}}
        _turn_ctx[cid] = ctx
        try:
            result = await graph.ainvoke(
                Command(resume=resume_val),
                {"configurable": {"thread_id": cid}, "recursion_limit": _RECURSION_LIMIT},
            )
        finally:
            _turn_ctx.pop(cid, None)
        if await _finish_turn(result, cid, broadcast):
            return True  # re-suspended (an edit raised the tier, or a later
            # round hit another Tier 3): nothing to bill or extract yet.
        await _bill_and_extract(cid, result, ctx, api_key, broadcast)
    except Exception as exc:  # noqa: BLE001 - rule 2: never a silent drop
        logger.exception("resume failed for approval_id=%s", approval_id)
        await broadcast(
            "error",
            {"code": "turn_failed", "message": str(exc), "recoverable": True, "conversation_id": cid,
             "operation_kind": "approval_response", "operation_id": approval_id},
        )
    return True


async def handle_approval_response(msg: dict, send, broadcast, locks: dict[str, asyncio.Lock]) -> None:
    """Non-mock approval_response dispatch. First response for an approval_id
    wins; a second (or unknown/stopped) one gets a recoverable error -- same
    behavior the mock established."""
    approval_id = msg["reply_to"]
    cid = gate.peek_conversation(approval_id)
    handled = False
    if cid is not None:
        async with locks.setdefault(cid, asyncio.Lock()):
            handled = await resume_turn(approval_id, msg["decision"], msg.get("edited_args"), broadcast)
    if not handled:
        await send(
            "error",
            {"code": "approval_already_handled", "message": "This approval was already handled.", "recoverable": True,
             "operation_kind": "approval_response", "operation_id": approval_id},
        )


async def handle_interrupt(msg: dict, broadcast, locks: dict[str, asyncio.Lock] | None = None) -> None:
    """Dual-mode stop (C3). Live streaming turn -> flip its stop event
    (checked between deltas, well under the contract's 2s); run_turn closes
    the turn with `done` and stickies the redirect note. No live turn but a
    pending Tier-3 approval -> implicit deny: resume the suspended graph with
    the cancelled decision (contract: "interrupt while waiting_approval ->
    implicit deny, then suspend"). Idle conversation -> no-op.

    Decision: plain chat is not a task, so no task_state frame is emitted --
    task_state requires task_id/state/lane and there is no task here. Real
    tasks (Step 7) will pause with their own task_state."""
    cid = msg["conversation_id"]
    ctx = _turn_ctx.get(cid)
    if ctx:
        ctx["stop"].set()
        return
    approval_id = gate.pending_for_conversation(cid)
    if approval_id is None or locks is None:
        return
    async with locks.setdefault(cid, asyncio.Lock()):
        await resume_turn(approval_id, "cancelled", None, broadcast)
