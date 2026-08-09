"""Small, policy-gated command runner. Shell strings are deliberately unsupported."""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from brain import secrets_store
from brain.task_runtime import TaskFailed, TaskStopped

_MAX_SOURCE = 256 * 1024
_MAX_OUTPUT = 64 * 1024
_MAX_LIVE_OUTPUT = 256 * 1024
_MAX_SCRATCH = 64 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_PDF_VERIFY_SECONDS = 30
_SPEC_VERSION = 1
_EXECUTOR_VERSION = 1
_ENV_KEYS = ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SECRET_NAME = re.compile(r"(?:AUTH|BEARER|COOKIE|CREDENTIAL|KEY|PASSWORD|SECRET|SESSION|TOKEN)", re.I)
_SECRET_LITERAL = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token|client[_-]?secret|"
    r"credential|password|passwd|session[_-]?(?:id|token))\s*(?:=|:)\s*[\"']?[^\s\"']{4,}|"
    r"\b(?:AKIA|ghp_|github_pat_|sk-|xox[baprs]-)[A-Za-z0-9_\-]{8,}"
)
_ARTIFACT_LEASES: dict[Path, str] = {}


class _ProcessJob:
    """Windows kill-on-close Job Object; a no-op holder elsewhere."""
    def __init__(self, pid: int) -> None:
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.OpenProcess.restype = wintypes.HANDLE
        job = kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = kernel.OpenProcess(0x0101, False, pid)  # TERMINATE | SET_QUOTA
        ok = job and process and kernel.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)
        ) and kernel.AssignProcessToJobObject(job, process)
        if process:
            kernel.CloseHandle(process)
        if not ok:
            if job:
                kernel.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "could not contain command process tree")
        self.handle = job

    def close(self) -> None:
        if self.handle:
            import ctypes
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self.handle)
            self.handle = None

    def active_processes(self) -> int:
        if not self.handle:
            return 0
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        info = Accounting()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise OSError(ctypes.get_last_error(), "could not inspect command process tree")
        return info.ActiveProcesses


def _resume_process(pid: int) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
    if not handle:
        raise OSError(ctypes.get_last_error(), "could not open suspended command")
    try:
        resume = ctypes.WinDLL("ntdll").NtResumeProcess
        resume.argtypes = (wintypes.HANDLE,)
        resume.restype = ctypes.c_long
        status = resume(handle)
        if status:
            raise OSError(status, "could not resume contained command")
    finally:
        kernel.CloseHandle(handle)


class PolicyRefusal(ValueError):
    pass


class ArtifactBusy(TaskFailed):
    def __init__(self, path: Path) -> None:
        super().__init__(f"artifact is already targeted by another task: {path}")


