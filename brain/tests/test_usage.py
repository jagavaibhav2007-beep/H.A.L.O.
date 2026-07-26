"""Usage accounting (systemdesign/14 Track C).

The bug this pins: `usage_out["cost"]` used to be ASSIGNED, and one usage dict
is reused for every round of a multi-round tool turn -- so a turn was billed
for its final round only (measured 3.95x undercount on 6 rounds). Accumulation
is the whole point, so it is what gets asserted.

Plain asserts, no framework -- repo idiom.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain import llm

_ROUND = {
    "cost": 0.002,
    "prompt_tokens": 1500,
    "completion_tokens": 40,
    "prompt_tokens_details": {"cached_tokens": 1024},
    "completion_tokens_details": {"reasoning_tokens": 12},
}


def test_accumulates_across_rounds() -> None:
    """One usage_out dict, three rounds -> the SUM, not the last round."""
    before = llm.session_totals()
    out: dict = {}
    for _ in range(3):
        cost = llm._record_usage(_ROUND, out)
        assert cost == 0.002, cost  # returned per-call so the caller bills once

    assert abs(out["cost"] - 0.006) < 1e-9, out["cost"]
    assert out["prompt_tokens"] == 4500, out
    assert out["completion_tokens"] == 120, out
    assert out["cached_tokens"] == 3072, out
    assert out["reasoning_tokens"] == 36, out

    # Session totals move by the same amount, so background calls that pass no
    # usage_out still land somewhere (memory consolidation, doc_digest).
    after = llm.session_totals()
    assert abs((after["cost"] - before["cost"]) - 0.006) < 1e-9, (before, after)
    assert after["prompt_tokens"] - before["prompt_tokens"] == 4500, (before, after)


def test_records_without_usage_out() -> None:
    """usage_out=None must still reach the session totals -- a caller that
    forgets the out-param is exactly how _maybe_summarize went unbilled."""
    before = llm.session_totals()["prompt_tokens"]
    llm._record_usage(_ROUND, None)
    assert llm.session_totals()["prompt_tokens"] - before == 1500


def test_missing_and_null_fields_are_zero() -> None:
    """OpenRouter omits detail blocks on providers that don't cache, and can
    send an explicit null -- neither may raise or poison the totals."""
    out: dict = {}
    assert llm._record_usage({}, out) == 0.0
    assert out == dict.fromkeys(llm._USAGE_FIELDS, 0), out
    llm._record_usage({"cost": None, "prompt_tokens": None, "prompt_tokens_details": None}, out)
    assert out == dict.fromkeys(llm._USAGE_FIELDS, 0), out


def test_stub_zero_does_not_wipe_accumulation() -> None:
    """The HALO_LLM_STUB path calls _record_usage({"cost": 0.0}) once per round.
    Under the old assignment that zeroed the turn; it must only add zero."""
    out: dict = {}
    llm._record_usage(_ROUND, out)
    llm._record_usage({"cost": 0.0}, out)
    assert out["prompt_tokens"] == 1500, out
    assert abs(out["cost"] - 0.002) < 1e-9, out


if __name__ == "__main__":
    test_accumulates_across_rounds()
    test_records_without_usage_out()
    test_missing_and_null_fields_are_zero()
    test_stub_zero_does_not_wipe_accumulation()
    print("PASS -- usage accounting accumulates across rounds and never loses a call")
