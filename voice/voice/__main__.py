"""Entry point for `python -m voice`.

Connect to the Brain over the same WS choke point the UI uses, authenticate
with a hello frame, then sit idle logging a heartbeat. On disconnect it
reconnects in-process (re-reading session.json fresh for the Brain's new port)
rather than exiting, so a Brain crash-loop no longer takes Voice down for the
session (B4; see _reconnect_loop). Origin: Phase 0 Step 5 — the three-process
topology and Voice authenticating like any other client.

No wake word, no audio capture, no STT/TTS yet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import suppress
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


# B4: in-process reconnect ladder, mirroring the UI's useHaloConnection. Voice
# is a supervised sidecar, but a single `run()` that exits on the first
# disconnect meant a ~40s Brain crash-loop killed Voice for the rest of the
# session (the supervisor's own ladder can exhaust). With this loop, process
# exit means real failure only.
_BACKOFF_SECS = (1, 5, 30)  # then repeats 30s, like the Rust supervisor's ladder


def _backoff_delay(attempt: int) -> float:
    return _BACKOFF_SECS[min(attempt, len(_BACKOFF_SECS) - 1)]


async def _sleep(delay: float, stop: asyncio.Event | None) -> None:
    """Back off, but wake immediately on a shutdown request."""
    if stop is None:
        await asyncio.sleep(delay)
        return
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=delay)


async def _reconnect_loop(
    stop: asyncio.Event | None = None,
    *,
    max_attempts: int | None = None,
    session_file: Path = SESSION_FILE,
) -> None:
    """Reconnect forever until `stop` is set (or `max_attempts`, for tests).

    session.json is re-read FRESH every attempt on purpose: the Brain binds a
    new ephemeral port and rewrites the file on every (re)start, so a cached
    port/token would dial a dead socket forever (mem/Gotchas.md). A connection
    that authenticated — even if it later dropped — resets the ladder so the
    next reconnect is fast; a never-authenticated attempt (brain down, refused,
    or a stale token that the next fresh read will fix) advances the backoff."""
    attempt = 0
    tries = 0
    while stop is None or not stop.is_set():
        if max_attempts is not None and tries >= max_attempts:
            return
        tries += 1
        try:
            port, token = _read_session(session_file)
        except (OSError, KeyError, TypeError, ValueError):
            logger.info("session.json unavailable at %s -- is brain running? retrying", session_file)
            await _sleep(_backoff_delay(attempt), stop)
            attempt += 1
            continue
        authed = asyncio.Event()
        try:
            await run(f"ws://127.0.0.1:{port}", token, authenticated=authed)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            logger.info("connection to brain failed: %s", exc)
        attempt = 0 if authed.is_set() else attempt + 1
        await _sleep(_backoff_delay(attempt), stop)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[voice] %(message)s")
    with suppress(KeyboardInterrupt):
        asyncio.run(_reconnect_loop())
    logger.info("Halo Voice sidecar exiting cleanly.")


if __name__ == "__main__":
    main()