class VerificationLimit(TaskFailed):
    def __init__(self) -> None:
        super().__init__("artifact verification limit exceeded")


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
    profile: str = ""
    prefix_args: tuple[str, ...] = ()
    identities: tuple[tuple[str, int, int, str], ...] = ()
    cwd_identity: tuple[int, int] = (0, 0)

    @property
    def fingerprint(self) -> str:
        value = {
            "tool": self.tool, "language": self.language,
            "profile": self.profile, "executable": str(self.executable),
            "identities": self.identities, "prefix_args": self.prefix_args,
            "args": self.args, "cwd": str(self.cwd), "cwd_identity": self.cwd_identity,
            "purpose": self.purpose, "timeout_seconds": self.timeout_seconds,
            "source_sha256": self.source_sha256, "network": self.network,
            "env": self.env, "secret_env": self.secret_env,
            "artifacts": [(str(a.path), a.kind, a.required, a.overwrite) for a in self.artifacts],
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    tier: int
    mutating: bool = True
    destructive: bool = False
    effective_args: tuple[str, ...] | None = None
    env: tuple[tuple[str, str], ...] = ()


def _identity(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    with path.open("rb") as handle:
        head = handle.read(64 * 1024)
        handle.seek(max(0, stat.st_size - 64 * 1024))
        tail = handle.read(64 * 1024)
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(head + tail).hexdigest()


def recheck_identity(spec: CommandSpec) -> None:
    for raw, size, mtime, digest in spec.identities:
        if _identity(Path(raw)) != (size, mtime, digest):
            raise PolicyRefusal("executable or launcher changed after normalization")
    if (not spec.cwd.is_dir() or spec.cwd.resolve() != spec.cwd or
            (spec.cwd.stat().st_dev, spec.cwd.stat().st_ino) != spec.cwd_identity):
        raise PolicyRefusal("working directory changed after normalization")
    if any(item.path.resolve() != item.path for item in spec.artifacts):
        raise PolicyRefusal("artifact path changed after normalization")


def _resolve_executable(value: str, which: Callable[[str], str | None]) -> Path:
    if not value or any(c in value for c in "\0\r\n"):
        raise ValueError("executable is invalid")
    found = value if Path(value).is_absolute() else which(value)
    if not found:
        raise ValueError(f"executable not found: {value}")
    path = Path(found).resolve()
    if not path.is_file():
        raise ValueError(f"executable is not a file: {path}")
    return path


def _trusted_profile(requested: str, executable: Path, which: Callable[[str], str | None]) -> str:
    stem = Path(requested).stem.lower()
    if (not getattr(sys, "frozen", False) and stem.startswith("python") and
            executable == Path(sys.executable).resolve()):
        return stem
    for probe in dict.fromkeys((Path(requested).name, stem)):
        found = which(probe)
        if found and Path(found).resolve() == executable:
            return stem
    return ""


def normalize(tool: str, raw: dict, *, which: Callable[[str], str | None] = shutil.which) -> CommandSpec:
    """Turn model arguments into one strict, immutable execution request."""
    if tool not in {"command_run", "script_run"}:
        raise ValueError("unknown command tool")
    cwd = Path(raw.get("cwd") or Path.home()).resolve()
    if not cwd.is_dir():
        raise ValueError("cwd must be an existing directory")
    purpose_raw = raw.get("purpose", "")
    purpose = purpose_raw.strip() if isinstance(purpose_raw, str) else ""
    if not purpose or len(purpose) > 512:
        raise ValueError("purpose must contain 1 to 512 characters")
    timeout = raw.get("timeout_seconds", 300)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise ValueError("timeout_seconds must be an integer")
    if not 1 <= timeout <= 1800:
        raise ValueError("timeout_seconds must be between 1 and 1800")
    args = raw.get("args", [])
    if (not isinstance(args, list) or len(args) > 256 or
            any(not isinstance(a, str) or len(a.encode()) > 32768 or "\0" in a for a in args)):
        raise ValueError("args must be a list of at most 256 strings")
    if any(
        (arg.startswith("-") and _SECRET_NAME.search(arg.split("=", 1)[0])) or
        re.search(r"://[^/\s:@]+:[^/\s@]+@", arg)
        for arg in args
    ):
        raise ValueError("literal command secrets are unsupported; use secret_env")
    artifacts_raw = raw.get("expected_artifacts", [])
    if not isinstance(artifacts_raw, list) or len(artifacts_raw) > 16:
        raise ValueError("expected_artifacts must be a list of at most 16 items")
    artifacts_list = []
    for item in artifacts_raw:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"]:
            raise ValueError("each expected artifact needs a nonempty path")
        kind = item.get("kind", "file")
        required, overwrite = item.get("required", True), item.get("overwrite", False)
        if kind not in {"file", "pdf"} or not isinstance(required, bool) or not isinstance(overwrite, bool):
            raise ValueError("artifact kind/required/overwrite is invalid")
        path = Path(item["path"])
        path = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
        artifacts_list.append(Artifact(path, kind, required, overwrite))
    if len({item.path for item in artifacts_list}) != len(artifacts_list):
        raise ValueError("expected artifact paths must be unique")
    artifacts = tuple(artifacts_list)
    env = raw.get("env", {})
    secret_env = raw.get("secret_env", {})
    if not isinstance(env, dict) or not isinstance(secret_env, dict) or len(env) > 32 or len(secret_env) > 32:
        raise ValueError("env and secret_env must be objects")
    if any(not isinstance(k, str) or not _ENV_NAME.fullmatch(k) for k in (*env, *secret_env)):
        raise ValueError("environment variable names are invalid")
    if (set(env) & set(secret_env) or any(not isinstance(v, str) or len(v.encode()) > 8192 for v in env.values()) or
            any(not isinstance(v, str) for v in secret_env.values()) or any(_SECRET_NAME.search(k) for k in env)):
        raise ValueError("environment values are invalid")
    pairs = lambda values: tuple(sorted(values.items()))
    source = language = digest = None
    profile = ""
    prefix_args: tuple[str, ...] = ()
    if tool == "script_run":
        language = str(raw.get("language", "")).lower()
        if language not in {"python", "powershell"}:
            raise ValueError("language must be python or powershell")
        source = raw.get("source")
        if not isinstance(source, str) or not source or len(source.encode()) > _MAX_SOURCE:
            raise ValueError("source must contain 1 to 262144 UTF-8 bytes")
        digest = hashlib.sha256(source.encode()).hexdigest()
        if language == "powershell":
            found = which("pwsh") or which("powershell")
            if not found:
                raise ValueError("PowerShell interpreter unavailable")
            executable = _resolve_executable(found, which)
        else:
            found = which("python")
            if not found and not getattr(sys, "frozen", False):
                found = sys.executable
            if not found:
                raise ValueError("Python interpreter unavailable")
            executable = _resolve_executable(found, which)
        profile = language
    else:
        requested = str(raw.get("executable", ""))
        executable = _resolve_executable(requested, which)
        profile = _trusted_profile(requested, executable, which)
    identity_paths = [executable]
    if tool == "command_run" and os.name == "nt" and executable.suffix.lower() == ".cmd":
        shim = executable.with_suffix(".ps1")
        found = which("pwsh") or which("powershell")
        if not shim.is_file() or not found:
            raise ValueError(f"safe launcher unavailable for command shim: {executable}")
        executable = _resolve_executable(found, which)
        prefix_args = ("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(shim))
        identity_paths = [executable, shim.resolve()]
    network = raw.get("network", False)
    if not isinstance(network, bool):
        raise ValueError("network must be a boolean")
    if any(_SECRET_LITERAL.search(value) for value in (*args, purpose, source or "", *env.values())):
        raise ValueError("obvious literal secret detected; use secret_env")
    identities = tuple((str(path), *_identity(path)) for path in identity_paths)
    cwd_stat = cwd.stat()
    return CommandSpec(
        tool=tool, executable=executable, args=tuple(args), cwd=cwd, purpose=purpose,
        timeout_seconds=timeout, artifacts=artifacts, source=source,
        source_sha256=digest, language=language, env=pairs(env),
        secret_env=pairs(secret_env), network=network, profile=profile,
        prefix_args=prefix_args, identities=identities,
        cwd_identity=(cwd_stat.st_dev, cwd_stat.st_ino),
    )


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == root.resolve() or root.resolve() in path.parents for root in roots)


def _outside_operand(value: str, cwd: Path, roots: list[Path]) -> bool:
    candidate = Path(value).expanduser()
    candidate = candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve()
    return not _inside(candidate, roots)


def _has_outside_path(args: tuple[str, ...], cwd: Path, roots: list[Path]) -> bool:
    for raw in args:
        value = raw.split("=", 1)[1] if raw.startswith("--") and "=" in raw else raw
        path = Path(value).expanduser()
        pathlike = path.is_absolute() or value.startswith(("./", "../", ".\\", "..\\"))
        if pathlike and _outside_operand(value, cwd, roots):
            return True
    return False


def analyze(spec: CommandSpec, roots: list[Path]) -> Decision:
    """Classify a normalized request; ambiguous nested shells fail closed."""
    name = spec.profile
    lowered = tuple(a.lower() for a in spec.args)
    if name == "cmd" and any(a in {"/c", "/k"} for a in lowered):
        raise PolicyRefusal("nested cmd execution is unsupported; pass structured argv directly")
    if name in {"powershell", "pwsh"} and any(a in {"-encodedcommand", "-enc"} for a in lowered):
        raise PolicyRefusal("encoded PowerShell is unsupported")
    if name in {"diskpart", "format", "bcdedit", "runas"}:
        raise PolicyRefusal(f"{name} requires a dedicated capability")
    if any(a in {"--detach", "--background"} for a in lowered):
        raise PolicyRefusal("detached/background execution is unsupported")
    if spec.network or any(a.overwrite for a in spec.artifacts):
        return Decision(3, destructive=any(a.overwrite for a in spec.artifacts))
    if spec.env or spec.secret_env:
        return Decision(3)
    if not _inside(spec.cwd, roots) or any(not _inside(a.path, roots) for a in spec.artifacts):
        return Decision(3, destructive=any(a.overwrite for a in spec.artifacts))
    if spec.tool == "script_run" or (name.startswith("python") and "-c" in spec.args):
        return Decision(3)
    if any(a.startswith("@") for a in spec.args):
        return Decision(3)
    if name.startswith("python") and spec.args and not spec.args[0].startswith("-"):
        operand = Path(spec.args[0]).expanduser()
        if operand.is_absolute() or operand.suffix.lower() == ".py":
            operand = operand.resolve() if operand.is_absolute() else (spec.cwd / operand).resolve()
            if not _inside(operand, roots):
                return Decision(3)
    version_args = {("--version",), ("-V",), ("version",)}
    if spec.args in version_args and name in {"python", "python3", "node", "npm", "cargo", "codex", "claude"}:
        return Decision(1, mutating=False)
    if name == "git" and spec.args:
        if spec.args[0].startswith("-"):
            return Decision(3)
        sub = spec.args[0]
        if sub in {"status", "diff", "log"}:
            dangerous = {"--no-index", "--ext-diff", "--textconv"}
            if any(a in dangerous or a in {"-o", "--output"} or a.startswith("--output=")
                   for a in spec.args[1:]):
                raise PolicyRefusal("Git option may execute helpers, escape roots, or write output")
            if _has_outside_path(spec.args[1:], spec.cwd, roots):
                return Decision(3)
            path_mode = False
            for arg in spec.args[1:]:
                if arg == "--":
                    path_mode = True
                    continue
                candidate = Path(arg).expanduser()
                pathlike = path_mode or candidate.is_absolute() or arg.startswith(("./", "../", ".\\", "..\\"))
                if pathlike:
                    candidate = candidate.resolve() if candidate.is_absolute() else (spec.cwd / candidate).resolve()
                    if not _inside(candidate, roots):
                        return Decision(3)
            safe = ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
                    "-c", "diff.external=", sub)
            if sub in {"diff", "log"}:
                safe += ("--no-ext-diff", "--no-textconv")
            safe += spec.args[1:]
            return Decision(1, mutating=False, effective_args=safe, env=(
                ("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_CONFIG_GLOBAL", os.devnull),
                ("GIT_PAGER", "cat"), ("GIT_TERMINAL_PROMPT", "0"),
            ))
        destructive = (
            sub in {"clean", "restore", "checkout"} or
            (sub == "reset" and "--hard" in spec.args) or
            (sub == "branch" and any(arg in {"-d", "-D", "--delete"} for arg in spec.args[1:])) or
            (sub == "push" and any(arg in {"-f", "--force", "--force-with-lease"} for arg in spec.args[1:]))
        )
        return Decision(3, destructive=destructive)
    if name.startswith("python"):
        if len(spec.args) >= 2 and spec.args[:2] == ("-m", "pip"):
            return Decision(3)
        if spec.args and (spec.args[0].endswith(".py") or spec.args[:2] in (("-m", "pytest"), ("-m", "unittest"))):
            operands = spec.args[2:] if spec.args[0] == "-m" else spec.args
            if _has_outside_path(operands, spec.cwd, roots):
                return Decision(3)
            if any(_outside_operand(arg, spec.cwd, roots) for arg in operands if not arg.startswith("-")):
                return Decision(3)
            return Decision(2)
        return Decision(3)
    if name == "npm" and spec.args:
        if _has_outside_path(spec.args[1:], spec.cwd, roots):
            return Decision(3)
        for index, arg in enumerate(spec.args[1:]):
            if arg in {"--prefix", "--cache"} and index + 2 < len(spec.args):
                if _outside_operand(spec.args[index + 2], spec.cwd, roots):
                    return Decision(3)
            if arg.startswith(("--prefix=", "--cache=")) and _outside_operand(arg.split("=", 1)[1], spec.cwd, roots):
                return Decision(3)
        return Decision(2 if spec.args[0] in {"test", "run"} else 3)
    if name == "cargo" and spec.args:
        if spec.args[0] not in {"test", "build", "check"}:
            return Decision(3)
        if any(arg in {"--manifest-path", "--target-dir"} or arg.startswith(("--manifest-path=", "--target-dir="))
               for arg in spec.args[1:]):
            return Decision(3)
        if _has_outside_path(spec.args[1:], spec.cwd, roots):
            return Decision(3)
        return Decision(2)
    if name == "node" and spec.args and spec.args[0].endswith((".js", ".mjs", ".cjs")):
        if _has_outside_path(spec.args, spec.cwd, roots):
            return Decision(3)
        return Decision(2)
    if name in {"powershell", "pwsh"}:
        return Decision(3)
    return Decision(3)


def matches_user_intent(spec: CommandSpec, user_text: str) -> bool:
    """Bind Tier-2 execution to both an operation and its current target."""
    words = set(re.findall(r"[a-z0-9_.-]+", user_text.lower()))
    operations = {"run", "test", "tests", "build", "convert", "render", "generate", "create", "execute", "check"}
    targets = {spec.cwd.name.lower(), spec.executable.stem.lower()}
    for value in (*spec.args, *(str(a.path) for a in spec.artifacts)):
        path = Path(value)
        targets.update(part.lower() for part in path.parts if part not in {".", ".."})
    return bool(words & operations) and bool(words & targets)


def redacted_args(spec: CommandSpec) -> dict:
    """Persist intent and hashes, never generated source or secret values."""
    result = {
        "spec_version": _SPEC_VERSION, "executor_version": _EXECUTOR_VERSION,
        "profile": spec.profile, "executable": str(spec.executable),
        "launcher_args": list(spec.prefix_args), "args": list(spec.args), "cwd": str(spec.cwd),
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


def validate_secrets(spec: CommandSpec) -> tuple[str, ...]:
    """Resolve references and reject literal copies before approval/persistence."""
    secrets = tuple(secrets_store.resolve_reference(ref) for _, ref in spec.secret_env)
    if any(len(secret) < 2 for secret in secrets):
        raise PolicyRefusal("secret values must contain at least two characters")
    visible = (
        *spec.args, spec.source or "", spec.purpose, str(spec.cwd),
        *(str(item.path) for item in spec.artifacts), *(value for _, value in spec.env),
    )
    if any(secret in value for secret in secrets for value in visible):
        raise PolicyRefusal("literal secret detected; use only the secret_env reference")
    return secrets


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
    base = (Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Halo" / "tasks").resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = base / safe
    root.mkdir(exist_ok=False)
    if root.is_symlink() or root.resolve().parent != base:
        with suppress(OSError):
            root.rmdir()
        raise PolicyRefusal("unsafe task scratch path")
    return root


def _scratch_bytes(root: Path) -> int:
    total = 0
    for item in root.rglob("*"):
        if item.is_symlink() or root not in item.resolve().parents:
            return _MAX_SCRATCH + 1
        if item.is_file():
            total += item.stat().st_size
            if total > _MAX_SCRATCH:
                break
    return total


def _clean_scratch(root: Path | None) -> None:
    if root and not root.is_symlink() and root.resolve() == root and root.parent.name == "tasks":
        shutil.rmtree(root, ignore_errors=True)


def _pdf_worker(path: str, output) -> None:
    try:
        if os.environ.get("HALO_TEST_PDF_VERIFY_BLOCK") == "1":
            time.sleep(30)
        from pypdf import PdfReader
        output.send((True, len(PdfReader(path).pages)))
    except Exception as exc:  # noqa: BLE001 - child returns parser failure as data
        output.send((False, str(exc)))
    finally:
        output.close()


def _pdf_pages(path: Path, deadline: float | None) -> int:
    """Parse in a disposable process so malformed PDFs cannot outlive the deadline."""
    limit = min(deadline or float("inf"), time.monotonic() + _MAX_PDF_VERIFY_SECONDS)
    receive, send = multiprocessing.get_context("spawn").Pipe(duplex=False)
    process = multiprocessing.get_context("spawn").Process(
        target=_pdf_worker, args=(str(path), send), daemon=True,
    )
    process.start()
    send.close()
    try:
        remaining = max(0, limit - time.monotonic())
        if not receive.poll(remaining):
            raise VerificationLimit()
        try:
            ok, value = receive.recv()
        except EOFError as exc:
            raise ValueError("PDF verifier exited without a result") from exc
        if not ok:
            raise ValueError(value)
        return int(value)
    finally:
        if process.is_alive():
            process.terminate()
        process.join(0.5)
        if process.is_alive():
            process.kill()
            process.join(0.5)
        receive.close()
        process.close()


async def _stop_tree(proc: asyncio.subprocess.Process, job: _ProcessJob) -> None:
    if proc.returncode is not None:
        job.close()
        return
    if os.name == "nt":
        with suppress(ProcessLookupError, ValueError):
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        for _ in range(10):
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.05)
        job.close()
        if proc.returncode is None:
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(proc.pid), "/T", "/F",
                                                          stdout=asyncio.subprocess.DEVNULL,
                                                          stderr=asyncio.subprocess.DEVNULL)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(killer.wait(), 0.5)
        # Some sandbox/job configurations reject taskkill even for our child.
        # Killing the direct process is still required so cancellation is prompt.
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
    else:
        with suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        for _ in range(10):
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.05)
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), 1)


