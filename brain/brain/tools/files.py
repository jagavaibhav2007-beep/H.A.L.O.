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
import os
import shlex
import shutil
import subprocess
import uuid
from datetime import datetime
from itertools import islice
from pathlib import Path

from brain import extract, gate, store

_READ_CAP = 8 * 1024  # ponytail: 8KB/~2k tokens (systemdesign/13-document-ingestion.md
                       # Layer 0); offset/limit below let the model page past it.
_CMD_HEAD_CAP = 4 * 1024
_CMD_TAIL_CAP = 4 * 1024
_LIST_CAP = 500  # max entries a listing may return (limit clamp)
_LIST_DEFAULT = 50  # default when the model gives no limit
_LIST_TEXT_CAP = 7 * 1024  # keep listing text under gate._RESULT_CAP after JSON-string escaping
_SEARCH_CAP = 200  # max search matches returned (limit clamp)
_SEARCH_SCAN_CAP = 2000  # ponytail: walk at most this many glob matches before sorting by mtime --
                          # newest-first is over this window, not a million-file tree; raise if a real ask needs deeper
_BATCH_CAP = 200
_UNDO_BLOB_CAP = 10 * 1024 * 1024


# ------------------------------------------------------------------- roots --


def _roots() -> list[Path]:
    home = Path.home()
    defaults = [(home / name).resolve() for name in ("Desktop", "Documents", "Downloads")]
    raw = store.get_setting("project_roots")
    if not isinstance(raw, list):
        return defaults

    configured: list[Path] = []
    persisted: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            root = Path(value).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if not root.is_dir():
            continue
        if root not in configured:
            configured.append(root)
            persisted.append(str(root))
    # Existing profiles treated project_roots as a full replacement, which let
    # a stale test/temp path silently remove Desktop/Documents/Downloads. Until
    # an explicit "replace defaults" setting exists, custom roots are additive.
    if persisted != raw:
        store.set_setting("project_roots", persisted)
    return list(dict.fromkeys([*defaults, *configured]))


def project_roots_state() -> dict:
    """UI-safe accessible-root projection plus stale configured entries."""
    raw = store.get_setting("project_roots")
    configured = raw if isinstance(raw, list) else []
    roots = _roots()  # validates and persists pruning first
    accepted = set(store.get_setting("project_roots") or [])
    pruned: list[str] = []
    for value in configured:
        if not isinstance(value, str):
            continue
        try:
            normalized = str(Path(value).expanduser().resolve())
        except (OSError, RuntimeError):
            normalized = ""
        if normalized not in accepted:
            pruned.append(value)
    return {"roots": [str(root) for root in roots], "pruned": pruned}


def _resolve(p: str) -> Path:
    q = Path(p).expanduser()
    if not q.is_absolute():
        q = Path.home() / q  # bare/relative paths anchor at home, not the Brain's CWD
    return q.resolve()


def _in_roots(p: Path) -> bool:
    return any(p.is_relative_to(r) for r in _roots())


def _sha(p: Path) -> str:
    digest = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    try:
        text = extract.extract_text(p)
    except (ValueError, MemoryError):
        # ValueError: honest "no extractable text"/unsupported-format/too-large
        # error -> the tool error. MemoryError: the file did not fit once, so
        # the raw-read fallback below would only try the same allocation again.
        raise
    except Exception:
        # Extraction library choked on a malformed file -- fall back to the
        # old best-effort raw read rather than losing the file entirely.
        with p.open("rb") as handle:
            text = handle.read().decode("utf-8", errors="replace")

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    offset = int(args["offset"]) if args.get("offset") else 1
    start = max(offset - 1, 0)
    limit = int(args["limit"]) if args.get("limit") else None
    window = lines[start : start + limit] if limit is not None else lines[start:]
    end = start + len(window)

    body = "".join(window)
    encoded = body.encode("utf-8")
    if len(encoded) > _READ_CAP:
        body = encoded[:_READ_CAP].decode("utf-8", errors="ignore")
        shown_lines = body.count("\n") + (1 if body and not body.endswith("\n") else 0)
        next_offset = start + shown_lines + 1
        remaining = total_lines - (start + shown_lines)
        body += (
            f"\n\n... [truncated at {_READ_CAP // 1024}KB; {remaining} more line(s) remain of "
            f"{total_lines} total in {p.name} -- call file_read again with offset={next_offset} to page on]"
        )
        return body

    if start > 0 or end < total_lines:
        remaining = total_lines - end
        if remaining > 0:
            body += (
                f"\n\n[showing lines {start + 1}-{end} of {total_lines} total in {p.name}; "
                f"{remaining} more line(s) remain -- call file_read again with offset={end + 1} to page on]"
            )
        else:
            body += f"\n\n[showing lines {start + 1}-{end} of {total_lines} total in {p.name}]"
    return body


