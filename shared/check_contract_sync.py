#!/usr/bin/env python3
"""Fails (non-zero exit) if the TS and Python IPC contracts have drifted
from each other or from shared/ipc-contract.json.

Compares the complete runtime schema across all three:
  - shared/ipc-contract.json      (schema, authoritative)
  - ui/src/ipc/contract.ts        (via `node`, which runs .ts natively)
  - brain/brain/ipc/contract.py   (via direct import)

...plus contract.py's TypedDicts, which are a FOURTH mirror the structural
diff never sees (see check_python_typeddicts).

Usage: python shared/check_contract_sync.py
"""

from __future__ import annotations

import json
import difflib
import re
import subprocess
import sys
import typing
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


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def check_python_typeddicts(schema: dict) -> list[str]:
    """contract.py declares each message TWICE -- once as a TypedDict and once
    in CONTRACT_SPEC -- and only the second copy is compared above. That is how
    SpendUpdateMsg lost session_tokens/last_turn_tokens while every gate stayed
    green. Compare each IpcMessage union member's payload fields, and whether
    each is required, against the authoritative schema.

    # ponytail: field NAMES and required-ness only. Mapping a Python annotation
    # to the schema's "string"/"integer"/enum vocabulary is the real machinery,
    # and the drift that actually happens is a missing field, not a retyped
    # one. Compare types too only if a retype ever ships unnoticed.
    """
    sys.path.insert(0, str(ROOT / "brain"))
    from brain.ipc import contract as py  # noqa: PLC0415

    envelope = set(schema["envelope"]["fields"])
    problems: list[str] = []
    matched: set[str] = set()
    for cls in typing.get_args(py.IpcMessage):
        # UserMsg -> "user"/"user_msg"; every other class drops a bare "Msg".
        base = _snake(cls.__name__.removesuffix("Msg"))
        name = base if base in schema["messages"] else f"{base}_msg"
        spec = schema["messages"].get(name)
        if spec is None:
            problems.append(f"TypedDict {cls.__name__} matches no message type in the schema")
            continue
        matched.add(name)
        hints = typing.get_type_hints(cls, include_extras=True)
        actual = {
            key: typing.get_origin(hint) is not typing.NotRequired
            for key, hint in hints.items()
            if key not in envelope
        }
        expected = {key: key in spec["required"] for key in spec["fields"]}
        if actual != expected:
            problems.append(
                f'"{name}": TypedDict {cls.__name__} declares '
                f"{sorted(actual.items())} but the schema declares "
                f"{sorted(expected.items())} (name, required?)"
            )
    for name in sorted(set(schema["messages"]) - matched):
        problems.append(f'"{name}": schema message has no TypedDict in contract.py')
    return problems


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
        *check_python_typeddicts(schema),
    ]

    # The contract_version FIELD is compared by the structural diff above; this
    # cross-checks the version VALUE, a single hand-mirrored constant in each
    # copy that the structural comparison never sees. A drift there = spurious
    # incompatibility at runtime (one side refuses the other), so pin it here.
    schema_version = json.loads(SCHEMA_PATH.read_text(encoding="utf-8")).get("version")
    versions = {
        "schema": schema_version,
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
        f"[check_contract_sync] OK - {len(schema['messages'])} complete message schemas "
        "in sync across schema/ts/python, including python's TypedDicts."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
