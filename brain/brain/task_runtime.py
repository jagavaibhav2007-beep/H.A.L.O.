"""Durable, bounded execution runtime for task-shaped Brain tools.

Tasks sit below the permission gate and outside interactive turn slots. Intent,
progress checkpoints, and terminal results are persisted before they are
broadcast, so restart reconciliation never blindly replays side effects.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from brain import store

logger = logging.getLogger("brain.task_runtime")

Broadcast = Callable[[str, dict], Awaitable[None]]
Continuation = Callable[[str, str, str], Awaitable[None]]
_LOG_CHUNK_BYTES = 4 * 1024
_LOG_FLUSH_SECONDS = 0.25


class TaskStopped(Exception):
    """Cooperative stop observed at a task step boundary."""


@dataclass
class TaskContext:
    task_id: str
    lane: int
    tier: int
    supports_pause: bool
    broadcast: Broadcast
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    pause_requested: asyncio.Event = field(default_factory=asyncio.Event)
    _seq: int = 0
    _log_buffer: str = ""
    _log_timer: asyncio.Task | None = None
    _checkpoint: dict | None = None

    async def checkpoint(self) -> None:
        if self.cancelled.is_set():
            raise TaskStopped()
        if not self.pause_requested.is_set():
            return
        if not self.supports_pause:
            return
        await self._state("paused")
        while self.pause_requested.is_set():
            if self.cancelled.is_set():
                raise TaskStopped()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.cancelled.wait(), timeout=0.1)
        if self.cancelled.is_set():
            raise TaskStopped()
        await self._state("running")

    async def _state(self, state: str, **fields) -> None:
        await asyncio.to_thread(store.upsert_task, self.task_id, state=state, **fields)
        await self.broadcast("task_state", {"task_id": self.task_id, "state": state, "lane": self.lane, **fields})

    async def progress(
        self,
        step: int,
        steps_total: int,
        step_label: str,
        *,
        checkpoint: dict | None = None,
    ) -> None:
        await self.checkpoint()
        if checkpoint is not None:
            self._checkpoint = checkpoint
        fields = {
            "step": step,
            "steps_total": steps_total,
            "step_label": step_label,
        }
        if checkpoint is not None:
            fields["checkpoint_json"] = json.dumps(checkpoint, ensure_ascii=False, default=str)
        await asyncio.to_thread(store.upsert_task, self.task_id, state="running", **fields)
        await self.broadcast("task_state", {
            "task_id": self.task_id,
            "state": "running",
            "lane": self.lane,
            "step": step,
            "steps_total": steps_total,
            "step_label": step_label,
        })

    async def log(self, text: str) -> None:
        if not text:
            return
        self._log_buffer += text
        if len(self._log_buffer.encode("utf-8")) >= _LOG_CHUNK_BYTES:
            await self.flush_log()
        elif self._log_timer is None:
            self._log_timer = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(_LOG_FLUSH_SECONDS)
            await self.flush_log()

    async def flush_log(self) -> None:
        timer = self._log_timer
        self._log_timer = None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        while self._log_buffer:
            raw = self._log_buffer.encode("utf-8")
            chunk = raw[:_LOG_CHUNK_BYTES].decode("utf-8", errors="ignore")
            self._log_buffer = self._log_buffer[len(chunk):]
            self._seq += 1
            await self.broadcast("task_log", {
                "task_id": self.task_id,
                "seq": self._seq,
                "text": chunk,
            })


@dataclass
class _ActiveTask:
    context: TaskContext
    runner: asyncio.Task
    tool: str


class TaskRuntime:
    def __init__(
        self,
        broadcast: Broadcast,
        continuation: Continuation | None = None,
        *,
        concurrency: int | None = None,
    ) -> None:
        configured = concurrency if concurrency is not None else int(os.environ.get("HALO_TASK_CONCURRENCY", "2"))
        self._slots = asyncio.Semaphore(max(1, configured))
        self._broadcast = broadcast
        self._continuation = continuation
        self._active: dict[str, _ActiveTask] = {}
        self._closing = False

    @property
    def active(self) -> dict[str, _ActiveTask]:
        return self._active

    async def submit(
        self,
        *,
        task_id: str,
        conversation_id: str,
        tool: str,
        args: dict,
        args_redacted: dict,
        tier: int,
        lane: int,
        title: str,
        steps_total: int | None,
        supports_pause: bool,
        fn,
        inverse_builder=None,
    ) -> str:
        if self._closing:
            raise RuntimeError("task runtime is shutting down")
        if task_id in self._active:
            raise ValueError(f"task already active: {task_id}")
        action_id, _ = await asyncio.to_thread(
            store.record_action,
            tool,
            args_redacted,
            tier,
            lane,
            "intent",
            False,
            None,
            task_id,
        )
        fields = {
            "state": "waiting",
            "lane": lane,
            "title": title,
            "step": 0,
            "conversation_id": conversation_id,
            "tool": tool,
            "args_json": json.dumps(args, ensure_ascii=False, default=str),
            "supports_pause": int(supports_pause),
            "intent_action_id": action_id,
        }
        if steps_total is not None:
            fields["steps_total"] = steps_total
        await asyncio.to_thread(store.upsert_task, task_id, **fields)
        await self._broadcast("task_state", {
            "task_id": task_id,
            "state": "waiting",
            "lane": lane,
            "title": title,
            "step": 0,
            **({"steps_total": steps_total} if steps_total is not None else {}),
        })
        context = TaskContext(task_id, lane, tier, supports_pause, self._broadcast)
        runner = asyncio.create_task(self._run(
            context=context,
            conversation_id=conversation_id,
            tool=tool,
            args=args,
            args_redacted=args_redacted,
            tier=tier,
            title=title,
            steps_total=steps_total,
            fn=fn,
            inverse_builder=inverse_builder,
        ))
        self._active[task_id] = _ActiveTask(context, runner, tool)
        runner.add_done_callback(lambda _done: self._active.pop(task_id, None))
        return task_id

    async def _run(self, **job) -> None:
        context: TaskContext = job["context"]
        try:
            async with self._slots:
                await context.checkpoint()
                await asyncio.to_thread(
                    store.upsert_task,
                    context.task_id,
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
                fields = {}
                if job["steps_total"] is not None:
                    fields["steps_total"] = job["steps_total"]
                await context._state("running", **fields)
                fn = job["fn"]
                if inspect.iscoroutinefunction(fn):
                    output = await fn(job["args"], context)
                else:
                    output = await asyncio.to_thread(fn, job["args"], context)
                await context.checkpoint()
                await self._finish_success(output=output, **job)
        except TaskStopped:
            await self._finish_stopped(**job)
        except asyncio.CancelledError:
            if not self._closing:
                await self._finish_failure("task worker was cancelled", **job)
            raise
        except Exception as exc:  # noqa: BLE001 - task failure is durable state
            logger.exception("task %s failed", context.task_id)
            await self._finish_failure(str(exc), **job)
        finally:
            await context.flush_log()

    async def _record_result(self, output, result: str, **job) -> str | None:
        inverse = None
        builder = job.get("inverse_builder")
        if builder is not None and output is not None:
            try:
                inverse = builder(job["args"], output)
            except Exception:  # noqa: BLE001 - a broken inverse is honestly non-undoable
                logger.exception("task inverse failed for %s", job["tool"])
        _, undo_token = await asyncio.to_thread(
            store.record_action,
            job["tool"],
            job["args_redacted"],
            job["tier"],
            job["context"].lane,
            result,
            inverse is not None,
            inverse,
            job["context"].task_id,
        )
        return undo_token

    async def _finish_success(self, output, **job) -> None:
        from brain import gate

        ctx: TaskContext = job["context"]
        undo_token = await self._record_result(output, "ok", **job)
        result_json = json.dumps(output, ensure_ascii=False, default=str)
        await asyncio.to_thread(
            store.upsert_task,
            ctx.task_id,
            state="done",
            result_json=result_json,
            reason=None,
        )
        await self._broadcast("task_state", {
            "task_id": ctx.task_id,
            "state": "done",
            "lane": ctx.lane,
            "title": job["title"],
            **({"steps_total": job["steps_total"], "step": job["steps_total"]} if job["steps_total"] is not None else {}),
        })
        await self._broadcast(
            "activity",
            gate._activity_frame(
                job["tool"], job["args"], ctx.task_id,
                undo_token is not None, job["tier"], ctx.lane, undo_token,
            ),
        )
        payload, _ = gate._cap_result(output)
        await self._continue(job["conversation_id"], ctx.task_id, f"Task {ctx.task_id} completed. Untrusted task result: {payload}")

    async def _finish_stopped(self, **job) -> None:
        ctx: TaskContext = job["context"]
        output = ctx._checkpoint
        undo_token = await self._record_result(output, "stopped", **job) if output else None
        await asyncio.to_thread(store.upsert_task, ctx.task_id, state="failed", reason="stopped")
        await self._broadcast("task_state", {
            "task_id": ctx.task_id, "state": "failed", "lane": ctx.lane, "reason": "stopped",
        })
        if undo_token:
            from brain import gate
            await self._broadcast(
                "activity",
                gate._activity_frame(job["tool"], job["args"], ctx.task_id, True, job["tier"], ctx.lane, undo_token),
            )
        await self._continue(job["conversation_id"], ctx.task_id, f"Task {ctx.task_id} stopped before completion.")

    async def _finish_failure(self, reason: str, **job) -> None:
        ctx: TaskContext = job["context"]
        undo_token = await self._record_result(ctx._checkpoint, f"error: {reason}", **job)
        await asyncio.to_thread(store.upsert_task, ctx.task_id, state="failed", reason=reason)
        await self._broadcast("task_state", {
            "task_id": ctx.task_id, "state": "failed", "lane": ctx.lane, "reason": reason,
        })
        if undo_token:
            from brain import gate
            await self._broadcast(
                "activity",
                gate._activity_frame(job["tool"], job["args"], ctx.task_id, True, job["tier"], ctx.lane, undo_token),
            )
        await self._continue(job["conversation_id"], ctx.task_id, f"Task {ctx.task_id} failed: {reason}")

    async def _continue(self, conversation_id: str, task_id: str, text: str) -> None:
        if self._continuation is not None and not self._closing:
            await self._continuation(conversation_id, text, task_id)

    async def handle_op(self, msg: dict, send: Broadcast) -> None:
        requested = msg.get("task_id")
        targets = [self._active[requested]] if requested in self._active else (
            list(self._active.values()) if requested is None else []
        )
        if not targets:
            await send("error", {
                "code": "task_not_active",
                "message": "the requested task is not active",
                "recoverable": True,
                "operation_kind": "task_op",
                "operation_id": requested or msg["id"],
            })
            return
        op = msg["op"]
        for active in targets:
            ctx = active.context
            if op == "pause":
                if not ctx.supports_pause:
                    await send("error", {
                        "code": "task_pause_unsupported",
                        "message": f"{active.tool} cannot be paused safely",
                        "recoverable": True,
                        "operation_kind": "task_op",
                        "operation_id": ctx.task_id,
                    })
                    continue
                ctx.pause_requested.set()
            elif op == "resume":
                if not ctx.supports_pause:
                    await send("error", {
                        "code": "task_pause_unsupported",
                        "message": f"{active.tool} cannot be resumed because it does not support pause",
                        "recoverable": True,
                        "operation_kind": "task_op",
                        "operation_id": ctx.task_id,
                    })
                    continue
                ctx.pause_requested.clear()
            else:
                ctx.cancelled.set()

    async def reconcile(self) -> int:
        """Mark torn tasks failed and preserve an undo for checkpointed work."""
        from brain import gate

        rows = await asyncio.to_thread(store.list_tasks, ["waiting", "running", "paused"])
        for row in rows:
            checkpoint = None
            if row.get("checkpoint_json"):
                with suppress(TypeError, ValueError):
                    checkpoint = json.loads(row["checkpoint_json"])
            reason = "Brain restarted; task was reconciled without replaying side effects"
            undo_token = None
            entry = gate.TOOLS.get(row.get("tool"))
            if checkpoint and entry and entry.get("inverse"):
                try:
                    args = json.loads(row.get("args_json") or "{}")
                    inverse = entry["inverse"](args, checkpoint)
                    _, undo_token = await asyncio.to_thread(
                        store.record_action,
                        row["tool"],
                        gate.redact(row["tool"], args),
                        gate.classify(row["tool"], args),
                        row.get("lane") or 1,
                        "reconciled_partial_after_restart",
                        inverse is not None,
                        inverse,
                        row["task_id"],
                    )
                except Exception:  # noqa: BLE001 - reconciliation still fails honestly
                    logger.exception("could not build recovery action for task %s", row["task_id"])
            await asyncio.to_thread(store.upsert_task, row["task_id"], state="failed", reason=reason)
            payload = {"task_id": row["task_id"], "state": "failed", "lane": row.get("lane") or 1, "reason": reason}
            await self._broadcast("task_state", payload)
            if undo_token:
                await self._broadcast("activity", {
                    "text": f"Recovered partial {row.get('tool') or 'task'} work after restart.",
                    "narrate": False,
                    "task_id": row["task_id"],
                    "undoable": True,
                    "undo_token": undo_token,
                    "tier": 2,
                    "lane": row.get("lane") or 1,
                })
        return len(rows)

    async def close(self) -> None:
        self.cancel()
        active = list(self._active.values())
        if active:
            _done, pending = await asyncio.wait(
                [item.runner for item in active], timeout=2.0,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    def cancel(self) -> None:
        self._closing = True
        for item in list(self._active.values()):
            item.context.cancelled.set()


_runtime: TaskRuntime | None = None


def install(runtime: TaskRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def current() -> TaskRuntime | None:
    return _runtime


def new_task_id(prefix: str = "task") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
