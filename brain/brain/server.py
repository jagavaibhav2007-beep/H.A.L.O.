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
from datetime import datetime, timezone
from pathlib import Path

import websockets
from websockets.asyncio.server import Server, ServerConnection

from brain.ipc.contract import IpcValidationError, parse_ipc_message

logger = logging.getLogger("brain.server")

SESSION_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Halo"
SESSION_FILE = SESSION_DIR / "session.json"


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


def write_session_file(port: int, token: str) -> None:
    """Write {port, token} atomically to %LOCALAPPDATA%\\Halo\\session.json.

    # ponytail: %LOCALAPPDATA% is already per-user on Windows, so this dir
    # isn't world-readable by other accounts. Tightening further (explicit
    # icacls ACL restricting even other processes under the same user) is
    # the upgrade path if that boundary ever needs to be stricter.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": port, "token": token}))
    tmp.replace(SESSION_FILE)  # atomic rename on both POSIX and Windows


class ConversationSerializer:
    """Serializes turns per conversation_id.

    # ponytail: locks are never evicted from this dict, so a long-lived
    # process accumulates one asyncio.Lock per conversation_id ever seen.
    # Fine for a Phase-0 skeleton; upgrade path is dropping the lock when
    # a conversation's `done`/close fires, once conversation lifecycle
    # tracking exists.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, conversation_id: str) -> asyncio.Lock:
        return self._locks.setdefault(conversation_id, asyncio.Lock())


async def _handle_user_msg(ws: ServerConnection, msg: dict, serializer: ConversationSerializer) -> None:
    conversation_id = msg["conversation_id"]
    lock = serializer.lock_for(conversation_id)
    async with lock:
        try:
            await asyncio.sleep(0)  # yield so a concurrent same-cid turn would interleave if unlocked
            await _send(ws, "token", {"text": f"echo: {msg['text']}", "conversation_id": conversation_id})
            await _send(ws, "done", {"conversation_id": conversation_id})
        except Exception as exc:  # noqa: BLE001 - turn must never drop silently
            logger.exception("turn failed for conversation_id=%s", conversation_id)
            await _send(
                ws,
                "error",
                {"code": "turn_failed", "message": str(exc), "recoverable": True, "conversation_id": conversation_id},
            )


async def _auth(ws: ServerConnection, token: str) -> bool:
    """First frame must be {type: hello, token}. Returns True if authenticated."""
    try:
        raw = await ws.recv()
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


async def _connection_handler(ws: ServerConnection, token: str, serializer: ConversationSerializer) -> None:
    if not await _auth(ws, token):
        await ws.close()
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
            asyncio.create_task(_handle_user_msg(ws, msg, serializer))
        # Other inbound types (interrupt, approval_response, ...) are out of
        # scope for Phase 0 Step 4 -- unhandled but validated, not dropped.


async def start(token: str | None = None, port: int = 0) -> tuple[Server, str]:
    """Start the server in-process. Returns (server, token) for callers/tests
    that need the bound port without touching session.json."""
    token = token or secrets.token_urlsafe(32)
    serializer = ConversationSerializer()

    async def handler(ws: ServerConnection) -> None:
        await _connection_handler(ws, token, serializer)

    server = await websockets.asyncio.server.serve(handler, "127.0.0.1", port)
    return server, token


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="[brain] %(message)s")
    server, token = await start()
    bound_port = server.sockets[0].getsockname()[1]
    write_session_file(bound_port, token)
    logger.info("listening on 127.0.0.1:%d, session.json written to %s", bound_port, SESSION_FILE)
    await server.serve_forever()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
