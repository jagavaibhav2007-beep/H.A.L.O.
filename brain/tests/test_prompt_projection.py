"""Runnable self-check for graph._prompt_messages Layer-1 tool-result eviction.

No test framework -- plain assert. Run with:
    python brain/tests/test_prompt_projection.py

Pure projection only: never calls run_turn/_ensure_graph, so no aiosqlite saver
thread is opened and the process exits cleanly (Gotchas: non-daemon aiosqlite
thread hangs on exit).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Redirect any incidental store/checkpoint paths off %LOCALAPPDATA% on import;
# _prompt_messages itself touches neither, but keep the test hermetic.
os.environ.setdefault("HALO_LLM_STUB", "1")
os.environ.setdefault("LOCALAPPDATA", tempfile.mkdtemp(prefix="halo-test-projection-"))

from brain import graph  # noqa: E402

BIG = "x" * 6000
BIG2 = "y" * 7000


def _assistant_call(call_id: str, name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


def _tool(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _scenario() -> list[dict]:
    return [
        {"role": "user", "content": "read my files"},
        _assistant_call("call_a", "file_read", {"path": r"C:\Users\me\notes.md"}),  # 1
        _tool("call_a", BIG),                                                       # 2 stale, large -> stub
        {"role": "assistant", "content": "read the first file"},                    # 3
        _assistant_call("call_b", "run_readonly_cmd", {"cmd": "git status"}),       # 4
        _tool("call_b", "ok"),                                                      # 5 stale, small -> verbatim
        {"role": "assistant", "content": "ran the command"},                        # 6
        _assistant_call("call_c", "file_read", {"path": r"C:\Users\me\todo.md"}),   # 7 LAST assistant
        _tool("call_c", BIG2),                                                      # 8 tail, not answered -> verbatim
    ]


def check_stale_large_stubbed_pair_untouched() -> None:
    messages = _scenario()
    state = {"messages": messages}
    out = graph._prompt_messages(state)

    assert len(out) == len(messages), out
    stub = out[2]["content"]
    assert stub.startswith("[file_read result for "), stub
    assert "notes.md" in stub, stub
    assert f"({len(BIG)} chars)" in stub, stub          # length from original content
    assert "offset/limit" in stub, stub
    # The paired assistant tool_calls message is passed through untouched.
    assert out[1] == messages[1], out[1]
    assert out[1]["tool_calls"][0]["function"]["name"] == "file_read", out[1]
    # Non-mutation: the stored transcript still holds the full payload.
    assert state["messages"][2]["content"] == BIG, "state was mutated"
    print("[check 1] stale large tool result -> stub (path+length); paired assistant untouched: OK")


def check_tail_stays_verbatim() -> None:
    out = graph._prompt_messages({"messages": _scenario()})
    assert out[8]["content"] == BIG2, "not-yet-answered tail tool result was altered"
    print("[check 2] not-yet-answered tail tool result stays verbatim: OK")


def check_small_stays_verbatim() -> None:
    out = graph._prompt_messages({"messages": _scenario()})
    assert out[5]["content"] == "ok", out[5]
    print("[check 3] small stale tool result stays verbatim: OK")


def check_stub_is_stable() -> None:
    a = graph._prompt_messages({"messages": _scenario()})[2]["content"]
    b = graph._prompt_messages({"messages": _scenario()})[2]["content"]
    assert a == b, (a, b)
    print("[check 4] stub is byte-stable across calls: OK")


def check_summary_prepended_and_stub_within_live() -> None:
    # First two messages fall before dropped_before; the summary stands in for
    # them. The stale large tool result lives inside the surviving span.
    messages = [
        {"role": "user", "content": "old q"},          # 0 dropped
        {"role": "assistant", "content": "old a"},      # 1 dropped
        _assistant_call("call_a", "file_read", {"path": r"C:\x\big.md"}),  # 2
        _tool("call_a", BIG),                           # 3 stale, large -> stub
        {"role": "assistant", "content": "answered"},   # 4 LAST assistant
        _tool("call_a", BIG2),                          # 5 tail -> verbatim
    ]
    out = graph._prompt_messages(
        {"messages": messages, "summary": "earlier: user asked things", "dropped_before": 2}
    )
    assert out[0]["role"] == "user" and "Untrusted Halo context" in out[0]["content"], out[0]
    assert "earlier: user asked things" in out[0]["content"], out[0]
    # live span begins after the summary; index +1 relative to `messages[2:]`.
    stub = out[2]["content"]  # summary(0), assistant_call(1), tool(2)
    assert stub.startswith("[file_read result for ") and "big.md" in stub, stub
    assert out[-1]["content"] == BIG2, "tail within live span was stubbed"
    print("[check 5] summary is untrusted user context; stubs apply within the live span: OK")


def main() -> None:
    check_stale_large_stubbed_pair_untouched()
    check_tail_stays_verbatim()
    check_small_stays_verbatim()
    check_stub_is_stable()
    check_summary_prepended_and_stub_within_live()
    print("[graph._prompt_messages] self-check OK")


if __name__ == "__main__":
    main()
