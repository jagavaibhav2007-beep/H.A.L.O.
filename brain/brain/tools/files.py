"""Lane-1 local file tools (Phase 2 Step 7).

Importing this module registers the real file tools into gate.TOOLS.
Rule 7: every tool resolves its paths (symlinks included) FIRST; anything
outside the configured project roots is Tier-3 territory, never silently
allowed. Classification is arg-predicate callables on the registry entries;
the gate stays the one choke point.
"""

from __future__ import annotations

import asyncio
import hashlib
import shlex
import shutil
import subprocess
import uuid
from itertools import islice
from pathlib import Path

from brain import gate, store

_READ_CAP = 64 * 1024
_LIST_CAP = 500
_SEARCH_CAP = 200
_BATCH_CAP = 200
_UNDO_BLOB_CAP = 10 * 1024 * 1024


# ------------------------------------------------------------------- roots --


def _roots() -> list[Path]:
    raw = store.get_setting("project_roots")
    if raw:
        return [Path(p).expanduser().resolve() for p in raw]
    home = Path.home()
    return [(home / name).resolve() for name in ("Desktop", "Documents", "Downloads")]


def _resolve(p: str) -> Path:
    return Path(p).expanduser().resolve()


def _in_roots(p: Path) -> bool:
    return any(p.is_relative_to(r) for r in _roots())


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _uncollide(p: Path) -> Path:
    """Name collision at execution time (Tier-2 race) -> ' (2)' before the
    extension, counting up until free. The ACTUAL final path is what the
    inverse records."""
    if not p.exists():
        return p
    n = 2
    while True:
        cand = p.with_name(f"{p.stem} ({n}){p.suffix}")
        if not cand.exists():
            return cand
        n += 1


# ------------------------------------------------------------- read-only ---


def _file_read(args: dict) -> str:
    p = _resolve(args["path"])
    data = p.read_bytes()
    text = data[:_READ_CAP].decode("utf-8", errors="replace")
    if len(data) > _READ_CAP:
        text += f"\n... [truncated at 64KB of {len(data)} bytes]"
    return text


def _dir_list(args: dict) -> list[dict]:
    p = _resolve(args["path"])
    return [
        {"name": c.name, "is_dir": c.is_dir()}
        for c in islice(sorted(p.iterdir()), _LIST_CAP)
    ]


def _file_search(args: dict) -> list[str]:
    p = _resolve(args["path"])
    return [str(m.relative_to(p)) for m in islice(p.glob(args["pattern"]), _SEARCH_CAP)]


def _run_cmd(args: dict) -> dict:
    # Trust boundary: cmd.exe would honor > | & redirection in joined args,
    # and git's --output/-o writes files from "read-only" subcommands.
    if any(c in args["cmd"] for c in "><|&") or "--output" in args["cmd"]:
        raise ValueError(f"not allowed: shell redirection/output flags refused ({args['cmd']!r})")
    parts = shlex.split(args["cmd"])
    allowed = bool(parts) and (
        (parts[0] == "git" and len(parts) > 1 and parts[1] in ("status", "log", "diff"))
        or parts[0] in ("dir", "ls")
    )
    if not allowed:
        # Refused inside the fn per the plan: arbitrary shell is out of scope
        # until a sandbox exists -- this is not Tier-3, it's a no.
        raise ValueError(f"not allowed: only git status/log/diff, dir, ls (got {args['cmd']!r})")
    if parts[0] == "dir":
        parts = ["cmd", "/c"] + parts  # dir is a cmd.exe builtin on Windows
    r = subprocess.run(parts, capture_output=True, text=True, timeout=10, shell=False)
    out = r.stdout
    if len(out) > _READ_CAP:
        out = out[:_READ_CAP] + "\n...[truncated]"
    return {"code": r.returncode, "stdout": out, "stderr": r.stderr[:4096]}


# ---------------------------------------------------------------- writes ---


def _file_create(args: dict) -> dict:
    p = _resolve(args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args["content"], encoding="utf-8")
    return {"path": str(p), "sha256": _sha(p)}


def _create_risky(args: dict) -> bool:
    p = _resolve(args["path"])
    return p.exists() or not _in_roots(p)


def _create_inverse(args: dict, result: dict) -> dict:
    # expected_sha256 makes the delete a hash-guarded Tier-2 (see _file_delete).
    return {
        "tool": "file_delete",
        "args": {"path": result["path"], "expected_sha256": result["sha256"]},
        "precondition": {"path": result["path"], "sha256": result["sha256"]},
    }


def _file_edit(args: dict) -> dict:
    p = _resolve(args["path"])
    text = p.read_text(encoding="utf-8")
    count = text.count(args["old"])
    if count == 0:
        raise ValueError("old text not found in file")
    if count > 1:
        raise ValueError(f"old text is ambiguous: {count} occurrences (need exactly 1)")
    p.write_text(text.replace(args["old"], args["new"], 1), encoding="utf-8")
    return {"path": str(p), "sha256": _sha(p)}


def _edit_inverse(args: dict, result: dict) -> dict:
    return {
        "tool": "file_edit",
        "args": {"path": result["path"], "old": args["new"], "new": args["old"]},
        "precondition": {"path": result["path"], "sha256": result["sha256"]},
    }


