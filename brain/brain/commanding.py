"""Small, policy-gated command runner. Shell strings are deliberately unsupported."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from brain import secrets_store
from brain.task_runtime import TaskFailed, TaskStopped

_MAX_SOURCE = 256 * 1024
_MAX_OUTPUT = 64 * 1024
_ENV_KEYS = ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class PolicyRefusal(ValueError):
    pass


@dataclass(frozen=True)
class Artifact:
    path: Path
    kind: str = "file"
    required: bool = True
    overwrite: bool = False


@dataclass(frozen=True)
class CommandSpec:
    tool: str
    executable: Path
    args: tuple[str, ...]
    cwd: Path
    purpose: str
    timeout_seconds: int
    artifacts: tuple[Artifact, ...]
    source: str | None = None
    source_sha256: str | None = None
    language: str | None = None
    env: tuple[tuple[str, str], ...] = ()
    secret_env: tuple[tuple[str, str], ...] = ()
    network: bool = False

    @property
    def fingerprint(self) -> str:
        stat = self.executable.stat()
        value = {
            "tool": self.tool, "language": self.language,
            "executable": str(self.executable), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "args": self.args, "cwd": str(self.cwd),
            "source_sha256": self.source_sha256, "network": self.network,
            "env": self.env, "secret_env": self.secret_env,
            "artifacts": [(str(a.path), a.kind, a.required, a.overwrite) for a in self.artifacts],
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    tier: int
    mutating: bool = True
    destructive: bool = False


def _resolve_executable(value: str, which: Callable[[str], str | None]) -> Path:
    found = value if Path(value).is_absolute() else which(value)
    if not found:
        raise ValueError(f"executable not found: {value}")
    path = Path(found).resolve()
    if not path.is_file():
        raise ValueError(f"executable is not a file: {path}")
    return path


def normalize(tool: str, raw: dict, *, which: Callable[[str], str | None] = shutil.which) -> CommandSpec:
    """Turn model arguments into one strict, immutable execution request."""
    if tool not in {"command_run", "script_run"}:
        raise ValueError("unknown command tool")
    cwd = Path(raw.get("cwd") or Path.home()).resolve()
    if not cwd.is_dir():
        raise ValueError("cwd must be an existing directory")
    purpose = str(raw.get("purpose", "")).strip()
    if not purpose:
        raise ValueError("purpose is required")
    timeout = int(raw.get("timeout_seconds", 300))
    if not 1 <= timeout <= 1800:
        raise ValueError("timeout_seconds must be between 1 and 1800")
    args = raw.get("args", [])
    if not isinstance(args, list) or len(args) > 256 or any(not isinstance(a, str) for a in args):
        raise ValueError("args must be a list of at most 256 strings")
    artifacts_raw = raw.get("expected_artifacts", [])
    if not isinstance(artifacts_raw, list) or len(artifacts_raw) > 16:
        raise ValueError("expected_artifacts must be a list of at most 16 items")
    artifacts = tuple(Artifact(
        path=(cwd / str(a["path"])).resolve() if not Path(str(a["path"])).is_absolute() else Path(str(a["path"])).resolve(),
        kind=str(a.get("kind", "file")).lower(),
        required=bool(a.get("required", True)),
        overwrite=bool(a.get("overwrite", False)),
    ) for a in artifacts_raw)
    env = raw.get("env", {})
    secret_env = raw.get("secret_env", {})
    if not isinstance(env, dict) or not isinstance(secret_env, dict):
        raise ValueError("env and secret_env must be objects")
    if any(not _ENV_NAME.fullmatch(str(k)) for k in (*env, *secret_env)):
        raise ValueError("environment variable names are invalid")
    pairs = lambda values: tuple((str(k), str(v)) for k, v in values.items())
    source = language = digest = None
    if tool == "script_run":
        language = str(raw.get("language", "")).lower()
        names = {"python": "python", "powershell": "powershell"}
        if language not in names:
            raise ValueError("language must be python or powershell")
        source = raw.get("source")
        if not isinstance(source, str) or not source or len(source.encode()) > _MAX_SOURCE:
            raise ValueError("source must contain 1 to 262144 UTF-8 bytes")
        digest = hashlib.sha256(source.encode()).hexdigest()
        executable = _resolve_executable(names[language], which)
    else:
        executable = _resolve_executable(str(raw.get("executable", "")), which)
    return CommandSpec(tool, executable, tuple(args), cwd, purpose, timeout, artifacts,
                       source, digest, language, pairs(env), pairs(secret_env), bool(raw.get("network", False)))


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == root.resolve() or root.resolve() in path.parents for root in roots)


def analyze(spec: CommandSpec, roots: list[Path]) -> Decision:
    """Classify a normalized request; ambiguous nested shells fail closed."""
    name = spec.executable.stem.lower()
    lowered = tuple(a.lower() for a in spec.args)
    if name in {"cmd", "cmd.exe"} and any(a in {"/c", "/k"} for a in lowered):
        raise PolicyRefusal("nested cmd execution is unsupported; pass structured argv directly")
    if name in {"powershell", "pwsh"} and any(a in {"-encodedcommand", "-enc"} for a in lowered):
        raise PolicyRefusal("encoded PowerShell is unsupported")
    if spec.tool == "script_run" or (name.startswith("python") and "-c" in spec.args):
        return Decision(3)
    if spec.network or any(a.overwrite for a in spec.artifacts):
        return Decision(3, destructive=any(a.overwrite for a in spec.artifacts))
    if not _inside(spec.cwd, roots) or any(not _inside(a.path, roots) for a in spec.artifacts):
        return Decision(3)
    if name == "git" and spec.args and spec.args[0] in {"status", "diff", "log", "show", "branch"}:
        return Decision(1, mutating=False)
    return Decision(2)


def redacted_args(spec: CommandSpec) -> dict:
    """Persist intent and hashes, never generated source or secret values."""
    result = {
        "executable": str(spec.executable), "args": list(spec.args), "cwd": str(spec.cwd),
        "purpose": spec.purpose, "timeout_seconds": spec.timeout_seconds,
        "network": spec.network, "env": dict(spec.env), "expected_artifacts": [
            {"path": str(a.path), "kind": a.kind, "required": a.required, "overwrite": a.overwrite}
            for a in spec.artifacts
        ], "secret_env": dict(spec.secret_env), "fingerprint": spec.fingerprint,
    }
    if spec.source is not None:
        result.update(language=spec.language, source_sha256=spec.source_sha256,
                      source_bytes=len(spec.source.encode()))
    return result


class SecretRedactor:
    """Streaming exact-value redactor that retains enough tail for split matches."""
    def __init__(self, secrets: tuple[str, ...]) -> None:
        self.secrets = tuple(sorted((s for s in secrets if s), key=len, reverse=True))
        self.tail = ""
        self.keep = max((len(s) for s in self.secrets), default=1) - 1

    def _mask(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def feed(self, chunk: str) -> str:
        combined = self.tail + chunk
        if len(combined) <= self.keep:
            self.tail = combined
            return ""
        cut = len(combined) - self.keep
        # If the boundary intersects a secret, hold that whole candidate.
        for secret in self.secrets:
            start = combined.rfind(secret[:1], 0, cut)
            if start >= 0 and combined[start:cut] == secret[:cut - start]:
                cut = start
        visible, self.tail = combined[:cut], combined[cut:]
        return self._mask(visible)

    def finish(self) -> str:
        result, self.tail = self._mask(self.tail), ""
        return result


def _task_dir(task_id: str) -> Path:
    safe = "".join(c for c in task_id if c.isalnum() or c in "-_") or "task"
    root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Halo" / "tasks" / safe
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _stop_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(proc.pid), "/T", "/F",
                                                      stdout=asyncio.subprocess.DEVNULL,
                                                      stderr=asyncio.subprocess.DEVNULL)
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(killer.wait(), 1)
        # Some sandbox/job configurations reject taskkill even for our child.
        # Killing the direct process is still required so cancellation is prompt.
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
    else:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), 1)


def _verify_artifact(item: Artifact) -> dict:
    result = {"path": str(item.path), "kind": item.kind}
    if not item.path.is_file():
        return result | {"status": "missing"}
    if item.path.stat().st_size == 0:
        return result | {"status": "invalid", "reason": "empty file"}
    if item.kind == "pdf":
        try:
            from pypdf import PdfReader
            with item.path.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise ValueError("missing PDF header")
                handle.seek(max(0, item.path.stat().st_size - 1024))
                if b"%%EOF" not in handle.read():
                    raise ValueError("missing PDF trailer")
            pages = len(PdfReader(str(item.path)).pages)
            return result | {"status": "valid", "bytes": item.path.stat().st_size, "pages": pages}
        except Exception as exc:
            return result | {"status": "invalid", "reason": str(exc)}
    return result | {"status": "valid", "bytes": item.path.stat().st_size}


async def run_managed(spec: CommandSpec, decision: Decision, ctx) -> dict:
    """Execute once, stream redacted output, and treat artifacts as part of success."""
    for item in spec.artifacts:
        if item.path.exists() and not item.overwrite:
            raise TaskFailed(f"artifact already exists: {item.path}", {"artifacts": [_verify_artifact(item)]})
    secrets = tuple(secrets_store.resolve_reference(ref) for _, ref in spec.secret_env)
    env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    env.update(spec.env)
    env.update((name, secrets_store.resolve_reference(ref)) for name, ref in spec.secret_env)
    scratch = None
    argv = [str(spec.executable)]
    if spec.source is not None:
        scratch = _task_dir(ctx.task_id)
        suffix = ".py" if spec.language == "python" else ".ps1"
        script = scratch / f"script{suffix}"
        temp = script.with_suffix(suffix + ".tmp")
        temp.write_text(spec.source, encoding="utf-8")
        temp.replace(script)
        if spec.language == "powershell":
            argv.extend(["-NoProfile", "-NonInteractive", "-File", str(script)])
        else:
            argv.append(str(script))
    argv.extend(spec.args)
    flags = getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    proc = await asyncio.create_subprocess_exec(*argv, cwd=spec.cwd, env=env,
                                                stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE,
                                                creationflags=flags,
                                                start_new_session=os.name != "nt")
    captured = {"stdout": "", "stderr": ""}

    async def drain(name: str, stream) -> None:
        redactor = SecretRedactor(secrets)
        while chunk := await stream.read(4096):
            text = redactor.feed(chunk.decode(errors="replace"))
            captured[name] = (captured[name] + text)[-_MAX_OUTPUT:]
            await ctx.log(text)
        tail = redactor.finish()
        captured[name] = (captured[name] + tail)[-_MAX_OUTPUT:]
        await ctx.log(tail)

    drains = [asyncio.create_task(drain("stdout", proc.stdout)), asyncio.create_task(drain("stderr", proc.stderr))]
    waiter = asyncio.create_task(proc.wait())
    stopper = asyncio.create_task(ctx.cancelled.wait())
    timed_out = False
    stopped = False
    try:
        done, _ = await asyncio.wait((waiter, stopper), timeout=spec.timeout_seconds,
                                     return_when=asyncio.FIRST_COMPLETED)
        stopped = stopper in done and ctx.cancelled.is_set()
        timed_out = not done
        if stopped or timed_out:
            await _stop_tree(proc)
        else:
            await waiter
        await asyncio.gather(*drains)
    finally:
        stopper.cancel()
        if proc.returncode is None:
            await _stop_tree(proc)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    artifacts = [_verify_artifact(a) for a in spec.artifacts]
    result = {"exit_code": proc.returncode, **captured, "artifacts": artifacts,
              "timed_out": timed_out, "stopped": stopped, "tier": decision.tier}
    if stopped:
        raise TaskStopped(result)
    if timed_out:
        raise TaskFailed("command timed out", result)
    if proc.returncode:
        raise TaskFailed(f"command exited with code {proc.returncode}", result)
    bad = [r for a, r in zip(spec.artifacts, artifacts) if a.required and r["status"] != "valid"]
    if bad:
        raise TaskFailed("required artifact verification failed", result)
    return result
