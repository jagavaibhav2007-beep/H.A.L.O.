"""Model-visible managed command tools registered through the one permission gate."""

from __future__ import annotations

from pathlib import Path

from brain import gate
from brain.commanding import analyze, normalize, redacted_args, run_managed
from brain.tools.files import _roots


def _request(tool: str, args: dict):
    spec = normalize(tool, args)
    return spec, analyze(spec, _roots())


def _hook(tool: str, pick):
    return lambda args: pick(*_request(tool, args))


def _schema(description: str, properties: dict, required: list[str]) -> dict:
    return {"description": description, "parameters": {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }}


_COMMON = {
    "args": {"type": "array", "items": {"type": "string"}, "description": "Structured argv; never a shell string."},
    "cwd": {"type": "string", "description": "Existing working directory."},
    "purpose": {"type": "string", "description": "Short, honest description of the intended outcome."},
    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 1800, "default": 300},
    "network": {"type": "boolean", "default": False, "description": "Whether network access is expected."},
    "env": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Non-secret environment variables."},
    "secret_env": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Environment name to OS-keystore reference; never put secret values here."},
    "expected_artifacts": {
        "type": "array", "maxItems": 16, "description": "Files that must be verified before success.",
        "items": {"type": "object", "properties": {
            "path": {"type": "string"},
            "kind": {"type": "string", "enum": ["file", "pdf"]},
            "required": {"type": "boolean", "default": True},
            "overwrite": {"type": "boolean", "default": False},
        }, "required": ["path"], "additionalProperties": False},
    },
}


def _register(tool: str, schema: dict) -> None:
    async def execute(args: dict, ctx):
        spec, decision = _request(tool, args)
        return await run_managed(spec, decision, ctx)

    gate.register(
        tool, execute,
        validate=_hook(tool, lambda spec, _decision: spec),
        tier=_hook(tool, lambda _spec, decision: decision.tier),
        destructive=_hook(tool, lambda _spec, decision: decision.destructive),
        mutating=_hook(tool, lambda _spec, decision: decision.mutating),
        redact=_hook(tool, lambda spec, _decision: redacted_args(spec)),
        persist_args=_hook(tool, lambda spec, _decision: redacted_args(spec)),
        approval_fingerprint=_hook(tool, lambda spec, _decision: spec.fingerprint),
        user_intent=lambda _args, text: "run" in text.lower() or "execute" in text.lower(),
        task=True, supports_pause=False,
        title=lambda args: args.get("purpose") or f"Run {tool}",
        summary=lambda args: f"I want to {args.get('purpose') or 'run a managed command'}.",
        schema=schema,
    )


_register("command_run", _schema(
    "Run one executable with structured arguments and no shell. Prefer dedicated file tools for simple file/folder work. "
    "Use this for build, test, Git, and installed CLI programs. Risk is classified by the Brain.",
    {"executable": {"type": "string", "description": "Program name or absolute executable path."}, **_COMMON},
    ["executable", "args", "cwd", "purpose", "expected_artifacts"],
))
_register("script_run", _schema(
    "Run a generated Python or PowerShell script as a managed task. Use when structured tools cannot produce the artifact; "
    "for PDFs, declare the PDF in expected_artifacts so Halo verifies it before reporting success.",
    {
        "language": {"type": "string", "enum": ["python", "powershell"]},
        "source": {"type": "string", "description": "Generated source; approval and persistence show only its hash."},
        **_COMMON,
    },
    ["language", "source", "args", "cwd", "purpose", "expected_artifacts"],
))