def _clamp_limit(raw, default: int, cap: int) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, cap))


def _fmt_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _entry_line(display: str, st, is_dir: bool) -> str:
    size = "-" if is_dir else str(st.st_size)
    return f"{_fmt_mtime(st.st_mtime)}  {size:>12}  {display}{'/' if is_dir else ''}"


def _bounded_listing(rows: list[tuple[float, str]], skipped: int, limit: int, cap: int, kind: str) -> str:
    """Sort (mtime, line) rows newest-first, take `limit`, then trim to
    _LIST_TEXT_CAP bytes so a big limit can't overflow the gate result cap and
    get byte-sliced into garbage. One header line states counts + how to widen."""
    rows.sort(key=lambda r: r[0], reverse=True)
    total = len(rows)
    lines = [line for _, line in rows[:limit]]
    while lines and len("\n".join(lines)) > _LIST_TEXT_CAP:
        lines.pop()
    shown = len(lines)
    note = f"{shown} of {total} {kind}, newest first"
    if skipped:
        note += f", {skipped} unreadable skipped"
    if shown < total:
        note += f" — raise limit (max {cap}) or narrow the path for more"
    body = "\n".join(lines)
    return f"[{note}]\n{body}" if body else f"[{note}]"


def _dir_text(p: Path, limit: int) -> str:
    """Immediate children of `p`, newest-first, as flat text. Uses os.scandir
    (caches stat from the walk on Windows) and SKIPS any entry whose stat()
    raises -- OneDrive placeholders, half-written .crdownload files, broken
    junctions, or a file deleted mid-walk. One unguarded raise would fail the
    whole call, and the model answers a tool error by retrying: the exact loop
    Track A exists to remove (systemdesign/14 A1)."""
    rows: list[tuple[float, str]] = []
    skipped = 0
    with os.scandir(p) as it:
        for entry in it:
            try:
                st = entry.stat()
                is_dir = entry.is_dir()
            except OSError:
                skipped += 1
                continue
            rows.append((st.st_mtime, _entry_line(entry.name, st, is_dir)))
    return _bounded_listing(rows, skipped, limit, _LIST_CAP, "entries")


def _dir_list(args: dict) -> str:
    p = _resolve(args["path"])
    return _dir_text(p, _clamp_limit(args.get("limit"), _LIST_DEFAULT, _LIST_CAP))


def _file_search(args: dict) -> str:
    p = _resolve(args["path"])
    pattern = args["pattern"]
    pattern_path = Path(pattern)
    if pattern_path.is_absolute() or ".." in pattern_path.parts:
        raise ValueError("search pattern must stay below the requested directory")
    limit = _clamp_limit(args.get("limit"), _LIST_DEFAULT, _SEARCH_CAP)
    rows: list[tuple[float, str]] = []
    skipped = 0
    for match in islice(p.glob(pattern), _SEARCH_SCAN_CAP):
        resolved = match.resolve()
        if not resolved.is_relative_to(p):
            raise ValueError("search result escaped the requested directory")
        try:
            st = match.stat()
            is_dir = match.is_dir()
        except OSError:
            skipped += 1  # same stat-guard as _dir_text: skip, never fail the call
            continue
        rows.append((st.st_mtime, _entry_line(str(match.relative_to(p)), st, is_dir)))
    return _bounded_listing(rows, skipped, limit, _SEARCH_CAP, "match(es)")


