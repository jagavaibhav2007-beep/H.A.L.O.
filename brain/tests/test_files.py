"""Runnable self-check for Phase 2 Step 7 (Lane-1 file tools).

No test framework -- plain asyncio + assert, matching test_gate.py. Run with:
    python brain/tests/test_files.py

Drives gate.gated_execute directly (no server/graph needed) with a fake
broadcast and a temp-dir project root set via the project_roots setting.
Tier-3 paths can't interrupt() outside a graph, so Tier 3 is asserted via
classify() only; execution paths are proven at Tiers 1-2 on real files.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="halo-test-files-")
os.environ["LOCALAPPDATA"] = _TMP  # store's default DB path lands in the temp dir

from brain import gate, store
from brain.tools import files  # registers the real file tools; also exposes _resolve/_file_search

ROOT = Path(tempfile.mkdtemp(prefix="halo-files-root-")).resolve()
OUT = Path(tempfile.mkdtemp(prefix="halo-files-out-")).resolve()

store.connect()
store.set_setting("project_roots", [str(ROOT)])

FRAMES: list[tuple[str, dict]] = []


async def broadcast(msg_type: str, payload: dict) -> None:
    FRAMES.append((msg_type, payload))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


async def _run(tool: str, args: dict) -> str:
    """Execute through the real gate; returns the recorded result status."""
    FRAMES.clear()
    res = await gate.gated_execute(tool, args, conversation_id="files-test", task_id=None, broadcast=broadcast)
    return res["pending_tool_result"]["status"]


def _activity() -> dict:
    acts = [p for t, p in FRAMES if t == "activity"]
    assert acts, f"no activity frame in {FRAMES}"
    return acts[-1]


async def _undo(token: str) -> None:
    FRAMES.clear()
    await gate.handle_undo({"undo_token": token}, broadcast)


def check_classify() -> None:
    inside = str(ROOT / "x.txt")
    outside = str(OUT / "x.txt")
    for tool in ("file_read", "dir_list"):
        assert gate.classify(tool, {"path": inside}) == 1, tool
        assert gate.classify(tool, {"path": outside}) == 3, tool
    assert gate.classify("file_search", {"path": str(ROOT), "pattern": "*.txt"}) == 1
    assert gate.classify("file_search", {"path": str(OUT), "pattern": "*"}) == 3

    existing = ROOT / "exists.txt"
    existing.write_text("here", encoding="utf-8")
    assert gate.classify("file_create", {"path": inside, "content": "x"}) == 2
    assert gate.classify("file_create", {"path": str(existing), "content": "x"}) == 3  # overwrite
    assert gate.is_destructive("file_create", {"path": str(existing), "content": "x"}) is True
    assert gate.classify("file_create", {"path": outside, "content": "x"}) == 3

    assert gate.classify("file_edit", {"path": inside, "old": "a", "new": "b"}) == 2
    assert gate.classify("file_edit", {"path": outside, "old": "a", "new": "b"}) == 3

    assert gate.classify("file_move", {"src": str(existing), "dst": str(ROOT / "new.txt")}) == 2
    assert gate.classify("file_move", {"src": str(existing), "dst": str(existing)}) == 3  # dst exists
    assert gate.classify("file_move", {"src": str(existing), "dst": outside}) == 3
    assert gate.classify("file_move", {"src": outside, "dst": str(ROOT / "new.txt")}) == 3

    assert gate.classify("file_delete", {"path": str(existing)}) == 3  # always
    assert gate.is_destructive("file_delete", {"path": str(existing)}) is True

    good = {"path": str(ROOT), "moves": [{"src": str(existing), "dst": str(ROOT / "o" / "e.txt")}]}
    assert gate.classify("dir_organize", good) == 2
    bad = {"path": str(ROOT), "moves": [{"src": str(existing), "dst": outside}]}
    assert gate.classify("dir_organize", bad) == 3
    collide = {"path": str(ROOT), "moves": [{"src": inside, "dst": str(existing)}]}
    assert gate.classify("dir_organize", collide) == 3  # a dst exists

    assert gate.classify("run_readonly_cmd", {"cmd": "git status"}) == 1
    existing.unlink()
    print("[check 1] classify matrix: in-roots vs outside vs overwrite/exists tiers + destructive flags: OK")


async def check_create_undo() -> None:
    p = ROOT / "made.txt"
    status = await _run("file_create", {"path": str(p), "content": "hello halo"})
    assert status == "ok", status
    assert p.read_text(encoding="utf-8") == "hello halo"
    act = _activity()
    assert act["tier"] == 2 and act["undoable"] is True and act["undo_token"], act
    # content redacts to a length note; the path stays visible.
    red = gate.redact("file_create", {"path": str(p), "content": "hello halo"})
    assert red == {"path": str(p), "content": "<10 chars>"}, red

    await _undo(act["undo_token"])
    assert not p.exists(), "undo didn't delete the created file"
    assert any(t == "activity" for t, _ in FRAMES), "no reversal activity"
    print("[check 2] file_create: writes, redacts content, undo deletes it (trusted hash-guarded inverse): OK")


async def check_edit() -> None:
    p = ROOT / "edit.txt"
    p.write_text("alpha beta gamma", encoding="utf-8")
    before = _sha(p)
    status = await _run("file_edit", {"path": str(p), "old": "beta", "new": "BETA"})
    assert status == "ok", status
    assert p.read_text(encoding="utf-8") == "alpha BETA gamma"
    act = _activity()
    assert act["undoable"] is True, act

    await _undo(act["undo_token"])
    assert p.read_text(encoding="utf-8") == "alpha beta gamma"
    assert _sha(p) == before, "undo didn't restore prior bytes"

    p2 = ROOT / "amb.txt"
    p2.write_text("x x", encoding="utf-8")
    status = await _run("file_edit", {"path": str(p2), "old": "x", "new": "y"})
    assert status.startswith("error") and "ambiguous" in status, status
    assert p2.read_text(encoding="utf-8") == "x x", "ambiguous edit changed the file"
    status = await _run("file_edit", {"path": str(p2), "old": "zzz", "new": "y"})
    assert status.startswith("error") and "not found" in status, status
    print("[check 3] file_edit: exact replace, undo restores bytes (sha), ambiguous/missing old error honestly: OK")


async def check_move() -> None:
    src, dst = ROOT / "m.txt", ROOT / "sub" / "m.txt"
    src.write_text("mv", encoding="utf-8")
    status = await _run("file_move", {"src": str(src), "dst": str(dst)})
    assert status == "ok", status
    assert dst.is_file() and not src.exists()
    act = _activity()
    await _undo(act["undo_token"])
    assert src.is_file() and src.read_text(encoding="utf-8") == "mv" and not dst.exists()

    # Collision at execution time (Tier-2 race, classify already passed):
    # drive the execution tail directly with an occupied dst.
    taken = ROOT / "taken.txt"
    taken.write_text("occupied", encoding="utf-8")
    mover = ROOT / "mover.txt"
    mover.write_text("payload", encoding="utf-8")
    FRAMES.clear()
    await gate._execute_tail("file_move", {"src": str(mover), "dst": str(taken)}, 2, "files-test", broadcast)
    suffixed = ROOT / "taken (2).txt"
    assert suffixed.read_text(encoding="utf-8") == "payload", "collision suffix not applied"
    assert taken.read_text(encoding="utf-8") == "occupied", "collision overwrote the existing file"
    act = _activity()
    await _undo(act["undo_token"])  # inverse recorded the ACTUAL suffixed dst
    assert mover.read_text(encoding="utf-8") == "payload" and not suffixed.exists()
    print("[check 4] file_move: round-trip undo; collision suffixes ' (2)' and the inverse uses the actual dst: OK")


async def check_organize() -> None:
    pile = ROOT / "pile"
    pile.mkdir()
    srcs = []
    for i in range(3):
        f = pile / f"f{i}.txt"
        f.write_text(f"file {i}", encoding="utf-8")
        srcs.append(f)
    moves = [{"src": str(f), "dst": str(ROOT / "sorted" / f.name)} for f in srcs]
    status = await _run("dir_organize", {"path": str(pile), "moves": moves})
    assert status == "ok", status
    for f in srcs:
        assert not f.exists() and (ROOT / "sorted" / f.name).is_file()
    tasks = store.list_tasks()
    assert any(t["state"] == "done" and t["steps_total"] == 3 for t in tasks), tasks
    act = _activity()
    assert act["undoable"] is True, act

    await _undo(act["undo_token"])  # one token reverses the whole batch
    for f in srcs:
        assert f.is_file() and not (ROOT / "sorted" / f.name).exists(), f

    over = [{"src": str(pile / f"z{i}"), "dst": str(ROOT / "zz" / f"z{i}")} for i in range(201)]
    status = await _run("dir_organize", {"path": str(pile), "moves": over})
    assert status.startswith("error") and "200" in status, status
    print("[check 5] dir_organize: 3 files moved + task row, one undo restores all, 201-move batch refused: OK")


async def check_readonly_cmd() -> None:
    status = await _run("run_readonly_cmd", {"cmd": "git status"})
    assert status == "ok", status
    status = await _run("run_readonly_cmd", {"cmd": "del foo"})
    assert status.startswith("error") and "not allowed" in status, status
    for sneaky in ("dir > pwned.txt", "git log --output=pwned.txt"):
        status = await _run("run_readonly_cmd", {"cmd": sneaky})
        assert status.startswith("error") and "not allowed" in status, (sneaky, status)
    print("[check 6] run_readonly_cmd: git status runs; del, redirection, --output refused: OK")


def check_resolve() -> None:
    home = Path.home()
    # Bare/relative paths anchor at home, NOT the Brain's CWD (the bug that sent
    # "Desktop" to a spurious out-of-roots location and emptied the search).
    assert files._resolve("Desktop") == (home / "Desktop").resolve(), files._resolve("Desktop")
    assert files._resolve("a/b.txt") == (home / "a" / "b.txt").resolve()
    if Path.cwd().resolve() != home.resolve():
        assert files._resolve("Desktop") != (Path.cwd() / "Desktop").resolve(), "relative path resolved against CWD"
    # ~ and absolute paths are untouched.
    assert files._resolve("~") == home.resolve()
    assert files._resolve(str(ROOT / "x.txt")) == (ROOT / "x.txt").resolve()
    print("[check 8] _resolve: bare/relative anchors at home not CWD; ~ and absolute paths untouched: OK")


async def check_search() -> None:
    (ROOT / "needle.pdf").write_text("x", encoding="utf-8")
    # The result must reach the model via the tool message content, not just the
    # status -- a found search and an empty one were both "I ran file_search."
    FRAMES.clear()
    res = await gate.gated_execute(
        "file_search", {"path": str(ROOT), "pattern": "*.pdf"},
        conversation_id="files-test", task_id=None, broadcast=broadcast,
    )
    assert res["pending_tool_result"]["status"] == "ok", res
    content = res["messages"][0]["content"]
    assert "needle.pdf" in content, content
    assert content != "I ran file_search.", "search result discarded, not surfaced to the model"

    res = await gate.gated_execute(
        "file_search", {"path": str(ROOT), "pattern": "*.nomatch"},
        conversation_id="files-test", task_id=None, broadcast=broadcast,
    )
    empty = res["messages"][0]["content"]
    assert "[]" in empty and "no matches" in empty, empty
    (ROOT / "needle.pdf").unlink()
    print("[check 9] file_search: finds a known file and its name reaches the tool message; "
          "empty result reads as explicit 'no matches': OK")


def check_traversal() -> None:
    sneaky = str(ROOT / ".." / ".." / "Windows" / "system32" / "x")
    assert gate.classify("file_read", {"path": sneaky}) == 3, "traversal escaped roots at Tier 1"
    assert gate.classify("file_create", {"path": sneaky, "content": "x"}) == 3
    print("[check 7] traversal: ..\\..\\ paths resolve outside roots -> Tier 3: OK")


async def main() -> None:
    check_classify()
    await check_create_undo()
    await check_edit()
    await check_move()
    await check_organize()
    await check_readonly_cmd()
    check_resolve()
    await check_search()
    check_traversal()
    print("[brain.files] self-check OK")


if __name__ == "__main__":
    asyncio.run(main())
