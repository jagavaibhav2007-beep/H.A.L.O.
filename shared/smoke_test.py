"""End-to-end smoke test for Phase 0 (phase-0-plan.md Step 8).

Locks the four Phase-0 exit criteria (phase-0-plan.md, "Phase exit criteria")
behind one repeatable, script-runnable check:

  1. A client sends `user_msg` over an authenticated WS; the stub Brain
     streams `token`(s) then `done`, keyed to the same conversation_id.
  2. Killing the Brain -> a client reconnects to the RESPAWNED Brain (new
     ephemeral port, rewritten session.json) and a subsequent message succeeds.
  3. A wrong/missing `hello` token connection is dropped by the Brain.
  4. The Voice sidecar connects and idles (authenticates through the same
     choke point).

SCOPE BOUNDARY -- read this before trusting a green run for more than it
proves:
  This test drives the WS-protocol contract with real in-process Brain
  servers (brain.server.start(port=0)). It does NOT drive the Tauri GUI and
  does NOT exercise actual OS-process supervision (spawn/kill/respawn) --
  a native WebView2 window and Rust process supervision can't be headlessly
  driven from here. Those are covered separately:
    - the backoff ladder is unit-tested in ui/src-tauri/src/supervisor.rs
      (`cargo test`, run from ui/src-tauri).
    - actual kill-Brain -> OS-respawn -> UI-window-reconnect was verified
      manually (see DEVELOPMENT.md).
  Criterion 2 here proves the PROTOCOL contract that makes that manual
  recovery correct: a client that re-reads session.json fresh (never caches
  a port) reaches whatever Brain is currently listening, mirroring
  ui/src/ipc/useHaloConnection.ts's connect().

Reuses assertion logic from brain/tests/test_server.py and
voice/tests/test_client.py rather than re-implementing it -- only criterion
2's kill-and-respawn-on-a-new-port sequence is new.

Requires `brain` importable, and for criterion 4, `voice` with
`pip install -e ../brain` done in voice's environment (same prerequisite as
voice/tests/test_client.py -- see DEVELOPMENT.md).

Run with:
    python shared/smoke_test.py
Exit code is 0 iff all four criteria pass (non-zero otherwise, for CI).
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Phase 2: the non-mock Brain runs the real graph. Point it at the offline
# deterministic LLM stub and a temp data dir so the smoke test stays
# network-free and never touches %LOCALAPPDATA%\Halo. (test_server sets the
# same env on import; set it here too so ordering never matters.)
os.environ["HALO_LLM_STUB"] = "1"
_TMP = tempfile.mkdtemp(prefix="halo-smoke-")
os.environ["LOCALAPPDATA"] = _TMP
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)  # don't leak on every run

ROOT = Path(__file__).resolve().parents[1]
# ponytail: brain/tests and voice/tests have no __init__.py (plain scripts,
# not packages) -- add their dirs to sys.path and import the modules
# directly, same way each test file already imports its own target module.
sys.path.insert(0, str(ROOT / "brain" / "tests"))
sys.path.insert(0, str(ROOT / "voice" / "tests"))

import test_server  # brain/tests/test_server.py -- reused for criteria 1 & 3
import test_client  # voice/tests/test_client.py -- reused for criterion 4

from brain.server import start, write_session_file  # noqa: E402 (path set above)


async def check_criterion_2_reconnect_new_port() -> None:
    """New logic (everything else in this file is reuse/aggregation).

    Start Brain A, prove it works, "kill" it (close the server), start
    Brain B on a *different* ephemeral port and rewrite session.json (what
    the respawned Brain's write_session_file does) -- then a client that
    re-reads session.json fresh (never the cached port_a/token_a) must reach
    B and complete a turn.
    """
    with tempfile.TemporaryDirectory() as tmp:
        session_file = Path(tmp) / "session.json"
        server_a, token_a = await start(port=0)
        port_a = server_a.sockets[0].getsockname()[1]
        write_session_file(port_a, token_a, session_file, voice_token=server_a.voice_token)
        await test_server.check_good_token_echo(port_a, token_a)

        server_a.close()
        await server_a.wait_closed()

        server_b, token_b = await start(port=0)
        try:
            port_b = server_b.sockets[0].getsockname()[1]
            assert port_b != port_a, "test invalid: new server bound the same port"
            write_session_file(port_b, token_b, session_file, voice_token=server_b.voice_token)
            session = json.loads(session_file.read_text(encoding="utf-8"))
            read_port, read_token = session["port"], session["token"]
            assert (read_port, read_token) == (port_b, token_b), "session.json not rewritten to new port/token"
            await test_server.check_good_token_echo(read_port, read_token)
        finally:
            server_b.close()
            await server_b.wait_closed()
    print(
        "[check 2] Brain killed -> respawned on new port -> fresh session.json "
        "read -> reconnect+turn succeeds: OK"
    )


async def main() -> int:
    print("=== Halo Phase 0 smoke test (WS-protocol scope; see module docstring) ===")
    ok = True

    server, token = await start()
    port = server.sockets[0].getsockname()[1]
    try:
        try:
            await test_server.check_good_token_echo(port, token)
            print("PASS -- criterion 1: authenticated user_msg -> token(s)+done")
        except AssertionError as exc:
            ok = False
            print(f"FAIL -- criterion 1: {exc}")

        try:
            await test_server.check_bad_token_dropped(port)
            print("PASS -- criterion 3: wrong/missing hello token is dropped")
        except AssertionError as exc:
            ok = False
            print(f"FAIL -- criterion 3: {exc}")

        try:
            await test_client.check_good_token_connects(port, server.voice_token)
            print("PASS -- criterion 4: voice sidecar authenticates and idles")
        except AssertionError as exc:
            ok = False
            print(f"FAIL -- criterion 4: {exc}")
    finally:
        server.close()
        await server.wait_closed()

    try:
        await check_criterion_2_reconnect_new_port()
        print("PASS -- criterion 2: kill Brain -> respawn on new port -> reconnect+turn succeeds")
    except AssertionError as exc:
        ok = False
        print(f"FAIL -- criterion 2: {exc}")

    print(f"=== SUMMARY: {'ALL 4 CRITERIA PASS' if ok else 'FAILURES ABOVE'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
