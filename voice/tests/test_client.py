"""Runnable self-check for voice/__main__.py (Phase 0 Step 5).

No test framework -- plain asyncio + assert, mirrors brain/tests/test_server.py.
Run with:
    python voice/tests/test_client.py

Requires `pip install -e ../brain` from voice/'s environment (see DEVELOPMENT.md)
so both `brain.server` (to stand up an in-process Brain) and `voice` are importable.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "brain"))

from brain.server import start

from voice.__main__ import _parse_hello_ack, _read_session, run


async def check_good_token_connects(port: int, token: str) -> None:
    uri = f"ws://127.0.0.1:{port}"
    # Brain never pushes a frame in Phase 0 idle state, so run() would sit in
    # its heartbeat wait forever -- bound it and treat the timeout itself as
    # "stayed connected", same as the acceptance criteria asks for.
    try:
        await asyncio.wait_for(run(uri, token), timeout=1)
    except asyncio.TimeoutError:
        pass
    print("[check 1] good token -> connects and idles without error: OK")


async def check_bad_token_dropped_cleanly(port: int) -> None:
    uri = f"ws://127.0.0.1:{port}"
    # Bad token: brain drops the connection right after hello, so run()
    # should return on its own (ConnectionClosed handled internally) well
    # before any timeout -- no exception should escape.
    await asyncio.wait_for(run(uri, "not-the-real-token"), timeout=2)
    print("[check 2] bad token -> connection dropped, run() returns cleanly: OK")


def check_invalid_session_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.json"
        for data in ({"port": "1234", "token": "token"}, {"port": 1234, "token": ""}):
            path.write_text(json.dumps(data), encoding="utf-8")
            try:
                _read_session(path)
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected invalid session data to be rejected: {data}")
    print("[check 3] malformed session values are rejected: OK")


def check_non_ack_rejected() -> None:
    try:
        _parse_hello_ack(json.dumps({"type": "token", "id": "x", "ts": "x", "text": "x", "conversation_id": "x"}))
    except ValueError:
        pass
    else:
        raise AssertionError("Voice accepted a non-ack frame as authentication")
    print("[check 4] non-ack authentication response is rejected: OK")


async def main() -> None:
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        await check_good_token_connects(port, token)
        await check_bad_token_dropped_cleanly(port)
    finally:
        server.close()
        await server.wait_closed()
    check_invalid_session_rejected()
    check_non_ack_rejected()
    print("[voice.client] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
