#!/usr/bin/env python3
"""Fails (non-zero exit) if the TS and Python IPC contracts have drifted
from each other or from shared/ipc-contract.json.

Compares the complete runtime schema across all three:
  - shared/ipc-contract.json      (schema, authoritative)
  - ui/src/ipc/contract.ts        (via `node`, which runs .ts natively)
  - brain/brain/ipc/contract.py   (via direct import)

Usage: python shared/check_contract_sync.py
"""

from __future__ import annotations

import json
import difflib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "shared" / "ipc-contract.json"
TS_CONTRACT_PATH = ROOT / "ui" / "src" / "ipc" / "contract.ts"


def load_schema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return {"envelope": schema["envelope"], "messages": schema["messages"]}


def load_python_contract() -> dict:
    sys.path.insert(0, str(ROOT / "brain"))
    from brain.ipc.contract import CONTRACT_SPEC  # noqa: PLC0415

    return CONTRACT_SPEC


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
    schema = load_schema()
    python_contract = load_python_contract()
    ts_contract = load_ts_contract()

    problems = [
        *diff("schema", schema, "python", python_contract),
        *diff("schema", schema, "typescript", ts_contract),
    ]

    if problems:
        print("[check_contract_sync] DRIFT DETECTED:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(
        f"[check_contract_sync] OK - {len(schema['messages'])} complete message schemas "
        "in sync across schema/ts/python."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
