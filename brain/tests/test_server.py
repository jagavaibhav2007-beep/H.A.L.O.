"""Runnable self-check for brain/server.py (Phase 0 Steps 3-4).

No test framework -- plain asyncio + assert. Run with:
    python brain/tests/test_server.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets

from brain.server import start


def _frame(msg_type: str, **payload) -> dict:
    return {
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


async def _connect(port: int):
    return await websockets.connect(f"ws://127.0.0.1:{port}")


async def check_good_token_echo(port: int, token: str) -> None:
    ws = await _connect(port)
    try:
        await ws.send(json.dumps(_frame("hello", token=token)))
        conversation_id = "conv-1"
        await ws.send(json.dumps(_frame("user_msg", text="hi", conversation_id=conversation_id, source="ui")))

        got_token = json.loads(await ws.recv())
        assert got_token["type"] == "token", got_token
        assert got_token["conversation_id"] == conversation_id

        got_done = json.loads(await ws.recv())
        assert got_done["type"] == "done", got_done
        assert got_done["conversation_id"] == conversation_id
    finally:
        await ws.close()
    print("[check 1] good token -> token+done with matching conversation_id: OK")


async def check_bad_token_dropped(port: int) -> None:
    ws = await _connect(port)
    await ws.send(json.dumps(_frame("hello", token="not-the-real-token")))
    try:
        await asyncio.wait_for(ws.recv(), timeout=2)
        raise AssertionError("expected connection to be dropped, but got a frame")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await ws.close()
    print("[check 2] bad token -> connection dropped before any other frame: OK")


async def check_malformed_frame_rejected(port: int, token: str) -> None:
    ws = await _connect(port)
    try:
        await ws.send(json.dumps(_frame("hello", token=token)))
        await ws.send(json.dumps({"type": "not_a_real_type", "id": "x", "ts": "x"}))
        reply = json.loads(await ws.recv())
        assert reply["type"] == "error", reply
    finally:
        await ws.close()
    print("[check 3] malformed frame -> error frame, connection stays up: OK")


async def check_conversation_order(port: int, token: str) -> None:
    ws = await _connect(port)
    try:
        await ws.send(json.dumps(_frame("hello", token=token)))
        cid = "conv-order"
        await ws.send(json.dumps(_frame("user_msg", text="first", conversation_id=cid, source="ui")))
        await ws.send(json.dumps(_frame("user_msg", text="second", conversation_id=cid, source="ui")))

        texts = []
        for _ in range(4):  # token,done,token,done
            frame = json.loads(await ws.recv())
            if frame["type"] == "token":
                texts.append(frame["text"])
        assert texts == ["echo: first", "echo: second"], texts
    finally:
        await ws.close()
    print("[check 4] two messages to one conversation handled in arrival order: OK")


async def main() -> None:
    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        await check_good_token_echo(port, token)
        await check_bad_token_dropped(port)
        await check_malformed_frame_rejected(port, token)
        await check_conversation_order(port, token)
    finally:
        server.close()
        await server.wait_closed()
    print("[brain.server] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
