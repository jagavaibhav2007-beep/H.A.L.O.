"""Runnable self-check for managed Lane-1 command execution."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-commands-")
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")

from brain import secrets_store, store
from brain.commanding import (
    PolicyRefusal,
    SecretRedactor,
    analyze,
    normalize,
    redacted_args,
    run_managed,
)
from brain.task_runtime import TaskFailed, TaskStopped

ROOT = Path(tempfile.mkdtemp(prefix="halo-command-root-")).resolve()
OUT = Path(tempfile.mkdtemp(prefix="halo-command-out-")).resolve()
store.connect()
store.set_setting("project_roots", [str(ROOT)])


class Context:
    def __init__(self, task_id: str = "command-test") -> None:
        self.task_id = task_id
        self.cancelled = asyncio.Event()
        self.logs: list[str] = []

    async def log(self, text: str) -> None:
        self.logs.append(text)


def _python(_name: str) -> str:
    return sys.executable


def check_normalization_and_policy() -> None:
    spec = normalize("command_run", {
        "executable": sys.executable,
        "args": ["-c", "print('a & b')", r"C:\Program Files\x"],
        "cwd": str(ROOT),
        "purpose": "print literal arguments",
        "timeout_seconds": 30,
        "expected_artifacts": [],
    })
    assert spec.args == ("-c", "print('a & b')", r"C:\Program Files\x")
    assert analyze(spec, [ROOT]).tier == 3  # opaque inline Python

    outside = normalize("command_run", {
        "executable": sys.executable, "args": ["--version"], "cwd": str(OUT),
        "purpose": "check Python", "expected_artifacts": [],
    })
    assert analyze(outside, [ROOT]).tier == 3

    script = normalize("script_run", {
        "language": "python", "source": "print('ok')", "args": [],
        "cwd": str(ROOT), "purpose": "run generated logic", "expected_artifacts": [],
    }, which=_python)
    assert analyze(script, [ROOT]).tier == 3
    assert "print('ok')" not in json.dumps(redacted_args(script))
    assert redacted_args(script)["source_sha256"] == script.source_sha256

    hidden = normalize("command_run", {
        "executable": os.environ.get("COMSPEC", "cmd.exe"), "args": ["/c", "echo hidden"],
        "cwd": str(ROOT), "purpose": "nested shell", "expected_artifacts": [],
    })
    try:
        analyze(hidden, [ROOT])
        raise AssertionError("cmd /c was accepted")
    except PolicyRefusal:
        pass
    print("[check 1] argv stays structured; scripts/outside roots are Tier 3; opaque shells refuse: OK")


def check_chunk_safe_redaction() -> None:
    redactor = SecretRedactor(("top-secret-value",))
    visible = redactor.feed("prefix top-sec") + redactor.feed("ret-value suffix") + redactor.finish()
    assert "top-secret-value" not in visible
    assert "[REDACTED]" in visible
    print("[check 2] secrets split across output chunks are redacted: OK")


async def check_script_pdf_and_false_success() -> None:
    target = ROOT / "generated report.pdf"
    source = (
        "from pathlib import Path\n"
        "from pypdf import PdfWriter\n"
        "p = Path(__import__('sys').argv[1])\n"
        "w = PdfWriter(); w.add_blank_page(width=72, height=72); w.write(p)\n"
        "print(p)\n"
    )
    spec = normalize("script_run", {
        "language": "python", "source": source, "args": [str(target)],
        "cwd": str(ROOT), "purpose": "create a PDF",
        "expected_artifacts": [{"path": str(target), "kind": "pdf", "required": True, "overwrite": False}],
    }, which=_python)
    result = await run_managed(spec, analyze(spec, [ROOT]), Context())
    assert result["exit_code"] == 0 and result["artifacts"][0]["status"] == "valid", result
    assert target.read_bytes().startswith(b"%PDF-")

    missing = ROOT / "missing.pdf"
    no_artifact = normalize("script_run", {
        "language": "python", "source": "print('done')", "args": [],
        "cwd": str(ROOT), "purpose": "claim a missing artifact",
        "expected_artifacts": [{"path": str(missing), "kind": "pdf", "required": True, "overwrite": False}],
    }, which=_python)
    try:
        await run_managed(no_artifact, analyze(no_artifact, [ROOT]), Context("missing"))
        raise AssertionError("exit zero with a missing artifact succeeded")
    except TaskFailed as exc:
        assert exc.result and exc.result["artifacts"][0]["status"] == "missing"
    print("[check 3] generated Python creates and verifies a PDF; missing artifact defeats exit zero: OK")


async def check_secret_environment_and_stop() -> None:
    secrets_store._backend_set("command-test-secret", "never-print-this")
    secret = normalize("script_run", {
        "language": "python",
        "source": "import os; print(os.environ['TEST_SECRET'])",
        "args": [], "cwd": str(ROOT), "purpose": "use a secret",
        "secret_env": {"TEST_SECRET": "command-test-secret"},
        "expected_artifacts": [],
    }, which=_python)
    ctx = Context("secret")
    result = await run_managed(secret, analyze(secret, [ROOT]), ctx)
    dump = json.dumps(result) + "".join(ctx.logs)
    assert "never-print-this" not in dump and "[REDACTED]" in dump

    slow = normalize("script_run", {
        "language": "python", "source": "import time; time.sleep(30)", "args": [],
        "cwd": str(ROOT), "purpose": "test cancellation", "expected_artifacts": [],
    }, which=_python)
    stop_ctx = Context("stop")
    running = asyncio.create_task(run_managed(slow, analyze(slow, [ROOT]), stop_ctx))
    await asyncio.sleep(0.2)
    started = time.monotonic()
    stop_ctx.cancelled.set()
    try:
        await running
        raise AssertionError("stopped command returned success")
    except TaskStopped:
        assert time.monotonic() - started < 2.0
    print("[check 4] secret env is redacted and Stop terminates a running command under two seconds: OK")


async def main() -> None:
    check_normalization_and_policy()
    check_chunk_safe_redaction()
    await check_script_pdf_and_false_success()
    await check_secret_environment_and_stop()
    print("[brain.commands] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