def _file_move(args: dict) -> dict:
    src = _resolve(args["src"])
    dst = _resolve(args["dst"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst = _uncollide(dst)
    shutil.move(str(src), str(dst))
    out = {"src": str(src), "dst": str(dst)}
    if dst.is_file():
        out["sha256"] = _sha(dst)
    return out


def _move_risky(args: dict) -> bool:
    src = _resolve(args["src"])
    dst = _resolve(args["dst"])
    return dst.exists() or not (_in_roots(src) and _in_roots(dst))


def _move_inverse(args: dict, result: dict) -> dict:
    inv = {"tool": "file_move", "args": {"src": result["dst"], "dst": result["src"]}}
    if "sha256" in result:
        inv["precondition"] = {"path": result["dst"], "sha256": result["sha256"]}
    # ponytail: directory moves get no precondition (the gate's check is
    # file-only) -- a per-entry dir check is the upgrade if it ever matters.
    return inv


def _file_delete(args: dict) -> None:
    p = _resolve(args["path"])
    want = args.get("expected_sha256")
    if want and _sha(p) != want:
        raise ValueError("file changed since; refusing to delete")
    data = p.read_bytes()
    p.unlink()
    args["_prior"] = data  # handed to the inverse builder, never leaves the fn


def _delete_inverse(args: dict, result) -> dict | None:
    data = args.pop("_prior", None)
    if data is None or len(data) > _UNDO_BLOB_CAP:
        # ponytail: >10MB deletes are honestly undoable:false rather than
        # hoarding blobs in the action log; a spill-to-disk trash dir is the upgrade.
        return None
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return None  # ponytail: text files only; binary restore needs a blob store
    return {"tool": "file_create", "args": {"path": args["path"], "content": content}}


async def _dir_organize(args: dict) -> dict:
    moves = args["moves"]
    if len(moves) > _BATCH_CAP:
        raise ValueError(f"too many moves ({len(moves)}; max {_BATCH_CAP} per batch)")

    def run() -> dict:
        task_id = f"organize-{uuid.uuid4().hex[:8]}"
        store.connect()
        store.upsert_task(
            task_id, state="running", lane=1,
            title=f"Organize {len(moves)} files in {args['path']}",
            step=0, steps_total=len(moves),
        )
        done: list[dict] = []
        skipped: list[str] = []
        # ponytail: no cooperative cancel between files yet -- arrives with
        # real long tasks; and no stepped task_state progress frames -- tool
        # fns get a broadcast context in Step 9.
        for m in moves:
            src = _resolve(m["src"])
            if not src.exists():
                skipped.append(str(src))  # vanished mid-plan: skip, don't crash (D6)
                continue
            dst = _uncollide(_resolve(m["dst"]))
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            done.append({"src": str(src), "dst": str(dst)})
        store.upsert_task(task_id, state="done", step=len(done), steps_total=len(moves))
        return {"task_id": task_id, "moves": done, "skipped": skipped}

    return await asyncio.to_thread(run)


def _organize_risky(args: dict) -> bool:
    for m in args["moves"]:
        src = _resolve(m["src"])
        dst = _resolve(m["dst"])
        if dst.exists() or not (_in_roots(src) and _in_roots(dst)):
            return True
    return False


def _organize_inverse(args: dict, result: dict) -> dict | None:
    done = result["moves"]
    if not done:
        return None
    rev = [{"src": m["dst"], "dst": m["src"]} for m in reversed(done)]
    return {
        "tool": "dir_organize",
        "args": {"path": args["path"], "moves": rev},
        # ponytail: cheap check -- first reversed move's file still where we
        # put it; per-file sha256 preconditions are the upgrade.
        "precondition": {"path": rev[0]["src"]},
    }


# ---------------------------------------------------------- registration ---


def _path_tier(key: str, inside: int):
    def tier(args: dict) -> int:
        return inside if _in_roots(_resolve(args[key])) else 3

    return tier


gate.register(
    "file_read", _file_read, tier=_path_tier("path", 1),
    summary=lambda a: f"I want to read {a['path']}.",
)
gate.register(
    "dir_list", _dir_list, tier=_path_tier("path", 1),
    summary=lambda a: f"I want to list {a['path']}.",
)
gate.register(
    "file_search", _file_search, tier=_path_tier("path", 1),
    summary=lambda a: f"I want to search {a['path']} for {a['pattern']}.",
)
gate.register(
    "file_create", _file_create,
    tier=lambda a: 3 if _create_risky(a) else 2, destructive=_create_risky,
    redact=lambda a: {"path": a["path"], "content": f"<{len(a.get('content', ''))} chars>"},
    summary=lambda a: f"I want to create {a['path']}.",
    inverse=_create_inverse,
)
gate.register(
    "file_edit", _file_edit, tier=_path_tier("path", 2),
    redact=lambda a: {
        "path": a["path"],
        "old": f"<{len(a.get('old', ''))} chars>",
        "new": f"<{len(a.get('new', ''))} chars>",
    },
    summary=lambda a: f"I want to edit {a['path']}.",
    inverse=_edit_inverse,
)
gate.register(
    "file_move", _file_move,
    tier=lambda a: 3 if _move_risky(a) else 2, destructive=_move_risky,
    summary=lambda a: f"I want to move {a['src']} to {a['dst']}.",
    inverse=_move_inverse,
)
gate.register(
    "file_delete", _file_delete, tier=3, destructive=True,
    redact=lambda a: {"path": a["path"]},  # drops expected_sha256/_prior noise
    summary=lambda a: f"I want to delete {a['path']}.",
    inverse=_delete_inverse,
)
gate.register(
    "dir_organize", _dir_organize,
    tier=lambda a: 3 if _organize_risky(a) else 2, destructive=_organize_risky,
    summary=lambda a: f"I want to organize {len(a['moves'])} files in {a['path']}.",
    inverse=_organize_inverse,
)
gate.register(
    "run_readonly_cmd", _run_cmd, tier=1,
    summary=lambda a: f"I want to run `{a['cmd']}`.",
)