def _run_cmd(args: dict) -> dict:
    # Trust boundary: cmd.exe would honor > | & redirection in joined args,
    # and git's --output/-o writes files from "read-only" subcommands.
    if any(c in args["cmd"] for c in "><|&\r\n") or "--output" in args["cmd"]:
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
    cwd = Path.cwd().resolve()
    if not _in_roots(cwd):
        raise ValueError(f"not allowed: command working directory is outside project roots ({cwd})")
    if parts[0] in ("dir", "ls"):
        allowed_flags = {"-a", "-l", "-la", "-al", "/b"}
        operands = [part for part in parts[1:] if part not in allowed_flags]
        unknown_flags = [part for part in operands if part.startswith("-")]
        if unknown_flags or len(operands) > 1:
            raise ValueError("not allowed: dir/ls accepts at most one path and basic listing flags")
        target = (cwd / operands[0]).resolve() if operands and not Path(operands[0]).is_absolute() else (
            Path(operands[0]).expanduser().resolve() if operands else cwd
        )
        if not _in_roots(target):
            raise ValueError(f"not allowed: command path is outside project roots ({target})")
        # A3: one listing code path -- dir/ls returns the SAME newest-first,
        # mtime+size formatter as dir_list, so no caller can regress to a
        # timestamp-free listing (systemdesign/14 A3).
        if target.is_dir():
            out = _dir_text(target, _LIST_DEFAULT)
        else:
            try:
                out = _entry_line(target.name, target.stat(), False)
            except OSError:
                out = f"[unreadable: {target.name}]"
        return {"code": 0, "stdout": out, "stderr": ""}

    dangerous = {"--no-index", "--ext-diff", "--textconv"}
    if any(part in dangerous or part.startswith("-o") for part in parts[2:]):
        raise ValueError("not allowed: git option can escape roots, execute helpers, or write output")
    path_mode = False
    for part in parts[2:]:
        if part == "--":
            path_mode = True
            continue
        candidate = Path(part).expanduser()
        looks_like_path = path_mode or candidate.is_absolute() or part.startswith(("./", "../", ".\\", "..\\"))
        if looks_like_path:
            candidate = candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve()
            if not _in_roots(candidate):
                raise ValueError(f"not allowed: git path is outside project roots ({candidate})")
    # Git reads repository, user, and system configuration even for ostensibly
    # read-only commands. Disable every helper-bearing surface we expose:
    # fsmonitor, hooks, external diff/textconv, pagers, prompts, and global/
    # system config. Local config remains available for repository semantics,
    # but the command-line overrides win for the executable helper keys.
    git_env = os.environ.copy()
    git_env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
    })
    safe_parts = [
        "git",
        "-c", "core.fsmonitor=false",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "diff.external=",
        parts[1],
    ]
    if parts[1] in ("diff", "log"):
        safe_parts.extend(("--no-ext-diff", "--no-textconv"))
    safe_parts.extend(parts[2:])
    r = subprocess.run(
        safe_parts, cwd=cwd, capture_output=True, text=True, timeout=10,
        shell=False, env=git_env,
    )
    out = r.stdout
    cap = _CMD_HEAD_CAP + _CMD_TAIL_CAP
    if len(out) > cap:
        head, tail = out[:_CMD_HEAD_CAP], out[-_CMD_TAIL_CAP:]
        elided = len(out) - _CMD_HEAD_CAP - _CMD_TAIL_CAP
        out = f"{head}\n\n... [{elided} chars elided from the middle of {len(out)} total] ...\n\n{tail}"
    return {"code": r.returncode, "stdout": out, "stderr": r.stderr[:4096]}


# ---------------------------------------------------------------- writes ---


