"""Runnable self-check for managed Lane-1 command execution."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-commands-")
os.environ["LOCALAPPDATA"] = _TMP
os.environ["HALO_KEYRING_DIR"] = str(Path(_TMP) / "keys")
os.environ["HALO_CHECKPOINT_DB"] = str(Path(_TMP) / "checkpoints.db")
os.environ["HALO_LLM_STUB"] = "1"

from brain import secrets_store, store
import brain.commanding as commanding
from brain.commanding import (
    ArtifactBusy,
    PolicyRefusal,
    SecretRedactor,
    analyze,
    matches_user_intent,
    normalize,
    recheck_identity,
    redacted_args,
    run_managed,
    validate_secrets,
)
from brain.task_runtime import TaskFailed, TaskStopped

ROOT = Path(tempfile.mkdtemp(prefix="halo-command-root-")).resolve()
OUT = Path(tempfile.mkdtemp(prefix="halo-command-out-")).resolve()
store._embed_failed = True  # offline self-check: never download an unrelated embedding model
store.connect()
store.set_setting("project_roots", [str(ROOT)])


class Context:
    def __init__(self, task_id: str = "command-test") -> None:
        self.task_id = task_id
        self.tier = 3
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
    version = normalize("command_run", {
        "executable": sys.executable, "args": ["--version"], "cwd": str(ROOT),
        "purpose": "check version", "expected_artifacts": [],
    })
    assert analyze(version, [ROOT]).tier == 1
    version_env = normalize("command_run", {
        "executable": sys.executable, "args": ["--version"], "cwd": str(ROOT),
        "purpose": "check version with environment", "env": {"PYTHONPATH": str(OUT)},
        "expected_artifacts": [],
    })
    assert analyze(version_env, [ROOT]).tier == 3
    changed_timeout = normalize("command_run", {
        "executable": sys.executable, "args": ["--version"], "cwd": str(ROOT),
        "purpose": "check version", "timeout_seconds": 301, "expected_artifacts": [],
    })
    assert version.fingerprint != changed_timeout.fingerprint
    project_python = normalize("command_run", {
        "executable": sys.executable, "args": ["tests/test_one.py"], "cwd": str(ROOT),
        "purpose": "run project tests", "expected_artifacts": [],
    })
    assert analyze(project_python, [ROOT]).tier == 2
    assert matches_user_intent(project_python, "run the tests in this project")
    assert not matches_user_intent(project_python, "run an unrelated backup")
    outside_script = normalize("command_run", {
        "executable": sys.executable, "args": [str(OUT / "outside.py")], "cwd": str(ROOT),
        "purpose": "run outside script", "expected_artifacts": [],
    })
    assert analyze(outside_script, [ROOT]).tier == 3
    script_outside_arg = normalize("command_run", {
        "executable": sys.executable, "args": ["tests/test_one.py", str(OUT / "input.txt")],
        "cwd": str(ROOT), "purpose": "run project tests with outside input", "expected_artifacts": [],
    })
    assert analyze(script_outside_arg, [ROOT]).tier == 3
    outside_pytest = normalize("command_run", {
        "executable": sys.executable, "args": ["-m", "pytest", str(OUT)], "cwd": str(ROOT),
        "purpose": "run outside tests", "expected_artifacts": [],
    })
    assert analyze(outside_pytest, [ROOT]).tier == 3
    outside_pytest_flag = normalize("command_run", {
        "executable": sys.executable, "args": ["-m", "pytest", f"--basetemp={OUT}"], "cwd": str(ROOT),
        "purpose": "run tests with outside temp", "expected_artifacts": [],
    })
    assert analyze(outside_pytest_flag, [ROOT]).tier == 3
    git = __import__("shutil").which("git")
    if git:
        git_spec = normalize("command_run", {
            "executable": git, "args": ["diff", "--", str(OUT / "outside.txt")],
            "cwd": str(ROOT), "purpose": "inspect outside path", "expected_artifacts": [],
        })
        assert analyze(git_spec, [ROOT]).tier == 3
        safe_git = normalize("command_run", {
            "executable": git, "args": ["status"], "cwd": str(ROOT),
            "purpose": "inspect repository", "expected_artifacts": [],
        })
        git_decision = analyze(safe_git, [ROOT])
        assert git_decision.tier == 1 and "core.hooksPath=" in " ".join(git_decision.effective_args or ())
        git_oneline = normalize("command_run", {
            "executable": git, "args": ["log", "--oneline"], "cwd": str(ROOT),
            "purpose": "inspect repository history", "expected_artifacts": [],
        })
        assert analyze(git_oneline, [ROOT]).tier == 1
        git_branch = normalize("command_run", {
            "executable": git, "args": ["branch", "new-branch"], "cwd": str(ROOT),
            "purpose": "create branch", "expected_artifacts": [],
        })
        assert analyze(git_branch, [ROOT]).tier == 3
        git_clean = normalize("command_run", {
            "executable": git, "args": ["clean", "-fdx"], "cwd": str(ROOT),
            "purpose": "clean repository", "expected_artifacts": [],
        })
        clean_decision = analyze(git_clean, [ROOT])
        assert clean_decision.tier == 3 and clean_decision.destructive
        git_chdir = normalize("command_run", {
            "executable": git, "args": ["-C", str(OUT), "status"], "cwd": str(ROOT),
            "purpose": "inspect outside repository", "expected_artifacts": [],
        })
        assert analyze(git_chdir, [ROOT]).tier == 3
        git_path_flag = normalize("command_run", {
            "executable": git, "args": ["status", f"--pathspec-from-file={OUT / 'paths.txt'}"],
            "cwd": str(ROOT), "purpose": "inspect outside path list", "expected_artifacts": [],
        })
        assert analyze(git_path_flag, [ROOT]).tier == 3
    fake_git = ROOT / "git.exe"
    fake_git.write_bytes(b"not the discovered git")
    lookalike = normalize("command_run", {
        "executable": str(fake_git), "args": ["status"], "cwd": str(ROOT),
        "purpose": "inspect lookalike", "expected_artifacts": [],
    })
    assert analyze(lookalike, [ROOT]).tier == 3

    overwrite_script = normalize("script_run", {
        "language": "python", "source": "print('x')", "args": [], "cwd": str(ROOT),
        "purpose": "overwrite output", "expected_artifacts": [
            {"path": str(ROOT / "overwrite.pdf"), "kind": "pdf", "overwrite": True}
        ],
    }, which=_python)
    overwrite_decision = analyze(overwrite_script, [ROOT])
    assert overwrite_decision.tier == 3 and overwrite_decision.destructive
    for bad in (
        {"executable": sys.executable, "args": ["-m", "pip", "install", "x"]},
        {"executable": sys.executable, "args": ["@hidden.rsp"]},
    ):
        shaped = {**bad, "cwd": str(ROOT), "purpose": "bad shape", "expected_artifacts": []}
        decision = analyze(normalize("command_run", shaped), [ROOT])
        assert decision.tier == 3

    base = {"executable": sys.executable, "args": [], "cwd": str(ROOT),
            "purpose": "validate", "expected_artifacts": []}
    for change in (
        {"purpose": "x" * 513}, {"args": ["x" * 32769]},
        {"env": {"BAD-NAME": "x"}}, {"env": {"API_KEY": "literal"}},
        {"env": {"AUTH": "literal"}}, {"args": ["access_token=literal-value"]},
        {"timeout_seconds": 0},
        {"expected_artifacts": [{"path": "x", "kind": "unknown"}]},
        {"expected_artifacts": [{"path": "same"}, {"path": "same"}]},
    ):
        try:
            normalize("command_run", base | change)
            raise AssertionError(f"invalid request accepted: {change.keys()}")
        except ValueError:
            pass
    try:
        normalize("script_run", {
            "language": "python", "source": "API_KEY = 'sk-obvious-literal-value'", "args": [],
            "cwd": str(ROOT), "purpose": "test source scanning", "expected_artifacts": [],
        }, which=_python)
        raise AssertionError("obvious literal secret in generated source was accepted")
    except ValueError:
        pass
    recheck_identity(version)
    swapped = ROOT / "swapped-cwd"
    swapped.mkdir()
    swap_spec = normalize("command_run", {
        "executable": sys.executable, "args": ["--version"], "cwd": str(swapped),
        "purpose": "check cwd identity", "expected_artifacts": [],
    })
    moved = ROOT / "original-cwd"
    swapped.rename(moved)
    swapped.mkdir()
    try:
        recheck_identity(swap_spec)
        raise AssertionError("replaced cwd kept its approval")
    except PolicyRefusal:
        pass
    print("[check 1] argv stays structured; scripts/outside roots are Tier 3; opaque shells refuse: OK")


def check_chunk_safe_redaction() -> None:
    secret = "top-secret-value"
    for split in range(len(secret) + 1):
        redactor = SecretRedactor((secret,))
        visible = redactor.feed("prefix " + secret[:split])
        visible += redactor.feed(secret[split:] + " suffix") + redactor.finish()
        assert secret not in visible and "[REDACTED]" in visible, split
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
    assert len(result["artifacts"][0]["sha256"]) == 64 and result["artifacts"][0]["mtime_ns"] > 0
    assert target.read_bytes().startswith(b"%PDF-")
    assert not (Path(_TMP) / "Halo" / "tasks" / "command-test").exists()

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


async def check_direct_command_profiles() -> None:
    import shutil
    npm = shutil.which("npm")
    if npm:
        version = normalize("command_run", {
            "executable": "npm", "args": ["--version"], "cwd": str(ROOT),
            "purpose": "check npm version", "expected_artifacts": [],
        })
        assert analyze(version, [ROOT]).tier == 1
        result = await run_managed(version, analyze(version, [ROOT]), Context("npm-version"))
        assert result["exit_code"] == 0 and result["stdout"].strip()
        npm_test = normalize("command_run", {
            "executable": "npm", "args": ["test", "--", "--run"], "cwd": str(ROOT),
            "purpose": "run project tests", "expected_artifacts": [],
        })
        npm_exec = normalize("command_run", {
            "executable": "npm", "args": ["exec", "unknown-package"], "cwd": str(ROOT),
            "purpose": "execute package", "expected_artifacts": [],
        })
        npm_outside = normalize("command_run", {
            "executable": "npm", "args": ["test", "--prefix", str(OUT)], "cwd": str(ROOT),
            "purpose": "run outside package tests", "expected_artifacts": [],
        })
        npm_outside_arg = normalize("command_run", {
            "executable": "npm", "args": ["test", "--", str(OUT)], "cwd": str(ROOT),
            "purpose": "run tests with outside input", "expected_artifacts": [],
        })
        assert analyze(npm_test, [ROOT]).tier == 2
        assert analyze(npm_exec, [ROOT]).tier == 3
        assert analyze(npm_outside, [ROOT]).tier == 3
        assert analyze(npm_outside_arg, [ROOT]).tier == 3
    node = shutil.which("node")
    if node:
        node_outside = normalize("command_run", {
            "executable": node, "args": [str(OUT / "outside.js")], "cwd": str(ROOT),
            "purpose": "run outside script", "expected_artifacts": [],
        })
        node_outside_arg = normalize("command_run", {
            "executable": node, "args": [str(ROOT / "inside.js"), str(OUT / "input.txt")],
            "cwd": str(ROOT), "purpose": "run script with outside input", "expected_artifacts": [],
        })
        assert analyze(node_outside, [ROOT]).tier == 3
        assert analyze(node_outside_arg, [ROOT]).tier == 3
    cargo = shutil.which("cargo")
    if cargo:
        cargo_test = normalize("command_run", {
            "executable": cargo, "args": ["test"], "cwd": str(ROOT),
            "purpose": "test Rust project", "expected_artifacts": [],
        })
        cargo_outside = normalize("command_run", {
            "executable": cargo, "args": ["build", "--manifest-path", str(OUT / "Cargo.toml")],
            "cwd": str(ROOT), "purpose": "build outside project", "expected_artifacts": [],
        })
        assert analyze(cargo_test, [ROOT]).tier == 2
        assert analyze(cargo_outside, [ROOT]).tier == 3
    print("[check 3b] direct CLI profiles execute safe shims and keep package/path escapes Tier 3: OK")


async def check_pdf_verifier_deadline() -> None:
    target = ROOT / "generated report.pdf"
    os.environ["HALO_TEST_PDF_VERIFY_BLOCK"] = "1"
    started = time.monotonic()
    try:
        try:
            await asyncio.to_thread(commanding._pdf_pages, target, time.monotonic() + 0.2)
            raise AssertionError("blocked PDF parser exceeded its deadline")
        except commanding.VerificationLimit:
            pass
    finally:
        os.environ.pop("HALO_TEST_PDF_VERIFY_BLOCK", None)
    assert time.monotonic() - started < 2
    print("[check 3d] stalled PDF parsing is killed at the operation deadline: OK")


async def check_admission_fingerprint() -> None:
    from brain import gate
    import brain.tools.commands  # noqa: F401 - registers the managed tools

    executable = ROOT / "queued-tool.exe"
    executable.write_bytes(b"approved identity")
    raw = {
        "executable": str(executable), "args": [], "cwd": str(ROOT),
        "purpose": "test queued identity binding", "expected_artifacts": [],
    }
    admitted = normalize("command_run", raw)
    executable.write_bytes(b"different identity")
    ctx = Context("fingerprint")
    ctx.admission_fingerprint = admitted.fingerprint
    try:
        await gate.TOOLS["command_run"]["fn"](raw, ctx)
        raise AssertionError("changed executable survived task admission")
    except PolicyRefusal:
        pass
    print("[check 3c] queued execution remains bound to its admitted fingerprint: OK")


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


async def check_bounded_truthful_results() -> None:
    flood = normalize("script_run", {
        "language": "python", "source": "print('HEAD'); print('x' * 400000); print('TAIL')",
        "args": [], "cwd": str(ROOT), "purpose": "test output bounds", "expected_artifacts": [],
    }, which=_python)
    flood_ctx = Context("flood")
    result = await run_managed(flood, analyze(flood, [ROOT]), flood_ctx)
    assert "HEAD" in result["stdout"] and "TAIL" in result["stdout"]
    assert result["output_truncated"]["stdout"] > 0
    assert len("".join(flood_ctx.logs)) <= 270_000
    assert result["duration_ms"] >= 0 and result["started_at"] <= result["finished_at"]

    dual_ctx = Context("dual-flood")
    dual = normalize("script_run", {
        "language": "python",
        "source": "import sys; print('o' * 300000); print('e' * 300000, file=sys.stderr)",
        "args": [], "cwd": str(ROOT), "purpose": "test per-stream live limits", "expected_artifacts": [],
    }, which=_python)
    await run_managed(dual, analyze(dual, [ROOT]), dual_ctx)
    assert "".join(dual_ctx.logs).count("live output truncated") == 2

    binary = normalize("script_run", {
        "language": "python", "source": "import sys; sys.stdout.buffer.write(b'good\\xffbad')",
        "args": [], "cwd": str(ROOT), "purpose": "test binary output", "expected_artifacts": [],
    }, which=_python)
    binary_ctx = Context("binary")
    binary_result = await run_managed(binary, analyze(binary, [ROOT]), binary_ctx)
    assert binary_result["binary_output"]["stdout"] is True
    assert "good" not in binary_result["stdout"] + "".join(binary_ctx.logs)
    assert "binary output suppressed" in binary_result["stdout"]

    scratch = normalize("script_run", {
        "language": "python",
        "source": (
            "from pathlib import Path\nimport time\n"
            "with Path(__file__).with_name('large.bin').open('wb') as f: f.truncate(65 * 1024 * 1024)\n"
            "time.sleep(1)\n"
        ),
        "args": [], "cwd": str(ROOT), "purpose": "test scratch budget", "expected_artifacts": [],
    }, which=_python)
    try:
        await run_managed(scratch, analyze(scratch, [ROOT]), Context("scratch-limit"))
        raise AssertionError("scratch overflow succeeded")
    except TaskFailed as exc:
        assert exc.reason == "command scratch limit exceeded" and exc.result and exc.result["scratch_limit"]
    assert not (Path(_TMP) / "Halo" / "tasks" / "scratch-limit").exists()

    eof = normalize("script_run", {
        "language": "python", "source": "import sys; assert sys.stdin.read() == ''; print('EOF')",
        "args": [], "cwd": str(ROOT), "purpose": "test closed stdin", "expected_artifacts": [],
    }, which=_python)
    assert "EOF" in (await run_managed(eof, analyze(eof, [ROOT]), Context("eof")))["stdout"]

    timeout = normalize("script_run", {
        "language": "python", "source": "import time; time.sleep(30)", "args": [],
        "cwd": str(ROOT), "purpose": "test timeout", "timeout_seconds": 1,
        "expected_artifacts": [],
    }, which=_python)
    started = time.monotonic()
    try:
        await run_managed(timeout, analyze(timeout, [ROOT]), Context("timeout"))
        raise AssertionError("timed-out command succeeded")
    except TaskFailed as exc:
        assert exc.reason == "command timed out" and exc.result and exc.result["timed_out"]
        assert time.monotonic() - started < 3

    partial = ROOT / "partial.txt"
    failing = normalize("script_run", {
        "language": "python",
        "source": "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('useful'); raise SystemExit(7)",
        "args": [str(partial)], "cwd": str(ROOT), "purpose": "test partial result",
        "expected_artifacts": [{"path": str(partial), "required": True}],
    }, which=_python)
    try:
        await run_managed(failing, analyze(failing, [ROOT]), Context("partial"))
        raise AssertionError("nonzero command succeeded")
    except TaskFailed as exc:
        assert exc.result and exc.result["exit_code"] == 7
        assert exc.result["artifacts"][0]["status"] == "valid"

    overwrite = ROOT / "unchanged.txt"
    overwrite.write_text("before")
    unchanged = normalize("script_run", {
        "language": "python", "source": "print('did nothing')", "args": [],
        "cwd": str(ROOT), "purpose": "test overwrite verification",
        "expected_artifacts": [{"path": str(overwrite), "overwrite": True}],
    }, which=_python)
    try:
        await run_managed(unchanged, analyze(unchanged, [ROOT]), Context("unchanged"))
        raise AssertionError("unchanged overwrite succeeded")
    except TaskFailed as exc:
        assert exc.result and exc.result["artifacts"][0]["status"] == "unexpected_overwrite"
    print("[check 5] output is bounded head+tail; partial and unchanged artifacts stay truthful: OK")


async def check_artifact_lease_and_literal_secret_refusal() -> None:
    target = ROOT / "leased.txt"
    slow = normalize("script_run", {
        "language": "python",
        "source": "import sys,time; time.sleep(.5); open(sys.argv[1], 'w').write('ok')",
        "args": [str(target)], "cwd": str(ROOT), "purpose": "hold artifact lease",
        "expected_artifacts": [{"path": str(target)}],
    }, which=_python)
    first = asyncio.create_task(run_managed(slow, analyze(slow, [ROOT]), Context("lease-one")))
    await asyncio.sleep(.1)
    try:
        await run_managed(slow, analyze(slow, [ROOT]), Context("lease-two"))
        raise AssertionError("artifact conflict ran concurrently")
    except ArtifactBusy:
        pass
    await first

    secret_value = secrets_store.resolve_reference("command-test-secret")
    leaked = normalize("script_run", {
        "language": "python", "source": f"print({secret_value!r})", "args": [],
        "cwd": str(ROOT), "purpose": "literal secret refusal",
        "secret_env": {"TEST_SECRET": "command-test-secret"}, "expected_artifacts": [],
    }, which=_python)
    try:
        await run_managed(leaked, analyze(leaked, [ROOT]), Context("literal-secret"))
        raise AssertionError("literal secret in source executed")
    except PolicyRefusal:
        pass
    purpose_leak = normalize("script_run", {
        "language": "python", "source": "print('safe')", "args": [],
        "cwd": str(ROOT), "purpose": f"leak {secret_value}",
        "secret_env": {"TEST_SECRET": "command-test-secret"}, "expected_artifacts": [],
    }, which=_python)
    try:
        validate_secrets(purpose_leak)
        raise AssertionError("literal secret in persisted purpose was accepted")
    except PolicyRefusal:
        pass
    path_leak = normalize("script_run", {
        "language": "python", "source": "print('safe')", "args": [],
        "cwd": str(ROOT), "purpose": "test artifact secret refusal",
        "secret_env": {"TEST_SECRET": "command-test-secret"},
        "expected_artifacts": [{"path": str(ROOT / secret_value)}],
    }, which=_python)
    try:
        validate_secrets(path_leak)
        raise AssertionError("literal secret in persisted artifact path was accepted")
    except PolicyRefusal:
        pass
    released = ROOT / "released-after-error.txt"
    missing_secret = normalize("script_run", {
        "language": "python", "source": "print('never starts')", "args": [],
        "cwd": str(ROOT), "purpose": "test lease cleanup",
        "secret_env": {"MISSING": "does-not-exist"},
        "expected_artifacts": [{"path": str(released)}],
    }, which=_python)
    try:
        await run_managed(missing_secret, analyze(missing_secret, [ROOT]), Context("lease-error"))
        raise AssertionError("missing secret executed")
    except ValueError:
        pass
    retry = normalize("script_run", {
        "language": "python", "source": "import sys; open(sys.argv[1], 'w').write('ok')",
        "args": [str(released)], "cwd": str(ROOT), "purpose": "prove lease released",
        "expected_artifacts": [{"path": str(released)}],
    }, which=_python)
    await run_managed(retry, analyze(retry, [ROOT]), Context("lease-retry"))
    print("[check 6] artifact leases prevent conflicts and literal secrets are refused: OK")


async def check_lease_boundaries_and_verification_limit() -> None:
    target = ROOT / "lease-boundary.txt"
    target.write_text("before")
    overwrite = normalize("script_run", {
        "language": "python",
        "source": "import sys; open(sys.argv[1], 'w').write('after')",
        "args": [str(target)], "cwd": str(ROOT), "purpose": "test lease admission",
        "expected_artifacts": [{"path": str(target), "overwrite": True}],
    }, which=_python)
    entered, release = threading.Event(), threading.Event()
    original_mark = commanding._artifact_mark
    first_mark = True

    def blocked_mark(path, *args):
        nonlocal first_mark
        if path == target and first_mark:
            first_mark = False
            entered.set()
            release.wait(5)
        return original_mark(path, *args)

    commanding._artifact_mark = blocked_mark
    first = asyncio.create_task(run_managed(overwrite, analyze(overwrite, [ROOT]), Context("lease-baseline")))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            await asyncio.wait_for(
                run_managed(overwrite, analyze(overwrite, [ROOT]), Context("lease-racer")), 2,
            )
            raise AssertionError("second task crossed the baseline lease window")
        except ArtifactBusy:
            pass
    finally:
        release.set()
        commanding._artifact_mark = original_mark
    await first

    verify_target = ROOT / "lease-verify.txt"
    verify_spec = normalize("script_run", {
        "language": "python", "source": "import sys; open(sys.argv[1], 'w').write('one')",
        "args": [str(verify_target)], "cwd": str(ROOT), "purpose": "test verification lease",
        "expected_artifacts": [{"path": str(verify_target)}],
    }, which=_python)
    verify_entered, verify_release = threading.Event(), threading.Event()
    original_verify = commanding._verify_artifact

    def blocked_verify(item, *args):
        if item.path == verify_target and not verify_entered.is_set():
            verify_entered.set()
            verify_release.wait(5)
        return original_verify(item, *args)

    commanding._verify_artifact = blocked_verify
    first_verify = asyncio.create_task(
        run_managed(verify_spec, analyze(verify_spec, [ROOT]), Context("lease-verify"))
    )
    try:
        assert await asyncio.to_thread(verify_entered.wait, 2)
        racer = normalize("script_run", {
            "language": "python", "source": "print('racer')", "args": [],
            "cwd": str(ROOT), "purpose": "test verification race",
            "expected_artifacts": [{"path": str(verify_target), "overwrite": True}],
        }, which=_python)
        try:
            await asyncio.wait_for(run_managed(racer, analyze(racer, [ROOT]), Context("verify-racer")), 2)
            raise AssertionError("second task crossed the verification lease window")
        except ArtifactBusy:
            pass
    finally:
        verify_release.set()
        commanding._verify_artifact = original_verify
    await first_verify

    huge = ROOT / "too-large.bin"
    with huge.open("wb") as handle:
        handle.truncate(257 * 1024 * 1024)
    too_large = normalize("script_run", {
        "language": "python", "source": "print('must not start')", "args": [],
        "cwd": str(ROOT), "purpose": "test artifact size limit",
        "expected_artifacts": [{"path": str(huge), "overwrite": True}],
    }, which=_python)
    try:
        await run_managed(too_large, analyze(too_large, [ROOT]), Context("artifact-limit"))
        raise AssertionError("oversized artifact baseline was accepted")
    except TaskFailed as exc:
        assert exc.reason == "artifact verification limit exceeded"
    huge.unlink()
    print("[check 6a] leases span admission through verification; verification is size-bounded: OK")


async def check_stop_kills_descendants() -> None:
    sentinel = ROOT / "descendant-survived.txt"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(sentinel)!r}).write_text('alive')"
    source = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(30)\n"
    )
    spec = normalize("script_run", {
        "language": "python", "source": source, "args": [], "cwd": str(ROOT),
        "purpose": "test descendant cancellation", "expected_artifacts": [],
    }, which=_python)
    ctx = Context("tree-stop")
    running = asyncio.create_task(run_managed(spec, analyze(spec, [ROOT]), ctx))
    await asyncio.sleep(.25)
    ctx.cancelled.set()
    try:
        await running
        raise AssertionError("stopped process tree succeeded")
    except TaskStopped:
        pass
    await asyncio.sleep(1.25)
    assert not sentinel.exists(), "a grandchild survived Stop"
    print("[check 6b] Stop kills the descendant process tree, not only its parent: OK")


async def check_websocket_approval_to_verified_artifact() -> None:
    import uuid
    from datetime import datetime, timezone
    import websockets
    from brain import graph
    from brain.server import start

    def frame(kind: str, **payload) -> dict:
        return {"type": kind, "id": str(uuid.uuid4()),
                "ts": datetime.now(timezone.utc).isoformat(), **payload}

    managed, token = await start()
    port = managed.sockets[0].getsockname()[1]
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    target = ROOT / "websocket report.pdf"
    source = (
        "from pypdf import PdfWriter\nimport sys\n"
        "w=PdfWriter(); w.add_blank_page(width=72,height=72); w.write(sys.argv[1])\n"
    )
    args = {"language": "python", "source": source, "args": [str(target)],
            "cwd": str(ROOT), "purpose": "create the websocket PDF",
            "expected_artifacts": [{"path": str(target), "kind": "pdf"}]}
    cid = "command-websocket"
    try:
        await ws.send(json.dumps(frame("hello", token=token)))
        assert json.loads(await ws.recv())["type"] == "hello_ack"
        while json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] != "snapshot_complete":
            pass
        await ws.send(json.dumps(frame(
            "user_msg", text=f"CALL_TOOL script_run {json.dumps(args)}",
            conversation_id=cid, source="ui",
        )))
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if event["type"] == "approval_request":
                assert event["tool"] == "script_run"
                dump = json.dumps(event)
                assert source not in dump and "source_sha256" in dump
                await ws.send(json.dumps(frame(
                    "approval_response", reply_to=event["approval_id"], decision="approve",
                )))
                break
        task_id = None
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if event["type"] == "task_state" and event["state"] == "done":
                task_id = event["task_id"]
                break
        task = store.get_task(task_id)
        assert task and json.loads(task["result_json"])["artifacts"][0]["status"] == "valid"
        assert source not in task["args_json"] and target.read_bytes().startswith(b"%PDF-")
        action_dump = json.dumps([a for a in store.recent_actions(20) if a.get("task_id") == task_id])
        assert source not in action_dump and "source_sha256" in action_dump

        outside_args = {"executable": sys.executable, "args": ["--version"],
                        "cwd": str(OUT), "purpose": "check Python outside registered roots",
                        "expected_artifacts": []}
        outside_cid = "command-outside-root"
        await ws.send(json.dumps(frame(
            "user_msg", text=f"CALL_TOOL command_run {json.dumps(outside_args)}",
            conversation_id=outside_cid, source="ui",
        )))
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if event["type"] == "approval_request" and event.get("conversation_id") == outside_cid:
                assert event["tier"] == 3 and event["tool"] == "command_run"
                await ws.send(json.dumps(frame(
                    "approval_response", reply_to=event["approval_id"], decision="approve",
                )))
                break
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if event["type"] == "task_state" and event["state"] == "done":
                outside_task = store.get_task(event["task_id"])
                if outside_task and outside_task["conversation_id"] == outside_cid:
                    assert json.loads(outside_task["result_json"])["exit_code"] == 0
                    break
    finally:
        await ws.close()
        managed.close()
        await managed.wait_closed()
        await graph.aclose()
    print("[check 7] WebSocket approval drives verified PDF and approved outside-root execution end-to-end: OK")


async def main() -> None:
    check_normalization_and_policy()
    check_chunk_safe_redaction()
    await check_script_pdf_and_false_success()
    await check_direct_command_profiles()
    await check_admission_fingerprint()
    await check_pdf_verifier_deadline()
    await check_secret_environment_and_stop()
    await check_bounded_truthful_results()
    await check_artifact_lease_and_literal_secret_refusal()
    await check_lease_boundaries_and_verification_limit()
    await check_stop_kills_descendants()
    await check_websocket_approval_to_verified_artifact()
    print("[brain.commands] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
