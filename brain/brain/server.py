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
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.exceptions import ConnectionClosed

from brain.ipc.contract import IpcValidationError, parse_ipc_message

logger = logging.getLogger("brain.server")

SESSION_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Halo"
SESSION_FILE = SESSION_DIR / "session.json"
INSTANCE_LOCK_FILE = SESSION_DIR / "brain.lock"


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


async def _handle_user_msg(ws: ServerConnection, msg: dict, locks: dict[str, asyncio.Lock]) -> None:
    conversation_id = msg["conversation_id"]
    async with locks.setdefault(conversation_id, asyncio.Lock()):
        try:
            await _send(ws, "token", {"text": f"echo: {msg['text']}", "conversation_id": conversation_id})
            await _send(ws, "done", {"conversation_id": conversation_id})
        except ConnectionClosed:
            return
        except Exception as exc:  # noqa: BLE001 - turn must never drop silently
            logger.exception("turn failed for conversation_id=%s", conversation_id)
            try:
                await _send(
                    ws,
                    "error",
                    {"code": "turn_failed", "message": str(exc), "recoverable": True, "conversation_id": conversation_id},
                )
            except ConnectionClosed:
                return


async def _auth(ws: ServerConnection, token: str, timeout: float) -> bool:
    """First frame must be {type: hello, token}. Returns True if authenticated."""
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout)
        frame = json.loads(raw)
        parsed = parse_ipc_message(frame)
    except Exception:
        logger.info("dropping connection: invalid hello frame")
        return False

    if parsed.get("type") != "hello":
        logger.info("dropping connection: first frame was not hello")
        return False

    supplied = parsed.get("token")
    if not isinstance(supplied, str) or not secrets.compare_digest(supplied, token):
        logger.info("dropping connection: bad token")
        return False

    return True


async def _connection_handler(
    ws: ServerConnection,
    token: str,
    locks: dict[str, asyncio.Lock],
    auth_timeout: float,
) -> None:
    if not await _auth(ws, token, auth_timeout):
        await ws.close()
        return

    try:
        await _send(ws, "hello_ack", {})
    except ConnectionClosed:
        return
    logger.info("client authenticated")
    async for raw in ws:
        try:
            frame = json.loads(raw)
            msg = parse_ipc_message(frame)
        except (json.JSONDecodeError, IpcValidationError) as exc:
            await _send(ws, "error", {"code": "bad_frame", "message": str(exc), "recoverable": True})
            continue

        if msg["type"] == "user_msg":
            asyncio.create_task(_handle_user_msg(ws, msg, locks))
        # Other inbound types (interrupt, approval_response, ...) are out of
        # scope for Phase 0 Step 4 -- unhandled but validated, not dropped.


async def start(token: str | None = None, port: int = 0, auth_timeout: float = 5) -> tuple[Server, str]:
    """Start the server in-process. Returns (server, token) for callers/tests
    that need the bound port without touching session.json."""
    token = token or secrets.token_urlsafe(32)
    locks: dict[str, asyncio.Lock] = {}

    async def handler(ws: ServerConnection) -> None:
        await _connection_handler(ws, token, locks, auth_timeout)

    server = await websockets.asyncio.server.serve(handler, "127.0.0.1", port)
    return server, token


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="[brain] %(message)s")
    with single_instance_lock():
        server, token = await start()
        bound_port = server.sockets[0].getsockname()[1]
        write_session_file(bound_port, token)
        logger.info("listening on 127.0.0.1:%d, session.json written to %s", bound_port, SESSION_FILE)
        await server.serve_forever()


def main() -> None:
    try:
        asyncio.run(_run())
    except BrainAlreadyRunning as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
