"""Authenticated loopback WebSocket server for Brain, UI, and Voice.

All frames use ``brain.ipc.contract``; real mode dispatches the Phase 2
backend while mock mode serves the scripted UI scenarios.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
import uuid
from contextlib import asynccontextmanager, contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.exceptions import ConnectionClosed

from brain import mock as mock_engine
from brain.ipc.contract import (
    CONTRACT_VERSION,
    IpcValidationError,
    contract_major,
    parse_ipc_message,
)

logger = logging.getLogger("brain.server")

SESSION_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Halo"
SESSION_FILE = SESSION_DIR / "session.json"
INSTANCE_LOCK_FILE = SESSION_DIR / "brain.lock"

# Per-client send timeout for _broadcast. A client whose socket buffer fills
# (it stopped reading but hasn't closed) would otherwise block the send
# forever while the conversation lock is held -- freezing every conversation.
_SEND_TIMEOUT_S = 5.0
_DEFERRED_MAX_FRAMES = 256
_DEFERRED_MAX_BYTES = 1_048_576
_REAL_TURN_CONCURRENCY = 4
_MAX_INFLIGHT_REQUESTS = 128
_MAX_INFLIGHT_PER_PRINCIPAL = 32
_WS_MAX_FRAME_BYTES = 1_048_576
_WS_MAX_QUEUE = 32


class _ConversationLocks:
    """Reference-counted per-conversation serialization with idle eviction."""

    def __init__(self) -> None:
        self._entries: dict[str, list] = {}

    @asynccontextmanager
    async def hold(self, conversation_id: str):
        entry = self._entries.get(conversation_id)
        if entry is None:
            entry = [asyncio.Lock(), 0]
            self._entries[conversation_id] = entry
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0 and self._entries.get(conversation_id) is entry:
                self._entries.pop(conversation_id, None)

    def __len__(self) -> int:
        return len(self._entries)


class BrainAlreadyRunning(RuntimeError):
    pass


def _flock(handle, lock: bool) -> None:
    """Non-blocking exclusive lock (lock=True) or unlock (lock=False), per-platform."""
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK if lock else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), (fcntl.LOCK_EX | fcntl.LOCK_NB) if lock else fcntl.LOCK_UN)


@contextmanager
def single_instance_lock(lock_file: Path = INSTANCE_LOCK_FILE):
    """Hold a crash-safe OS file lock for the Brain process lifetime."""
    lock_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = lock_file.open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _flock(handle, True)
        except OSError as exc:
            raise BrainAlreadyRunning("another Halo Brain is already running") from exc
        acquired = True
        yield
    finally:
        if acquired:
            handle.seek(0)
            _flock(handle, False)
        handle.close()


def _envelope(msg_type: str, payload: dict) -> dict:
    """Build a valid outbound frame and confirm it against the shared validator."""
    frame = {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    parse_ipc_message(frame)  # raises if we ever drift from the contract
    return frame


async def _send(ws: ServerConnection, msg_type: str, payload: dict) -> None:
    await asyncio.wait_for(
        ws.send(json.dumps(_envelope(msg_type, payload))), timeout=_SEND_TIMEOUT_S
    )


def _frame_visible_to(role: str, msg_type: str, payload: dict) -> bool:
    """The contract's outbound routing rule (11-ipc-contract.md): the UI gets
    everything; Voice is sent ONLY the subset it speaks (token, narrated
    activity, approval_request). Brain routes -- clients never filter a
    firehose -- so this is where the rule is enforced."""
    if role == "ui":
        return True
    if msg_type in ("token", "approval_request"):
        return True
    return msg_type == "activity" and payload.get("narrate") is True


# Clients still receiving their snapshot: broadcasts aimed at them are held
# here and flushed, in order, once the snapshot finishes. A client is added to
# `authenticated` BEFORE its snapshot so no event is missed, but a broadcast
# landing mid-snapshot would otherwise interleave into it -- landing ahead of
# the `snapshot_complete` marker that closes it, and showing the UI a live
# delta before the state it applies to.
_deferred: dict[ServerConnection, list[str]] = {}
_deferred_bytes: dict[ServerConnection, int] = {}


async def _broadcast(authenticated: dict[ServerConnection, str], msg_type: str, payload: dict) -> None:
    """Send one contract-validated frame to every authenticated client whose
    role should receive it (D4 -- "the UI gets everything", and reply-to-sender
    is just the one-client case of this). Dead connections are pruned
    defensively; the connection handler's own `finally` remains the source of
    truth."""
    raw = json.dumps(_envelope(msg_type, payload))
    for client, role in list(authenticated.items()):
        if not _frame_visible_to(role, msg_type, payload):
            continue
        held = _deferred.get(client)
        if held is not None:
            if msg_type in ("stream_frame", "task_log"):
                # Volatile tails are never replayable; dropping during snapshot
                # avoids unbounded reconnect pressure. Durable task/activity
                # state follows immediately after snapshot completion.
                continue
            raw_bytes = len(raw.encode("utf-8"))
            held_bytes = _deferred_bytes.get(client, sum(len(frame.encode("utf-8")) for frame in held))
            if len(held) >= _DEFERRED_MAX_FRAMES or held_bytes + raw_bytes > _DEFERRED_MAX_BYTES:
                logger.warning("dropping client whose deferred snapshot queue overflowed")
                authenticated.pop(client, None)
                _deferred.pop(client, None)
                _deferred_bytes.pop(client, None)
                with suppress(ConnectionClosed, asyncio.TimeoutError):
                    await asyncio.wait_for(client.close(code=1013, reason="snapshot backlog overflow"), _SEND_TIMEOUT_S)
                continue
            held.append(raw)
            _deferred_bytes[client] = held_bytes + raw_bytes
            continue
        try:
            await asyncio.wait_for(client.send(raw), timeout=_SEND_TIMEOUT_S)
        except (ConnectionClosed, asyncio.TimeoutError):
            # Closed, or stalled-but-open (buffer full). Drop it from routing so
            # it can't hold the conversation lock hostage, then close the socket
            # -- the handler is parked in `async for raw in ws` and only reaches
            # its `finally` once the socket closes, so leaving teardown to it
            # leaks the socket, its task and its state for the process lifetime.
            authenticated.pop(client, None)
            with suppress(ConnectionClosed, asyncio.TimeoutError):
                await asyncio.wait_for(client.close(code=1013, reason="send timeout"), _SEND_TIMEOUT_S)


async def _release_deferred(ws: ServerConnection, authenticated: dict[ServerConnection, str]) -> None:
    """Snapshot finished: flush anything that arrived during it, in order.

    Drains from the FRONT and drops the key only once the queue is empty.
    Popping the key first would leave `_deferred[ws]` absent for the whole
    flush, so a `_broadcast` landing mid-drain would take the live path and
    overtake frames still held -- reopening the very interleave window the
    deferral exists to close. Module-level rather than a handler closure so a
    test can drive the real function instead of reimplementing the drain."""
    held = _deferred.get(ws)
    while held:
        raw = held[0]
        try:
            await asyncio.wait_for(ws.send(raw), timeout=_SEND_TIMEOUT_S)
        except (ConnectionClosed, asyncio.TimeoutError):
            authenticated.pop(ws, None)
            _deferred.pop(ws, None)
            _deferred_bytes.pop(ws, None)
            return
        held.pop(0)
        _deferred_bytes[ws] = _deferred_bytes.get(ws, 0) - len(raw.encode("utf-8"))
    _deferred.pop(ws, None)
    _deferred_bytes.pop(ws, None)


def write_session_file(
    port: int,
    token: str,
    session_file: Path = SESSION_FILE,
    *,
    voice_token: str | None = None,
) -> None:
    """Write {port, token} atomically to %LOCALAPPDATA%\\Halo\\session.json.

    # ponytail: %LOCALAPPDATA% is already per-user on Windows, so this dir
    # isn't world-readable by other accounts. Tightening further (explicit
    # icacls ACL restricting even other processes under the same user) is
    # the upgrade path if that boundary ever needs to be stricter.
    """
    session_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = session_file.with_suffix(".tmp")
    payload = {"port": port, "token": token}
    if voice_token is not None:
        payload["voice_token"] = voice_token
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    if os.name != "nt":
        os.chmod(session_file.parent, 0o700)
        os.chmod(tmp, 0o600)
    tmp.replace(session_file)


async def _handle_settings_update(msg: dict, send_fn) -> None:
    """Real-mode settings_update (Phase 2 Step 2). openrouter_key goes to the
    OS keystore (D8: the value is never logged or echoed back -- status only);
    everything else persists to the store's settings table. Reports a
    settings_state back to the sender only -- this is per-client Settings
    feedback, not a global event (11-ipc-contract.md)."""
    from brain import llm, secrets_store, store

    key, value = msg["key"], msg.get("value")
    if key == "project_roots":
        from brain.tools import files

        if (
            not isinstance(value, list)
            or len(value) > 32
            or any(not isinstance(item, str) or not item.strip() or len(item) > 4096 for item in value)
        ):
            await send_fn("error", {
                "code": "invalid_project_roots",
                "message": "Project roots must be a list of at most 32 non-empty paths.",
                "recoverable": True,
            })
            return
        store.connect()
        await asyncio.to_thread(store.set_setting, key, value)
        await send_fn("project_roots_state", await asyncio.to_thread(files.project_roots_state))
        return
    if key != "openrouter_key":
        store.connect()
        await asyncio.to_thread(store.set_setting, key, value)
        return

    if not value:
        try:
            await asyncio.to_thread(secrets_store.delete_key)
        except Exception as exc:  # noqa: BLE001 - keystore backends fail in many platform-specific ways
            logger.warning("openrouter key deletion failed (%s)", type(exc).__name__)
            await _send_keystore_error(send_fn, key)
            return
        await send_fn("settings_state", {"key": key, "status": "missing"})
        return

    try:
        # Persist the conservative state first. If validation or its final
        # metadata write fails, reconnect must never upgrade the key to "set".
        await asyncio.to_thread(secrets_store.set_validation_status, "unverified")
        await asyncio.to_thread(secrets_store.set_key, value)
    except Exception as exc:  # noqa: BLE001 - never strand Settings on a keyring failure
        logger.warning("openrouter key storage failed (%s)", type(exc).__name__)
        await _send_keystore_error(send_fn, key)
        return
    try:
        ok = await llm.validate_key(value)
    except Exception as exc:  # noqa: BLE001 - any transport error -> stored but unconfirmed
        logger.warning("openrouter key validation failed (%s)", type(exc).__name__)
        status = "unverified"
    else:
        status = "set" if ok else "invalid"
    try:
        await asyncio.to_thread(secrets_store.set_validation_status, status)
    except Exception as exc:  # noqa: BLE001 - status persistence is part of the truthful save
        logger.warning("openrouter key validation status storage failed (%s)", type(exc).__name__)
        await _send_keystore_error(send_fn, key)
        return
    logger.info("openrouter key status: %s", status)
    await send_fn("settings_state", {"key": key, "status": status})


async def _send_keystore_error(send_fn, key: str = "openrouter_key") -> None:
    """Resolve pending Settings UI state without exposing backend details."""
    await send_fn("settings_state", {"key": key, "status": "invalid"})
    await send_fn(
        "error",
        {
            "code": "keystore_unavailable",
            "message": "the system credential store is unavailable; retry from Settings",
            "recoverable": True,
        },
    )


class _ServerRuntime:
    """Strongly retain request/background tasks and own their shutdown."""

    def __init__(
        self,
        *,
        max_tasks: int = _MAX_INFLIGHT_REQUESTS,
        max_tasks_per_owner: int = _MAX_INFLIGHT_PER_PRINCIPAL,
    ) -> None:
        self.tasks: set[asyncio.Task] = set()
        self.max_tasks = max_tasks
        self.max_tasks_per_owner = max_tasks_per_owner
        self._owner_counts: dict[object, int] = {}
        self.closing = False

    @staticmethod
    def _discard(awaitable) -> None:
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        elif isinstance(awaitable, asyncio.Task):
            awaitable.cancel()

    def spawn(
        self,
        awaitable,
        send_fn=None,
        context: dict | None = None,
        *,
        owner: object | None = None,
    ) -> bool:
        if self.closing:
            self._discard(awaitable)
            return False
        if len(self.tasks) >= self.max_tasks or (
            owner is not None
            and self._owner_counts.get(owner, 0) >= self.max_tasks_per_owner
        ):
            self._discard(awaitable)
            return False

        started = False

        async def supervised() -> None:
            nonlocal started
            started = True
            try:
                await awaitable
            except asyncio.CancelledError:
                raise
            except ConnectionClosed:
                return
            except Exception:  # noqa: BLE001 - detached failures must always be observed
                logger.exception("background request handler failed")
                if send_fn is None:
                    return
                payload = {
                    "code": "request_failed",
                    "message": "the request failed unexpectedly; retry",
                    "recoverable": True,
                }
                if context and context.get("conversation_id"):
                    payload["conversation_id"] = context["conversation_id"]
                correlation = _operation_correlation(context)
                if correlation:
                    payload.update(correlation)
                with suppress(ConnectionClosed, asyncio.TimeoutError):
                    await send_fn("error", payload)

        task = asyncio.create_task(supervised())
        self.tasks.add(task)
        if owner is not None:
            self._owner_counts[owner] = self._owner_counts.get(owner, 0) + 1

        def finished(done: asyncio.Task) -> None:
            self.tasks.discard(done)
            if not started:
                self._discard(awaitable)
            if owner is not None:
                remaining = self._owner_counts.get(owner, 1) - 1
                if remaining > 0:
                    self._owner_counts[owner] = remaining
                else:
                    self._owner_counts.pop(owner, None)

        task.add_done_callback(finished)
        return True

    def cancel(self) -> None:
        self.closing = True
        for task in tuple(self.tasks):
            task.cancel()

    async def wait_closed(self) -> None:
        self.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


class ManagedServer:
    """WebSocket server plus the Brain work whose lifetime it owns."""

    def __init__(self, server: Server, runtime: _ServerRuntime, voice_token: str, task_engine=None) -> None:
        self._server = server
        self._runtime = runtime
        self.voice_token = voice_token
        self._task_engine = task_engine

    @property
    def sockets(self):
        return self._server.sockets

    def close(self) -> None:
        if self._task_engine is not None:
            self._task_engine.cancel()
        self._runtime.cancel()
        self._server.close()

    async def wait_closed(self) -> None:
        await self._server.wait_closed()
        if self._task_engine is not None:
            await self._task_engine.close()
        await self._runtime.wait_closed()

    async def serve_forever(self) -> None:
        try:
            await self._server.serve_forever()
        finally:
            self.close()
            await self.wait_closed()


# Mock-only inbound types -> (mock_engine handler name, needs send_fn instead
# of broadcast_fn). Keep in sync with mock.py's handle_* functions -- a type
# handled here but not in mock.py (or vice versa) is the documented "affordance
# hangs forever" bug class (see CLAUDE.md, "Working against the mocked Brain").
# Control operations the real (non-mock) Brain can't execute yet (lane pinning,
# real voice capture, and skill management). They route through
# _send_unsupported_operation so the UI affordance gets a correlated error
# instead of hanging forever waiting on a confirming frame that never comes.
_REAL_UNSUPPORTED_OPS: tuple[str, ...] = ("lane_pin", "mic", "skill_op")

# Every inbound type the real dispatch in _connection_handler actually handles
# (real handlers + the unsupported-op group above), minus the `hello`
# handshake, which _auth consumes before the message loop. A UI-sendable
# inbound type in the contract that is NOT here is the "mock handles it, real
# Brain silently drops it, affordance hangs" bug (mem/Bugs.md #12) --
# test_server enumerates the contract and fails if this set drifts. Keep it
# beside the if/elif chain in _connection_handler that it mirrors.
_REAL_DISPATCH_TYPES: frozenset[str] = frozenset({
    "user_msg", "conversation_history_query", "settings_update", "interrupt", "approval_response",
    "memory_edit", "memory_query", "undo", "task_op",
    *_REAL_UNSUPPORTED_OPS,
})

_MOCK_DISPATCH: dict[str, tuple[str, bool]] = {
    "approval_response": ("handle_approval_response", True),
    "interrupt": ("handle_interrupt", False),
    "undo": ("handle_undo", False),
    "task_op": ("handle_task_op", False),
    "lane_pin": ("handle_lane_pin", False),
    "memory_edit": ("handle_memory_edit", False),
    "memory_query": ("handle_memory_query", True),
    "conversation_history_query": ("handle_conversation_history_query", True),
    "skill_op": ("handle_skill_op", False),
    "mic": ("handle_mic", False),
    "settings_update": ("handle_settings_update", True),
}

_ROLE_INBOUND: dict[str, frozenset[str]] = {
    "ui": _REAL_DISPATCH_TYPES,
    # Voice may contribute a spoken turn or stop the turn it initiated. It may
    # never mutate settings/memory/files, approve actions, operate tasks, or
    # claim UI authority merely by changing a hello/source field.
    "voice": frozenset({"user_msg", "interrupt"}),
}


def _operation_correlation(msg: dict | None) -> dict | None:
    if not msg:
        return None
    fields = {
        "undo": "undo_token", "memory_edit": "belief_id", "approval_response": "reply_to",
        "task_op": "task_id", "lane_pin": "task_id", "skill_op": "skill_name",
    }
    kind = msg.get("type")
    target = msg.get(fields.get(kind, ""))
    if kind in fields and isinstance(target, str):
        return {"operation_kind": kind, "operation_id": target}
    return None


async def _send_unsupported_operation(msg: dict, send_fn) -> None:
    correlation = _operation_correlation(msg)
    if correlation is None:
        correlation = {"operation_kind": msg["type"], "operation_id": msg["id"]}
    messages = {
        "lane_pin": "Task lane controls are not available in the real Brain yet.",
        "mic": "Voice input is not available in the real Brain yet.",
        "skill_op": "Skill controls are not available in the real Brain yet.",
    }
    await send_fn("error", {
        "code": "operation_unsupported",
        "message": messages.get(msg["type"], "This operation is not available in the real Brain yet."),
        "recoverable": True,
        **correlation,
    })


async def _serialize_user_msg(
    msg: dict,
    locks: _ConversationLocks,
    turn_slots: asyncio.Semaphore,
    send,
    broadcast,
    mock: bool,
) -> None:
    conversation_id = msg["conversation_id"]
    async with locks.hold(conversation_id):
        if mock:
            # Same turn_failed recovery the non-mock path has -- a mock handler
            # raising anything other than ConnectionClosed must not drop the turn
            # silently, leaving the UI waiting on a `done` that never comes.
            try:
                await mock_engine.handle_user_msg(msg, send, broadcast)
            except Exception as exc:  # noqa: BLE001 - turn must never drop silently
                logger.exception("mock turn failed for conversation_id=%s", conversation_id)
                await broadcast(
                    "error",
                    {"code": "turn_failed", "message": str(exc), "recoverable": True, "conversation_id": conversation_id},
                )
        else:
            # Real Brain (Phase 2 Step 4). graph.run_turn owns every error
            # frame for its turn (rule 2) -- no wrapper here, no double emit.
            from brain import graph  # ponytail: lazy -- keeps mock/test imports light

            # A burst of distinct conversations must not create an unbounded
            # number of provider streams, embedding jobs, and SQLite work.
            async with turn_slots:
                await graph.run_turn(msg, broadcast)


async def _auth(
    ws: ServerConnection,
    tokens: dict[str, str] | str,
    timeout: float,
) -> str | None:
    """First frame must be {type: hello, token}. Returns the client role
    ("ui"/"voice") if authenticated, else None."""
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout)
        frame = json.loads(raw)
        parsed = parse_ipc_message(frame)
    except Exception:
        logger.info("dropping connection: invalid hello frame")
        return None

    if parsed.get("type") != "hello":
        logger.info("dropping connection: first frame was not hello")
        return None

    # compare_digest on two str operands raises TypeError unless BOTH are pure
    # ASCII, and the contract only checks that `token` is a str -- so a
    # non-ASCII token from any unauthenticated local process would escape this
    # function as an unhandled traceback. Bytes are always comparable, and the
    # comparison stays constant-time.
    supplied = parsed.get("token")
    role_tokens = tokens if isinstance(tokens, dict) else {"ui": tokens}
    matched_role = None
    if isinstance(supplied, str):
        supplied_bytes = supplied.encode("utf-8")
        for candidate_role, expected in role_tokens.items():
            if secrets.compare_digest(supplied_bytes, expected.encode("utf-8")):
                matched_role = candidate_role
    if matched_role is None:
        logger.info("dropping connection: bad token")
        return None

    claimed_role = parsed.get("role") or "ui"
    if claimed_role != matched_role:
        logger.info("dropping connection: component credential/role mismatch")
        return None

    # Version negotiation runs only AFTER the token check -- an unauthenticated
    # peer learns nothing about the protocol. A major mismatch is terminal:
    # tell the (authenticated) client loudly so it can stop retrying instead of
    # storming the reconnect loop forever, then let the caller close. Absent
    # contract_version = pre-versioning client -> treated as compatible.
    their_major = contract_major(parsed.get("contract_version"))
    if their_major is not None and their_major != contract_major(CONTRACT_VERSION):
        logger.info("dropping connection: incompatible contract major (client=%s)", their_major)
        with suppress(ConnectionClosed, asyncio.TimeoutError):
            await _send(ws, "error", {
                "code": "incompatible_contract",
                "message": (
                    f"This client speaks contract {parsed.get('contract_version')}, "
                    f"but the Brain speaks {CONTRACT_VERSION}. Restart the app to update."
                ),
                "recoverable": False,
            })
        return None

    return matched_role


async def _connection_handler(
    ws: ServerConnection,
    tokens: dict[str, str],
    locks: _ConversationLocks,
    turn_slots: asyncio.Semaphore,
    auth_timeout: float,
    authenticated: dict[ServerConnection, str],
    runtime: _ServerRuntime,
    task_engine=None,
    mock: bool = False,
) -> None:
    role = await _auth(ws, tokens, auth_timeout)
    if role is None:
        await ws.close()
        return

    async def send_fn(msg_type: str, payload: dict) -> None:
        await _send(ws, msg_type, payload)

    async def broadcast_fn(msg_type: str, payload: dict) -> None:
        await _broadcast(authenticated, msg_type, payload)

    async def admit(awaitable, context: dict) -> None:
        if runtime.spawn(awaitable, send_fn, context, owner=role):
            return
        payload = {
            "code": "server_busy",
            "message": "too many requests are already running; retry shortly",
            "recoverable": True,
        }
        if context.get("conversation_id"):
            payload["conversation_id"] = context["conversation_id"]
        correlation = _operation_correlation(context)
        if correlation:
            payload.update(correlation)
        await send_fn("error", payload)

    try:
        await send_fn("hello_ack", {"contract_version": CONTRACT_VERSION})
    except (ConnectionClosed, asyncio.TimeoutError):
        return
    authenticated[ws] = role
    _deferred[ws] = []  # hold broadcasts until this client's snapshot is done
    _deferred_bytes[ws] = 0
    logger.info("client authenticated (role=%s, mock=%s)", role, mock)

    try:
        if mock and role == "ui":
            # D6: snapshot goes only to the connecting UI client, right after
            # hello_ack. Voice never gets it -- it's outside Voice's routing subset.
            await mock_engine.push_snapshot(send_fn)
        elif not mock and role == "ui":
            # Step 9: real mirror of the mock snapshot -- settings, live
            # tasks, rehydrated pending approvals, activity backlog, beliefs,
            # spend. Sent to this client only (see graph.snapshot).
            # graph transitively imports langgraph (seconds on first load) --
            # import off-loop so this connect never freezes other clients.
            import importlib

            graph_mod = await asyncio.to_thread(importlib.import_module, "brain.graph")
            await graph_mod.snapshot(send_fn)
        if role == "ui":
            # Explicit end-of-snapshot marker. Both snapshot builders happen to
            # end with `spend_update`, but the contract never gave that frame a
            # terminal role (it's a global that can arrive at any time), so the
            # UI reducer had no honest signal for leaving snapshot mode. Sent
            # here rather than inside either builder so real and mock share one
            # site. Voice gets no snapshot, so it gets no marker either.
            await send_fn("snapshot_complete", {})
        # Snapshot done (or none was owed -- Voice gets no snapshot): release
        # anything broadcast while it was streaming, then go live.
        await _release_deferred(ws, authenticated)

        async for raw in ws:
            try:
                frame = json.loads(raw)
                msg = parse_ipc_message(frame)
            except (json.JSONDecodeError, IpcValidationError) as exc:
                await send_fn("error", {"code": "bad_frame", "message": str(exc), "recoverable": True})
                continue

            if msg["type"] not in _ROLE_INBOUND[role]:
                await send_fn("error", {
                    "code": "operation_forbidden",
                    "message": f"the {role} component is not authorized for {msg['type']}",
                    "recoverable": False,
                })
                continue
            if msg["type"] == "user_msg" and msg.get("source") != role:
                await send_fn("error", {
                    "code": "source_mismatch",
                    "message": "message source does not match the authenticated component",
                    "recoverable": False,
                    "conversation_id": msg["conversation_id"],
                })
                continue

            if msg["type"] == "user_msg":
                await admit(
                    _serialize_user_msg(msg, locks, turn_slots, send_fn, broadcast_fn, mock),
                    msg,
                )
            elif not mock and msg["type"] == "settings_update":
                await admit(_handle_settings_update(msg, send_fn), msg)
            elif not mock and msg["type"] == "interrupt":
                # Must NOT route through the conversation lock -- a live turn
                # holds it and the interrupt just flips that turn's stop event.
                # The suspended-on-approval case takes the lock itself (it is
                # free then -- the turn ended at the interrupt()).
                from brain import graph

                await admit(graph.handle_interrupt(msg, broadcast_fn, locks), msg)
            elif not mock and msg["type"] == "approval_response":
                # Tier-3 resume (Phase 2 Step 5): re-acquires that approval's
                # conversation lock inside the handler.
                from brain import graph

                await admit(graph.handle_approval_response(msg, send_fn, broadcast_fn, locks), msg)
            elif not mock and msg["type"] == "memory_edit":
                # Step 8: memory panel round-trips against the real store.
                from brain import memory

                await admit(memory.handle_memory_edit(msg, broadcast_fn), msg)
            elif not mock and msg["type"] == "memory_query":
                from brain import memory

                await admit(memory.push_history(send_fn), msg)
            elif not mock and msg["type"] == "conversation_history_query":
                from brain import graph

                await admit(graph.push_conversation_history(msg, send_fn), msg)
            elif not mock and msg["type"] == "undo":
                # Step 6: undo is a global feed action, not tied to a
                # conversation -- no conversation lock; token consumption is
                # the atomic guard against a double-fire.
                from brain import gate

                await admit(gate.handle_undo(msg, broadcast_fn), msg)
            elif not mock and msg["type"] == "task_op":
                if task_engine is None:
                    await admit(_send_unsupported_operation(msg, send_fn), msg)
                else:
                    await admit(task_engine.handle_op(msg, send_fn), msg)
            elif not mock and msg["type"] in _REAL_UNSUPPORTED_OPS:
                await admit(_send_unsupported_operation(msg, send_fn), msg)
            elif mock and msg["type"] in _MOCK_DISPATCH:
                handler_name, needs_send = _MOCK_DISPATCH[msg["type"]]
                handler = getattr(mock_engine, handler_name)
                await admit(handler(msg, send_fn if needs_send else broadcast_fn), msg)
            # Other inbound types remain validated-but-unhandled outside mock
            # mode, per Phase 0 Step 4's original scope.
    finally:
        authenticated.pop(ws, None)
        _deferred.pop(ws, None)
        _deferred_bytes.pop(ws, None)


async def start(
    token: str | None = None,
    port: int = 0,
    auth_timeout: float = 5,
    mock: bool = False,
    voice_token: str | None = None,
) -> tuple[ManagedServer, str]:
    """Start the server in-process. Returns (server, token) for callers/tests
    that need the bound port without touching session.json."""
    token = token or secrets.token_urlsafe(32)
    voice_token = voice_token or secrets.token_urlsafe(32)
    tokens = {"ui": token, "voice": voice_token}
    locks = _ConversationLocks()
    turn_slots = asyncio.Semaphore(_REAL_TURN_CONCURRENCY)
    authenticated: dict[ServerConnection, str] = {}  # connection -> role ("ui"/"voice")
    runtime = _ServerRuntime()

    async def _broadcast_all(msg_type: str, payload: dict) -> None:
        await _broadcast(authenticated, msg_type, payload)

    task_engine = None
    if not mock:
        # Importing graph registers every task-shaped tool before reconciliation
        # inspects torn rows. The continuation re-enters the same conversation
        # lock/turn slots as a normal turn, but the generated task result carries
        # no approval-free mutation authority (`_internal`).
        from brain import graph, task_runtime

        async def continue_task(conversation_id: str, text: str, origin_turn_id: str) -> None:
            async with locks.hold(conversation_id), turn_slots:
                await graph.run_turn({
                    "text": text,
                    "conversation_id": conversation_id,
                    "source": "ui",
                    "turn_id": f"task-group-{origin_turn_id}",
                    "_internal": True,
                }, _broadcast_all)

        task_engine = task_runtime.TaskRuntime(_broadcast_all, continue_task)
        task_runtime.install(task_engine)
        await task_engine.reconcile()

    async def handler(ws: ServerConnection) -> None:
        await _connection_handler(
            ws, tokens, locks, turn_slots, auth_timeout, authenticated, runtime, task_engine, mock
        )

    server = await websockets.asyncio.server.serve(
        handler,
        "127.0.0.1",
        port,
        max_size=_WS_MAX_FRAME_BYTES,
        max_queue=_WS_MAX_QUEUE,
    )

    if not mock:
        # Step 8: belief decay -- once at start, then daily. Archive deltas
        # broadcast to whoever is connected at the time (reconnect hydration
        # replays current state anyway).
        from brain import memory

        runtime.spawn(memory.decay_loop(_broadcast_all))

    return ManagedServer(server, runtime, voice_token, task_engine), token


async def _run(mock: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="[brain] %(message)s")
    with single_instance_lock():
        server, token = await start(mock=mock)
        bound_port = server.sockets[0].getsockname()[1]
        write_session_file(bound_port, token, voice_token=server.voice_token)
        logger.info(
            "listening on 127.0.0.1:%d, session.json written to %s (mock=%s)", bound_port, SESSION_FILE, mock
        )
        try:
            await server.serve_forever()
        finally:
            # Drop the port+token so a client can't dial a dead Brain (or, worse,
            # an unrelated local listener that later grabs the same port). The
            # instance lock is released just after, so a fresh Brain rewrites it.
            SESSION_FILE.unlink(missing_ok=True)
            if not mock:
                # Graceful shutdown flushes any dirty conversations' memory
                # (graph.aclose -> memory.flush_all) before the process exits.
                from brain import graph

                await graph.aclose()


def main() -> None:
    mock = "--mock" in sys.argv[1:]
    try:
        asyncio.run(_run(mock=mock))
    except BrainAlreadyRunning as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
