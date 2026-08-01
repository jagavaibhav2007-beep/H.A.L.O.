"""Entry point for `python -m voice`.

Phase 0, Step 5 of phase-0-plan.md: connect to the Brain over the same
WS choke point the UI will use, authenticate with a hello frame, then sit
idle logging a heartbeat until the Brain drops the connection.

No wake word, no audio capture, no STT/TTS yet -- this only proves the
three-process topology and that Voice authenticates like any other client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import websockets
from websockets.asyncio.client import connect

from brain.ipc.contract import parse_ipc_message

logger = logging.getLogger("voice")

# Mirrors brain/brain/server.py's SESSION_DIR/SESSION_FILE exactly so both
# processes agree on where session.json lives without importing brain.server
# (which would pull in the full server module just for two path constants).
SESSION_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Halo"
SESSION_FILE = SESSION_DIR / "session.json"

HEARTBEAT_SECS = 30
AUTH_TIMEOUT_SECS = 5


def _hello_frame(token: str) -> dict:
    frame = {
        "type": "hello",
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "token": token,
        "role": "voice",  # opt into the restricted routing subset (11-ipc-contract.md)
    }
    parse_ipc_message(frame)  # never send a frame that drifted from the contract
    return frame


def _parse_hello_ack(raw: str | bytes) -> dict:
    frame = parse_ipc_message(json.loads(raw))
    if frame["type"] != "hello_ack":
        raise ValueError(f"expected hello_ack, got {frame['type']}")
    return frame


async def run(uri: str, token: str, *, authenticated: asyncio.Event | None = None) -> None:
    """Connect, authenticate, then idle-heartbeat until the Brain disconnects."""
    async with connect(uri) as ws:
        await ws.send(json.dumps(_hello_frame(token)))
        logger.info("connected to brain at %s, hello sent", uri)
        try:
            _parse_hello_ack(await asyncio.wait_for(ws.recv(), timeout=AUTH_TIMEOUT_SECS))
        except (TimeoutError, ValueError, websockets.exceptions.ConnectionClosed):
            logger.info("brain authentication failed, exiting cleanly")
            return
        if authenticated is not None:
            authenticated.set()
        logger.info("brain authentication acknowledged")

        while True:
            try:
                await asyncio.wait_for(ws.recv(), timeout=HEARTBEAT_SECS)
                # ponytail: Phase 0 has no inbound message types voice acts on,
                # so any frame is just noise-logged. Dispatch by msg["type"]
                # once voice_state/mic frames actually mean something.
                logger.info("heartbeat: received frame from brain")
            except asyncio.TimeoutError:
                logger.info("heartbeat: idle")
            except websockets.exceptions.ConnectionClosed:
                logger.info("brain disconnected, exiting cleanly")
                return


def _read_session(session_file: Path = SESSION_FILE) -> tuple[int, str]:
    data = json.loads(session_file.read_text(encoding="utf-8"))
    port, token = data["port"], data["voice_token"]
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535:
        raise ValueError("session port must be an integer from 1 to 65535")
    if not isinstance(token, str) or not token:
        raise ValueError("session token must be a non-empty string")
    return port, token


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[voice] %(message)s")
    try:
        port, token = _read_session()
    except (OSError, KeyError, TypeError, ValueError):
        logger.error("could not read session.json at %s -- is brain running?", SESSION_FILE)
        return

    uri = f"ws://127.0.0.1:{port}"
    try:
        asyncio.run(run(uri, token))
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        logger.error("connection to brain failed: %s", exc)
    logger.info("Halo Voice sidecar exiting cleanly.")


if __name__ == "__main__":
    main()
