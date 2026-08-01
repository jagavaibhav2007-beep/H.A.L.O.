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
import os
import sys
import tempfile
from pathlib import Path

from websockets.asyncio.server import serve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "brain"))

# Isolate before importing brain.server: start() runs the real (mock=False)
# Brain, whose belief-decay loop fires immediately and WRITES to the store at
# %LOCALAPPDATA%\Halo\halo.db. Without this redirect, running this documented
# test archives the developer's real beliefs (mirrors smoke_test.py's setup).
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="halo-voice-test-")
os.environ.setdefault("HALO_LLM_STUB", "1")

from brain.server import start

from voice.__main__ import _parse_hello_ack, _read_session, run


async def _assert_authenticates(uri: str, token: str, timeout: float = 1.0) -> None:
    """Prove ``run`` crossed the hello_ack boundary, then remained idle."""
    authenticated = asyncio.Event()
    task = asyncio.create_task(run(uri, token, authenticated=authenticated))
    try:
        try:
            await asyncio.wait_for(authenticated.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise AssertionError("Voice never received hello_ack") from exc
        assert not task.done(), "Voice exited immediately after authenticating instead of idling"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def check_good_token_connects(port: int, token: str) -> None:
    uri = f"ws://127.0.0.1:{port}"
    await _assert_authenticates(uri, token)
    print("[check 1] good token -> hello_ack observed, then client idles: OK")


async def check_blackhole_never_counts_as_authenticated() -> None:
    async def blackhole(ws) -> None:
        await ws.recv()  # accept Voice's hello, but deliberately never acknowledge it
        await asyncio.sleep(1)

    server = await serve(blackhole, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        try:
            await _assert_authenticates(f"ws://127.0.0.1:{port}", "unused", timeout=0.1)
        except AssertionError as exc:
            assert "never received hello_ack" in str(exc)
        else:
            raise AssertionError("a black-hole server was mistaken for authenticated Brain")
    finally:
        server.close()
        await server.wait_closed()
    print("[check 1b] black-hole server without hello_ack is rejected: OK")


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
        for data in (
            {"port": "1234", "voice_token": "token"},
            {"port": 1234, "voice_token": ""},
            {"port": 1234, "token": "ui-only"},
        ):
            path.write_text(json.dumps(data), encoding="utf-8")
            try:
                _read_session(path)
            except (KeyError, ValueError):
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
        await check_good_token_connects(port, server.voice_token)
        await check_bad_token_dropped_cleanly(port)
    finally:
        server.close()
        await server.wait_closed()
    await check_blackhole_never_counts_as_authenticated()
    check_invalid_session_rejected()
    check_non_ack_rejected()
    print("[voice.client] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
