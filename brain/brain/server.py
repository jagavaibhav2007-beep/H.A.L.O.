"""Brain WebSocket server: loopback bind, session handshake, stub echo turn.

Phase 0, Steps 3-4 of phase-0-plan.md. Reuses brain.ipc.contract for all
message shapes and validation -- no second schema, no hand-rolled framing
(websockets does that).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.exceptions import ConnectionClosed

from brain import mock as mock_engine
from brain.ipc.contract import IpcValidationError, parse_ipc_message

logger = logging.getLogger("brain.server")

SESSION_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Halo"
SESSION_FILE = SESSION_DIR / "session.json"
INSTANCE_LOCK_FILE = SESSION_DIR / "brain.lock"

# Per-client send timeout for _broadcast. A client whose socket buffer fills
# (it stopped reading but hasn't closed) would otherwise block the send
# forever while the conversation lock is held -- freezing every conversation.
_SEND_TIMEOUT_S = 5.0


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
    await ws.send(json.dumps(_envelope(msg_type, payload)))


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
        try:
            await asyncio.wait_for(client.send(raw), timeout=_SEND_TIMEOUT_S)
        except (ConnectionClosed, asyncio.TimeoutError):
            # Closed, or stalled-but-open (buffer full). Drop it from routing so
            # it can't hold the conversation lock hostage; it will reconnect and
            # re-snapshot. ponytail: transport teardown is left to the
            # connection handler's own finally.
            authenticated.pop(client, None)


def write_session_file(port: int, token: str, session_file: Path = SESSION_FILE) -> None:
    """Write {port, token} atomically to %LOCALAPPDATA%\\Halo\\session.json.

    # ponytail: %LOCALAPPDATA% is already per-user on Windows, so this dir
    # isn't world-readable by other accounts. Tightening further (explicit
    # icacls ACL restricting even other processes under the same user) is
    # the upgrade path if that boundary ever needs to be stricter.
    """
    session_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = session_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
    if os.name != "nt":
        os.chmod(session_file.parent, 0o700)
        os.chmod(tmp, 0o600)
    tmp.replace(session_file)


async def _handle_user_msg(broadcast, msg: dict) -> None:
    """Phase-0 stub echo turn. `broadcast` never raises ConnectionClosed (it
    prunes dead clients internally), so a turn here can't crash on a client
    that dropped mid-turn."""
    conversation_id = msg["conversation_id"]
    try:
        await broadcast("token", {"text": f"echo: {msg['text']}", "conversation_id": conversation_id})
        await broadcast("done", {"conversation_id": conversation_id})
    except Exception as exc:  # noqa: BLE001 - turn must never drop silently
        logger.exception("turn failed for conversation_id=%s", conversation_id)
        await broadcast(
            "error",
            {"code": "turn_failed", "message": str(exc), "recoverable": True, "conversation_id": conversation_id},
        )


# Mock-only inbound types -> (mock_engine handler name, needs send_fn instead
# of broadcast_fn). Keep in sync with mock.py's handle_* functions -- a type
# handled here but not in mock.py (or vice versa) is the documented "affordance
# hangs forever" bug class (see CLAUDE.md, "Working against the mocked Brain").
_MOCK_DISPATCH: dict[str, tuple[str, bool]] = {
    "approval_response": ("handle_approval_response", True),
    "interrupt": ("handle_interrupt", False),
    "undo": ("handle_undo", False),
    "task_op": ("handle_task_op", False),
    "lane_pin": ("handle_lane_pin", False),
    "memory_edit": ("handle_memory_edit", False),
    "skill_op": ("handle_skill_op", False),
    "mic": ("handle_mic", False),
}


async def _serialize_user_msg(msg: dict, locks: dict[str, asyncio.Lock], send, broadcast, mock: bool) -> None:
    conversation_id = msg["conversation_id"]
    async with locks.setdefault(conversation_id, asyncio.Lock()):
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
            await _handle_user_msg(broadcast, msg)


async def _auth(ws: ServerConnection, token: str, timeout: float) -> str | None:
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

    supplied = parsed.get("token")
    if not isinstance(supplied, str) or not secrets.compare_digest(supplied, token):
        logger.info("dropping connection: bad token")
        return None

    # ponytail: unknown/missing role -> "ui" (the full stream). Only Voice
    # opts into the restricted subset by declaring role:"voice".
    return "voice" if parsed.get("role") == "voice" else "ui"


async def _connection_handler(
    ws: ServerConnection,
    token: str,
    locks: dict[str, asyncio.Lock],
    auth_timeout: float,
    authenticated: dict[ServerConnection, str],
    mock: bool = False,
) -> None:
    role = await _auth(ws, token, auth_timeout)
    if role is None:
        await ws.close()
        return

    async def send_fn(msg_type: str, payload: dict) -> None:
        await _send(ws, msg_type, payload)

    async def broadcast_fn(msg_type: str, payload: dict) -> None:
        await _broadcast(authenticated, msg_type, payload)

    try:
        await send_fn("hello_ack", {})
    except ConnectionClosed:
        return
    authenticated[ws] = role
    logger.info("client authenticated (role=%s, mock=%s)", role, mock)

    try:
        if mock and role == "ui":
            # D6: snapshot goes only to the connecting UI client, right after
            # hello_ack. Voice never gets it -- it's outside Voice's routing subset.
            await mock_engine.push_snapshot(send_fn)

        async for raw in ws:
            try:
                frame = json.loads(raw)
                msg = parse_ipc_message(frame)
            except (json.JSONDecodeError, IpcValidationError) as exc:
                await send_fn("error", {"code": "bad_frame", "message": str(exc), "recoverable": True})
                continue

            if msg["type"] == "user_msg":
                asyncio.create_task(_serialize_user_msg(msg, locks, send_fn, broadcast_fn, mock))
            elif mock and msg["type"] in _MOCK_DISPATCH:
                handler_name, needs_send = _MOCK_DISPATCH[msg["type"]]
                handler = getattr(mock_engine, handler_name)
                asyncio.create_task(handler(msg, send_fn if needs_send else broadcast_fn))
            # Other inbound types remain validated-but-unhandled outside mock
            # mode, per Phase 0 Step 4's original scope.
    finally:
        authenticated.pop(ws, None)


async def start(
    token: str | None = None, port: int = 0, auth_timeout: float = 5, mock: bool = False
) -> tuple[Server, str]:
    """Start the server in-process. Returns (server, token) for callers/tests
    that need the bound port without touching session.json."""
    token = token or secrets.token_urlsafe(32)
    locks: dict[str, asyncio.Lock] = {}
    authenticated: dict[ServerConnection, str] = {}  # connection -> role ("ui"/"voice")

    async def handler(ws: ServerConnection) -> None:
        await _connection_handler(ws, token, locks, auth_timeout, authenticated, mock)

    server = await websockets.asyncio.server.serve(handler, "127.0.0.1", port)
    return server, token


async def _run(mock: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="[brain] %(message)s")
    with single_instance_lock():
        server, token = await start(mock=mock)
        bound_port = server.sockets[0].getsockname()[1]
        write_session_file(bound_port, token)
        logger.info(
            "listening on 127.0.0.1:%d, session.json written to %s (mock=%s)", bound_port, SESSION_FILE, mock
        )
        await server.serve_forever()


def main() -> None:
    mock = "--mock" in sys.argv[1:]
    try:
        asyncio.run(_run(mock=mock))
    except BrainAlreadyRunning as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
