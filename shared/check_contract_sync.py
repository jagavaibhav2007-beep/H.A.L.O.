#!/usr/bin/env python3
"""Fails (non-zero exit) if the TS and Python IPC contracts have drifted
from each other or from shared/ipc-contract.json.

Compares message names + required-field lists across all three:
  - shared/ipc-contract.json      (schema, authoritative)
  - ui/src/ipc/contract.ts        (via `node`, which runs .ts natively)
  - brain/brain/ipc/contract.py   (via direct import)

Usage: python shared/check_contract_sync.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "shared" / "ipc-contract.json"
TS_CONTRACT_PATH = ROOT / "ui" / "src" / "ipc" / "contract.ts"


def load_schema() -> dict[str, list[str]]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return {name: spec["required"] for name, spec in schema["messages"].items()}


def load_python_contract() -> dict[str, list[str]]:
    sys.path.insert(0, str(ROOT / "brain"))
    from brain.ipc.contract import REQUIRED_FIELDS  # noqa: PLC0415

    return {k: list(v) for k, v in REQUIRED_FIELDS.items()}


def load_ts_contract() -> dict[str, list[str]]:
    rel_path = TS_CONTRACT_PATH.relative_to(ROOT).as_posix()
    script = (
        f"import {{ REQUIRED_FIELDS }} from './{rel_path}';"
        "console.log(JSON.stringify(REQUIRED_FIELDS));"
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


def diff(label_a: str, a: dict[str, list[str]], label_b: str, b: dict[str, list[str]]) -> list[str]:
    problems = []
    names_a, names_b = set(a), set(b)
    for missing in names_a - names_b:
        problems.append(f'"{missing}" is in {label_a} but missing from {label_b}')
    for missing in names_b - names_a:
        problems.append(f'"{missing}" is in {label_b} but missing from {label_a}')
    for name in names_a & names_b:
        if set(a[name]) != set(b[name]):
            problems.append(
                f'"{name}" required fields differ: {label_a}={sorted(a[name])} '
                f"vs {label_b}={sorted(b[name])}"
            )
    return problems


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

    print(f"[check_contract_sync] OK - {len(schema)} message types in sync across schema/ts/python.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