def _atomic_write(p: Path, text: str) -> None:
    """Write `text` to `p` all-or-nothing. `open("w")` truncates and then
    streams, so a crash/full disk/AV grab mid-write leaves a half file -- and
    since the recorded inverse's sha256 precondition then no longer matches,
    undo *refuses* and the original is simply gone. Write a sibling temp (same
    directory: os.replace cannot cross volumes on Windows) and rename it over.

    newline="" is load-bearing on both this and every read: without it Python
    translates "\\n" to os.linesep, which silently rewrites an LF file's every
    line ending -- irreversibly, since the inverse records the post-write hash.
    """
    tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _file_create(args: dict) -> dict:
    p = _resolve(args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    # open("x") reserves the name so a raced create still raises FileExistsError
    # from the OS -- no hand-rolled exists() check, which would be TOCTOU.
    with p.open("x", encoding="utf-8", newline=""):
        pass
    try:
        _atomic_write(p, args["content"])
    except BaseException:
        p.unlink(missing_ok=True)  # don't leave the empty reservation behind
        raise
    return {"path": str(p), "sha256": _sha(p)}


def _dir_create(args: dict) -> dict:
    p = _resolve(args["path"])
    p.mkdir(parents=True, exist_ok=False)
    return {"path": str(p)}


def _dir_remove_empty(args: dict) -> None:
    _resolve(args["path"]).rmdir()


def _dir_create_inverse(_args: dict, result: dict) -> dict:
    return {"tool": "dir_remove_empty", "args": {"path": result["path"]}}


def _create_risky(args: dict) -> bool:
    p = _resolve(args["path"])
    return p.exists() or _is_repository_control(p) or not _in_roots(p)


def _create_inverse(args: dict, result: dict) -> dict:
    # expected_sha256 makes the delete a hash-guarded Tier-2 (see _file_delete).
    return {
        "tool": "file_delete",
        "args": {"path": result["path"], "expected_sha256": result["sha256"]},
        "precondition": {"path": result["path"], "sha256": result["sha256"]},
    }


def _file_edit(args: dict) -> dict:
    p = _resolve(args["path"])
    # newline="" on the read too: read_text normalizes line endings, so editing
    # one snippet in an LF-only file on Windows would silently rewrite every
    # line to CRLF (and the recorded inverse could never restore them).
    with p.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    count = text.count(args["old"])
    if count == 0:
        raise ValueError("old text not found in file")
    if count > 1:
        raise ValueError(f"old text is ambiguous: {count} occurrences (need exactly 1)")
    _atomic_write(p, text.replace(args["old"], args["new"], 1))
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
    return (
        dst.exists()
        or _is_repository_control(src)
        or _is_repository_control(dst)
        or not (_in_roots(src) and _in_roots(dst))
    )


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
    with p.open("rb") as handle:
        data = handle.read(_UNDO_BLOB_CAP + 1)
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
    return {
        "tool": "file_create",
        "args": {"path": args["path"], "content": content},
        "precondition": {"path": args["path"], "must_be_absent": True},
    }


async def _dir_organize(args: dict, ctx=None) -> dict:
    moves = args["moves"]
    if len(moves) > _BATCH_CAP:
        raise ValueError(f"too many moves ({len(moves)}; max {_BATCH_CAP} per batch)")

    done: list[dict] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for index, m in enumerate(moves, start=1):
        if ctx is not None:
            await ctx.checkpoint()

        def move_one() -> tuple[str, dict | str]:
            src = _resolve(m["src"])
            if not src.exists():
                return "skipped", str(src)
            if not src.is_file():
                return "failed", {"src": str(src), "error": "batch organize accepts files only"}
            try:
                dst = _uncollide(_resolve(m["dst"]))
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError as exc:
                return "failed", {"src": str(src), "error": str(exc)}
            result = {"src": str(src), "dst": str(dst)}
            if dst.is_file():
                result["sha256"] = _sha(dst)
            return "done", result

        kind, result = await asyncio.to_thread(move_one)
        if kind == "done":
            done.append(result)
            if ctx is not None:
                move_args = {"src": result["src"], "dst": result["dst"]}
                # Keep one durable, non-undoable receipt per completed move for
                # review/audit. The task's final batch action remains the sole
                # undoable row, so users cannot partially consume a batch undo.
                await asyncio.to_thread(
                    store.record_action,
                    "file_move",
                    move_args,
                    ctx.tier,
                    ctx.lane,
                    "task_step_ok",
                    False,
                    None,
                    ctx.task_id,
                )
                await ctx.broadcast("activity", {
                    "text": f"Moved {result['src']} to {result['dst']}.",
                    "narrate": False,
                    "task_id": ctx.task_id,
                    "undoable": False,
                    "tier": ctx.tier,
                    "lane": ctx.lane,
                })
        elif kind == "skipped":
            skipped.append(result)
        else:
            failed.append(result)
        checkpoint = {"moves": done, "skipped": skipped, "failed": failed}
        if ctx is not None:
            await ctx.progress(
                index,
                len(moves),
                f"{kind}: {Path(m['src']).name}",
                checkpoint=checkpoint,
            )
    return {"moves": done, "skipped": skipped, "failed": failed}


def _organize_risky(args: dict) -> bool:
    for m in args["moves"]:
        src = _resolve(m["src"])
        dst = _resolve(m["dst"])
        if (
            dst.exists()
            or _is_repository_control(src)
            or _is_repository_control(dst)
            or not (_in_roots(src) and _in_roots(dst))
        ):
            return True
    return False


def _organize_inverse(args: dict, result: dict) -> dict | None:
    done = result["moves"]
    if not done:
        return None
    if any(not m.get("sha256") for m in done):
        return None
    rev = [
        {"src": m["dst"], "dst": m["src"], "expected_sha256": m["sha256"]}
        for m in reversed(done)
    ]
    return {
        "tool": "dir_restore_batch",
        "args": {"moves": rev},
        "precondition": {"batch": [
            {"path": move["src"], "sha256": move["expected_sha256"]}
            for move in rev
        ] + [
            {"path": move["dst"], "must_be_absent": True}
            for move in rev
        ]},
    }


def _dir_restore_batch(args: dict) -> dict:
    """All-preflight batch reversal with rollback on a mid-commit OS error."""
    moves = args.get("moves")
    if not isinstance(moves, list) or not moves or len(moves) > _BATCH_CAP:
        raise ValueError("invalid batch reversal")
    planned: list[tuple[Path, Path, str]] = []
    for move in moves:
        src = _resolve(move["src"])
        dst = _resolve(move["dst"])
        expected = move["expected_sha256"]
        if not src.is_file() or _sha(src) != expected:
            raise ValueError(f"batch undo precondition failed: changed or missing {src}")
        if dst.exists():
            raise ValueError(f"batch undo precondition failed: restore target exists {dst}")
        planned.append((src, dst, expected))

    completed: list[tuple[Path, Path]] = []
    try:
        for src, dst, _expected in planned:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            completed.append((src, dst))
    except OSError as exc:
        rollback_errors: list[str] = []
        for original_src, restored_dst in reversed(completed):
            try:
                original_src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(restored_dst), str(original_src))
            except OSError as rollback_exc:
                rollback_errors.append(f"{restored_dst}: {rollback_exc}")
        detail = f"; rollback incomplete: {'; '.join(rollback_errors)}" if rollback_errors else "; rolled back"
        raise OSError(f"batch undo failed at commit: {exc}{detail}") from exc
    return {"restored": [str(dst) for _src, dst, _expected in planned]}