def _verify_artifact(item: Artifact, deadline: float | None = None) -> dict:
    result = {"path": str(item.path), "kind": item.kind}
    if not item.path.exists():
        return result | {"status": "missing"}
    if not item.path.is_file():
        return result | {"status": "invalid", "reason": "expected a regular file"}
    size = item.path.stat().st_size
    if size == 0:
        return result | {"status": "invalid", "reason": "empty file"}
    if size > _MAX_ARTIFACT_BYTES or (deadline is not None and time.monotonic() >= deadline):
        return result | {"status": "invalid", "reason": "artifact verification limit exceeded"}
    if item.kind == "pdf":
        try:
            with item.path.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise ValueError("missing PDF header")
                handle.seek(max(0, item.path.stat().st_size - 4096))
                if b"%%EOF" not in handle.read():
                    raise ValueError("missing PDF trailer")
            pages = _pdf_pages(item.path, deadline)
            size, mtime, digest = _artifact_mark(item.path, deadline)
            return result | {"status": "valid", "bytes": size, "mtime_ns": mtime,
                             "sha256": digest, "pages": pages}
        except Exception as exc:
            return result | {"status": "invalid", "reason": str(exc)}
    try:
        size, mtime, digest = _artifact_mark(item.path, deadline)
        return result | {"status": "valid", "bytes": size, "mtime_ns": mtime, "sha256": digest}
    except VerificationLimit as exc:
        return result | {"status": "invalid", "reason": exc.reason}


