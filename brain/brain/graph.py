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
_session_usd = 0.0  # per-process accumulator; resets on Brain restart by design
# conversation_id -> live-turn context while its stream runs. Callables stay
# out of the graph config so the checkpointer never tries to serialize them.
_turn_ctx: dict[str, dict] = {}


def _after_gate(state: State) -> str:
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
    return {"model": llm.route(text, state.get("escalated", False))}


async def _gate_node(state: State, config) -> dict:
    """Runs the HEAD of the pending-tool-call queue through gate.gated_execute
    -- Tier 3 raises GraphInterrupt from interrupt(), which MUST propagate to
    the checkpointer (no try/except here; see gate.py). One call per visit: the
    conditional edge sends us straight back here while the queue is non-empty."""
    cid = config["configurable"]["thread_id"]
    ctx = _turn_ctx[cid]
    queue = list(state.get("pending_tool_calls") or [])
    call = queue.pop(0)
    if call.get("error"):
        # Malformed arguments from the model: report it, never guess. The
        # result goes back as a tool message so it can correct itself.
        update: dict = {"pending_tool_result": {"tool": call["name"], "status": "error: bad arguments"}}
        content = f"I couldn't run {call['name']} — {call['error']}"
    else:
        update = await gate.gated_execute(
            call["name"],
            call["args"],
            conversation_id=cid,
            task_id=state.get("task_id"),
            broadcast=ctx["broadcast"],
        )
        content = (update.get("messages") or [{}])[0].get("content") or ""
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


def _prompt_messages(state: State) -> list[dict]:
    """The bounded history actually sent to the model: the running summary
    stands in for messages[:dropped_before]."""
    live = state["messages"][state.get("dropped_before") or 0:]
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
    # ponytail: soft cap. At the cap we make one more call with NO tools, so the
    # user gets a real answer grounded in every tool result so far instead of a
    # truncated turn. 8 is a guess at "a model that isn't converging"; raise it
    # if real multi-step tasks legitimately need more.
    tools = gate.tool_specs() if rounds < _MAX_TOOL_ROUNDS else None
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
        if state.get("model") == llm.LIGHT:
            # ponytail: any mid-LIGHT-stream exception escalates the next turn
            # to HEAVY -- a quality/parse-failure heuristic can refine this later.
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
    return _graph


async def aclose() -> None:
    """Close the checkpointer (tests' simulated restart; process exit doesn't
    need it -- SQLite WAL survives a kill, which is the restart semantics:
    history persists, a dead half-streamed turn is never auto-resumed)."""
    global _graph, _saver_conn
    try:
        if _saver_conn is not None:
            await _saver_conn.close()
    finally:
        _graph = None
        _saver_conn = None
        await llm.aclose()


def _spend_sync(cost: float) -> float:
    store.connect()
    store.add_spend(cost)
    return store.month_spend()


async def _bill_and_extract(cid: str, result: dict, ctx: dict, api_key: str | None, broadcast) -> None:
    """Shared completed-turn tail (run_turn and resume_turn): fire-and-forget
    memory extraction when the turn didn't error (memory.extract swallows its own
    failures, rule 5), then roll up cost and broadcast spend. resume owes this as
    much as run_turn: since the tool-calling loop landed, a resumed invoke is no
    longer just `gate -> END` -- it continues `gate -> respond -> ...` and streams
    a real (billable) final answer, so skipping it would undercount spend and skip
    extraction on approved-tool turns. The `api_key and` guard is harmless in
    run_turn (it early-returns without a key)."""
    global _session_usd
    if api_key and not result.get("error"):
        asyncio.create_task(memory.extract(cid, result.get("messages") or [], api_key, broadcast))
    cost = ctx["usage"].get("cost")
    if cost is not None:
        _session_usd += cost
        month = await asyncio.to_thread(_spend_sync, cost)
        await broadcast("spend_update", {"session_usd": _session_usd, "month_usd": month})


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
        await broadcast("done", {"conversation_id": cid})
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
    await send(
        "spend_update",
        {"session_usd": _session_usd, "month_usd": await asyncio.to_thread(store.month_spend)},
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
        mem_msgs = (
            [{
                "role": "system",
                "content": "Things I remember about the user:\n" + "\n".join(f"- {b['text']}" for b in beliefs),
            }]
            if beliefs
            else []
        )
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