# ---------------------------------------------------------- registration ---


def _path_tier(key: str, inside: int):
    def tier(args: dict) -> int:
        path = _resolve(args[key])
        if inside > 1 and _is_repository_control(path):
            return 3
        return inside if _in_roots(path) else 3

    return tier


_CONTROL_NAMES = {".gitattributes", ".gitmodules", ".gitconfig"}
_CONTROL_DIRS = {".git", ".hg", ".svn"}


def _is_repository_control(path: Path) -> bool:
    """Whether changing path can alter how source-control commands execute."""
    lowered = {part.casefold() for part in path.parts}
    return path.name.casefold() in _CONTROL_NAMES or bool(lowered & _CONTROL_DIRS)


def _user_intent(verbs: tuple[str, ...], *path_keys: str):
    """Conservative current-message authority check for approval-free writes."""
    def allowed(args: dict, user_text: str) -> bool:
        text = " ".join(user_text.casefold().replace("\\", "/").split())
        words = set(text.replace("/", " ").replace(".", " ").split())
        if not any(verb in words for verb in verbs):
            return False
        candidates: set[str] = set()
        for key in path_keys:
            raw = args.get(key)
            if not isinstance(raw, str):
                continue
            normalized = raw.casefold().replace("\\", "/")
            candidates.add(normalized)
            candidates.add(Path(raw).name.casefold())
        candidates.discard("")
        return any(candidate in text for candidate in candidates)

    return allowed


def _organize_user_intent(args: dict, user_text: str) -> bool:
    paths = {"path": args.get("path")}
    for index, move in enumerate(args.get("moves") or []):
        if isinstance(move, dict):
            paths[f"src_{index}"] = move.get("src")
            paths[f"dst_{index}"] = move.get("dst")
    return _user_intent(("organize", "sort", "move"), *paths.keys())(paths, user_text)