def _artifact_mark(path: Path, deadline: float | None = None) -> tuple[int, int, str] | None:
    if not path.is_file():
        return None
    stat = path.stat()
    if stat.st_size > _MAX_ARTIFACT_BYTES:
        raise VerificationLimit()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if deadline is not None and time.monotonic() >= deadline:
                raise VerificationLimit()
            digest.update(chunk)
    return stat.st_size, stat.st_mtime_ns, digest.hexdigest()


async def run_managed(spec: CommandSpec, decision: Decision, ctx) -> dict:
    """Own artifact targets atomically for the entire execution transaction."""
    for item in spec.artifacts:
        if item.path.exists() and not item.overwrite:
            raise TaskFailed(f"artifact already exists: {item.path}", {"artifacts": [{
                "path": str(item.path), "kind": item.kind, "status": "exists",
            }]})
        owner = _ARTIFACT_LEASES.get(item.path)
        if owner and owner != ctx.task_id:
            raise ArtifactBusy(item.path)
    for item in spec.artifacts:
        _ARTIFACT_LEASES[item.path] = ctx.task_id
    try:
        return await _run_managed(spec, decision, ctx)
    finally:
        for item in spec.artifacts:
            if _ARTIFACT_LEASES.get(item.path) == ctx.task_id:
                _ARTIFACT_LEASES.pop(item.path, None)


