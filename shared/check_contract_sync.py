#!/usr/bin/env python3
"""Fails (non-zero exit) if the TS and Python IPC contracts have drifted.

Both the Brain and the UI validate frames at runtime against their own
hand-mirrored copy of the contract. This diffs those two copies directly:
  - brain/brain/ipc/contract.py  (CONTRACT_SPEC, via direct import)
  - ui/src/ipc/contract.ts       (CONTRACT_SPEC, via `node`, which runs .ts natively)

Both expose the same {envelope, messages} dict shape, so the diff is
runtime-to-runtime -- the two copies that actually run must agree.

Usage: python shared/check_contract_sync.py
"""

from __future__ import annotations

import json
import difflib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TS_CONTRACT_PATH = ROOT / "ui" / "src" / "ipc" / "contract.ts"


def load_python_contract() -> dict:
    sys.path.insert(0, str(ROOT / "brain"))
    from brain.ipc.contract import CONTRACT_SPEC  # noqa: PLC0415

    return CONTRACT_SPEC


def load_python_version() -> str:
    sys.path.insert(0, str(ROOT / "brain"))
    from brain.ipc.contract import CONTRACT_VERSION  # noqa: PLC0415

    return CONTRACT_VERSION


def load_ts_contract() -> dict:
    rel_path = TS_CONTRACT_PATH.relative_to(ROOT).as_posix()
    script = (
        f"import {{ CONTRACT_SPEC }} from './{rel_path}';"
        "console.log(JSON.stringify(CONTRACT_SPEC));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("[check_contract_sync] failed to evaluate contract.ts via node:")
        print(result.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def load_ts_version() -> str:
    rel_path = TS_CONTRACT_PATH.relative_to(ROOT).as_posix()
    script = (
        f"import {{ CONTRACT_VERSION }} from './{rel_path}';"
        "console.log(CONTRACT_VERSION);"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("[check_contract_sync] failed to read CONTRACT_VERSION from contract.ts:")
        print(result.stderr)
        sys.exit(1)
    return result.stdout.strip()


def diff(label_a: str, a: dict, label_b: str, b: dict) -> list[str]:
    if a == b:
        return []
    details = difflib.unified_diff(
        json.dumps(a, indent=2, sort_keys=True).splitlines(),
        json.dumps(b, indent=2, sort_keys=True).splitlines(),
        fromfile=label_a,
        tofile=label_b,
        lineterm="",
    )
    return [
        f"complete schemas differ ({label_a} vs {label_b}):\n"
        + "\n".join(details)
    ]


def main() -> int:
    python_contract = load_python_contract()
    ts_contract = load_ts_contract()

    problems = [
        *diff("python", python_contract, "typescript", ts_contract),
    ]

    # The contract_version FIELD is compared by the structural diff above; this
    # cross-checks the version VALUE, a single hand-mirrored constant in each
    # copy that the structural comparison never sees. A drift there = spurious
    # incompatibility at runtime (one side refuses the other), so pin it here.
    versions = {
        "python": load_python_version(),
        "typescript": load_ts_version(),
    }
    if len(set(versions.values())) != 1:
        problems.append(f"CONTRACT_VERSION differs across copies: {versions}")

    if problems:
        print("[check_contract_sync] DRIFT DETECTED:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(
        f"[check_contract_sync] OK - {len(python_contract['messages'])} complete message "
        "schemas in sync across python/ts."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