def _schema(description: str, props: dict, required: list[str]) -> dict:
    return {
        "description": description,
        "parameters": {"type": "object", "properties": props, "required": required},
    }


_PATH = {"type": "string", "description": "Absolute path, or one starting with ~."}

# Descriptions are written for the model's benefit, not the user's: they say
# what the tool does, what it costs, and when NOT to reach for it. The gate
# still decides whether a call runs -- these only shape which tool gets asked
# for. ponytail: hand-written strings, not derived from the fns; if they drift
# the fix is to write them here, no codegen.

gate.register(
    "file_read", _file_read, tier=_path_tier("path", 1),
    summary=lambda a: f"I want to read {a['path']}.",
    schema=_schema(
        "Read a file and return its contents as markdown/plain text (extracted "
        "from PDF/DOCX/XLSX/HTML as needed; other text and source files pass "
        "through as-is). Capped at 8KB per call -- if the response says lines "
        "remain, call file_read again with offset set to the number it gives you "
        "to page through the rest instead of assuming the file ended. Use this "
        "before editing a file so you know its exact current text.",
        {
            "path": _PATH,
            "offset": {
                "type": "integer",
                "description": "1-based line number to start reading from. Omit to start at line 1.",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of lines to read from offset. Omit to read to the 8KB cap.",
            },
        },
        ["path"],
    ),
)
gate.register(
    "dir_list", _dir_list, tier=_path_tier("path", 1),
    summary=lambda a: f"I want to list {a['path']}.",
    schema=_schema(
        "List a directory's immediate contents, NEWEST FIRST, one flat line per "
        "entry: modified time, size in bytes, name (directories end in '/'). "
        "Returns the `limit` most-recently-modified entries (default 50) with a "
        "header stating the total -- so to answer 'latest'/'most recent' you do "
        "NOT need to read anything, the order already answers it. Call again with "
        "a larger `limit` (max 500) to see older entries. Not recursive -- use "
        "file_search for that.",
        {
            "path": _PATH,
            "limit": {
                "type": "integer",
                "description": "Max entries to return, newest first (default 50, max 500).",
            },
        },
        ["path"],
    ),
)
gate.register(
    "file_search", _file_search, tier=_path_tier("path", 1),
    summary=lambda a: f"I want to search {a['path']} for {a['pattern']}.",
    schema=_schema(
        "Find files by glob pattern under a directory, NEWEST FIRST. Matches "
        "names/paths, not file contents. Returns the `limit` most-recently-"
        "modified matches (default 50, max 200) as flat lines: modified time, "
        "size, path relative to the search root. Use this to find a specific "
        "file by name (e.g. '*.pdf'); the newest-first order also answers "
        "'the latest matching file' without reading anything.",
        {
            "path": _PATH,
            "pattern": {
                "type": "string",
                "description": "Glob relative to path, e.g. '*.pdf' or '**/*.py' to recurse.",
            },
            "limit": {
                "type": "integer",
                "description": "Max matches to return, newest first (default 50, max 200).",
            },
        },
        ["path", "pattern"],
    ),
)
gate.register(
    "file_create", _file_create,
    tier=lambda a: 3 if _create_risky(a) else 2, destructive=_create_risky,
    mutating=True,
    user_intent=_user_intent(("create", "write", "make", "save"), "path"),
    redact=lambda a: {"path": a["path"], "content": f"<{len(a.get('content', ''))} chars>"},
    summary=lambda a: f"I want to create {a['path']}.",
    inverse=_create_inverse,
    schema=_schema(
        "Write a new text file, creating parent directories as needed. The call "
        "fails safely if the path already exists -- to change an existing file "
        "use file_edit instead.",
        {
            "path": _PATH,
            "content": {"type": "string", "description": "Full UTF-8 text of the file."},
        },
        ["path", "content"],
    ),
)
gate.register(
    "dir_create", _dir_create, tier=_path_tier("path", 2), mutating=True,
    user_intent=_user_intent(("create", "make", "folder", "directory"), "path"),
    summary=lambda a: f"I want to create the folder {a['path']}.",
    inverse=_dir_create_inverse,
    schema=_schema(
        "Create one directory, including missing parents. Use this instead of a shell or generated script for a simple folder request. Fails if the final directory already exists.",
        {"path": _PATH}, ["path"],
    ),
)
gate.register("dir_remove_empty", _dir_remove_empty, tier=2)
gate.register(
    "file_edit", _file_edit, tier=_path_tier("path", 2),
    mutating=True,
    user_intent=_user_intent(("edit", "change", "update", "replace", "fix"), "path"),
    redact=lambda a: {
        "path": a["path"],
        "old": f"<{len(a.get('old', ''))} chars>",
        "new": f"<{len(a.get('new', ''))} chars>",
    },
    summary=lambda a: f"I want to edit {a['path']}.",
    inverse=_edit_inverse,
    schema=_schema(
        "Replace one exact snippet of text in an existing file. 'old' must appear "
        "EXACTLY ONCE -- zero or multiple matches fail without changing anything, "
        "so read the file first and include enough surrounding text to be unique.",
        {
            "path": _PATH,
            "old": {"type": "string", "description": "Exact text to replace; must match once."},
            "new": {"type": "string", "description": "Replacement text."},
        },
        ["path", "old", "new"],
    ),
)
gate.register(
    "file_move", _file_move,
    tier=lambda a: 3 if _move_risky(a) else 2, destructive=_move_risky,
    mutating=True,
    user_intent=_user_intent(("move", "rename", "organize", "sort"), "src", "dst"),
    summary=lambda a: f"I want to move {a['src']} to {a['dst']}.",
    inverse=_move_inverse,
    schema=_schema(
        "Move or rename a single file or directory. If the destination is taken, "
        "the name is suffixed ' (2)' rather than overwriting. For several files "
        "at once, prefer dir_organize.",
        {
            "src": {"type": "string", "description": "Existing path to move."},
            "dst": {"type": "string", "description": "Destination path, including the new filename."},
        },
        ["src", "dst"],
    ),
)
gate.register(
    "file_delete", _file_delete, tier=3, destructive=True,
    mutating=True,
    redact=lambda a: {"path": a["path"]},  # drops expected_sha256/_prior noise
    summary=lambda a: f"I want to delete {a['path']}.",
    inverse=_delete_inverse,
    schema=_schema(
        "PERMANENTLY delete a single file. It does not go to the Recycle Bin. "
        "Small text files can be restored via undo; large or binary ones cannot. "
        "Always requires the user's explicit approval, so only call it when they "
        "actually asked for a deletion.",
        {"path": _PATH}, ["path"],
    ),
)
gate.register(
    "dir_restore_batch", _dir_restore_batch, tier=2,
    summary=lambda a: f"I restored {len(a.get('moves') or [])} files from one batch.",
)
gate.register(
    "dir_organize", _dir_organize,
    tier=lambda a: 3 if _organize_risky(a) else 2, destructive=_organize_risky,
    mutating=True, user_intent=_organize_user_intent,
    task=True, supports_pause=True,
    title=lambda a: f"Organize {len(a.get('moves') or [])} files in {a.get('path', '')}",
    steps_total=lambda a: len(a.get("moves") or []),
    summary=lambda a: f"I want to organize {len(a['moves'])} files in {a['path']}.",
    inverse=_organize_inverse,
    schema=_schema(
        "Move many files in one batch (max 200). You must supply the EXPLICIT list "
        "of moves -- this tool sorts nothing by itself. List the directory first, "
        "decide the destinations yourself, then pass them here. Sources that have "
        "vanished are skipped, not errors.",
        {
            "path": _PATH,
            "moves": {
                "type": "array",
                "description": "Each entry moves one file.",
                "items": {
                    "type": "object",
                    "properties": {
                        "src": {"type": "string", "description": "Existing file path."},
                        "dst": {"type": "string", "description": "Destination path including filename."},
                    },
                    "required": ["src", "dst"],
                },
            },
        },
        ["path", "moves"],
    ),
)
gate.register(
    "run_readonly_cmd", _run_cmd, tier=1,
    summary=lambda a: f"I want to run `{a['cmd']}`.",
    schema=_schema(
        "Run one read-only shell command and return its exit code, stdout and "
        "stderr. ONLY 'git status', 'git log', 'git diff', 'dir' and 'ls' are "
        "permitted; anything else, plus redirection (> | & ) and --output, is "
        "refused. This is not a general shell.",
        {"cmd": {"type": "string", "description": "e.g. 'git status' or 'ls /path'."}},
        ["cmd"],
    ),
)