async def _run_managed(spec: CommandSpec, decision: Decision, ctx) -> dict:
    """Execute once, stream redacted output, and treat artifacts as part of success."""
    started_at = time.time()
    started = time.monotonic()
    deadline = started + spec.timeout_seconds
    recheck_identity(spec)
    baselines = {
        item.path: await asyncio.to_thread(_artifact_mark, item.path, deadline)
        for item in spec.artifacts
    }

    scratch = None
    try:
        secrets = validate_secrets(spec)
        env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
        env.update(spec.env)
        env.update(decision.env)
        env.update((name, secrets_store.resolve_reference(ref)) for name, ref in spec.secret_env)
        argv = [str(spec.executable), *spec.prefix_args]
        if spec.source is not None:
            scratch = _task_dir(ctx.task_id)
            suffix = ".py" if spec.language == "python" else ".ps1"
            script = scratch / f"script{suffix}"
            temp = script.with_suffix(suffix + ".tmp")
            with temp.open("w", encoding="utf-8", newline="") as handle:
                handle.write(spec.source)
            temp.replace(script)
            if hashlib.sha256(script.read_bytes()).hexdigest() != spec.source_sha256:
                raise PolicyRefusal("materialized script does not match the approved source")
            if spec.language == "powershell":
                argv.extend(["-NoProfile", "-NonInteractive", "-File", str(script)])
            else:
                argv.append(str(script))
        argv.extend(decision.effective_args if decision.effective_args is not None else spec.args)
        if time.monotonic() >= deadline:
            raise VerificationLimit()
        recheck_identity(spec)
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x4) if os.name == "nt" else 0  # CREATE_SUSPENDED
        proc = await asyncio.create_subprocess_exec(*argv, cwd=spec.cwd, env=env,
                                                    stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE,
                                                    creationflags=flags,
                                                    start_new_session=os.name != "nt")
        job = None
        try:
            job = _ProcessJob(proc.pid)
            _resume_process(proc.pid)
        except Exception:
            proc.kill()
            await proc.wait()
            if job is not None:
                job.close()
            raise
    except Exception:
        _clean_scratch(scratch)
        raise
    heads = {"stdout": "", "stderr": ""}
    tails = {"stdout": "", "stderr": ""}
    totals = {"stdout": 0, "stderr": 0}
    binary = {"stdout": False, "stderr": False}
    live_sent = {"stdout": 0, "stderr": 0}
    live_truncated = {"stdout": False, "stderr": False}

    def capture(name: str, text: str) -> None:
        totals[name] += len(text)
        if len(heads[name]) < _MAX_OUTPUT // 2:
            take = _MAX_OUTPUT // 2 - len(heads[name])
            heads[name] += text[:take]
            text = text[take:]
        tails[name] = (tails[name] + text)[-_MAX_OUTPUT // 2:]

    async def drain(name: str, stream) -> None:
        redactor = SecretRedactor(secrets)
        decoder = codecs.getincrementaldecoder("utf-8")("strict")

        async def emit(text: str) -> None:
            capture(name, text)
            if live_sent[name] < _MAX_LIVE_OUTPUT:
                visible = text[:_MAX_LIVE_OUTPUT - live_sent[name]]
                live_sent[name] += len(visible)
                await ctx.log(visible)
            elif not live_truncated[name]:
                live_truncated[name] = True
                await ctx.log(f"\n[{name} live output truncated; command still running]\n")

        while chunk := await stream.read(4096):
            if binary[name]:
                continue
            try:
                text = decoder.decode(chunk)
            except UnicodeDecodeError:
                binary[name] = True
                await emit("[binary output suppressed]\n")
                continue
            await emit(redactor.feed(text))
        if not binary[name]:
            try:
                text = decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                binary[name] = True
                await emit("[binary output suppressed]\n")
            else:
                await emit(redactor.feed(text) + redactor.finish())

    drains = [asyncio.create_task(drain("stdout", proc.stdout)), asyncio.create_task(drain("stderr", proc.stderr))]
    waiter = asyncio.create_task(proc.wait())
    stopper = asyncio.create_task(ctx.cancelled.wait())
    async def scratch_guard() -> None:
        while True:
            if scratch and _scratch_bytes(scratch) > _MAX_SCRATCH:
                return
            await asyncio.sleep(0.05)

    limiter = asyncio.create_task(scratch_guard())
    timed_out = False
    stopped = False
    scratch_limited = False
    orphan_descendants = False
    try:
        done, _ = await asyncio.wait((waiter, stopper, limiter), timeout=max(0, deadline - time.monotonic()),
                                     return_when=asyncio.FIRST_COMPLETED)
        stopped = stopper in done and ctx.cancelled.is_set()
        timed_out = not done
        scratch_limited = limiter in done or bool(scratch and _scratch_bytes(scratch) > _MAX_SCRATCH)
        if stopped or timed_out or scratch_limited:
            await _stop_tree(proc, job)
        else:
            await waiter
            if job.active_processes():
                await asyncio.sleep(0.25)
                orphan_descendants = bool(job.active_processes())
            job.close()  # kill descendants that outlive their parent
        await asyncio.gather(*drains)
    finally:
        stopper.cancel()
        limiter.cancel()
        if proc.returncode is None:
            await _stop_tree(proc, job)
        else:
            job.close()
        _clean_scratch(scratch)
    captured = {}
    truncated = {}
    for name in ("stdout", "stderr"):
        omitted = max(0, totals[name] - len(heads[name]) - len(tails[name]))
        captured[name] = heads[name] + (f"\n... [{omitted} characters omitted] ...\n" if omitted else "") + tails[name]
        truncated[name] = omitted
    artifacts = []
    for item in spec.artifacts:
        artifacts.append(await asyncio.to_thread(_verify_artifact, item, deadline))
    for index, (item, verified) in enumerate(zip(spec.artifacts, artifacts)):
        current = (verified.get("bytes"), verified.get("mtime_ns"), verified.get("sha256"))
        if item.overwrite and verified["status"] == "valid" and current == baselines[item.path]:
            verified = verified | {"status": "unexpected_overwrite", "reason": "target did not change"}
            artifacts[index] = verified
    finished_at = time.time()
    result = {"spec_fingerprint": spec.fingerprint, "executor_version": _EXECUTOR_VERSION,
              "started_at": started_at, "finished_at": finished_at,
              "duration_ms": round((time.monotonic() - started) * 1000),
              "exit_code": proc.returncode, **captured, "output_truncated": truncated,
              "binary_output": binary, "artifacts": artifacts,
              "timed_out": timed_out, "stopped": stopped, "scratch_limit": scratch_limited,
              "orphan_descendants": orphan_descendants, "tier": decision.tier}
    if stopped:
        raise TaskStopped(result)
    if timed_out:
        raise TaskFailed("command timed out", result)
    if scratch_limited:
        raise TaskFailed("command scratch limit exceeded", result)
    if orphan_descendants:
        raise TaskFailed("command left descendant processes running", result)
    if any(item.get("reason") == "artifact verification limit exceeded" for item in artifacts):
        raise TaskFailed("artifact verification limit exceeded", result)
    if proc.returncode:
        raise TaskFailed(f"command exited with code {proc.returncode}", result)
    bad = [r for a, r in zip(spec.artifacts, artifacts) if a.required and r["status"] != "valid"]
    if bad:
        raise TaskFailed("required artifact verification failed", result)
    return result
