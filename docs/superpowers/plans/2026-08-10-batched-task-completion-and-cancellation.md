# Batched Task Completion and Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a folder document request show continuous work feedback, stop promptly, and produce one connected assistant response after the whole request finishes.

**Architecture:** Extend the existing `TaskRuntime` with origin-turn completion groups and full authoritative task snapshots. Prepare folder inputs before submission, isolate PDF extraction in a killable process, and teach the React UI the truthful `stopping`/`stopped` lifecycle with reduced-motion-safe animation. Keep the existing WebSocket envelope and task runtime; only the contract's task-state enum and minor version change.

**Tech Stack:** Python 3.11+, asyncio, multiprocessing, LangGraph, SQLite, authenticated WebSockets, React 19, TypeScript 5.8, Zustand, Vitest, Testing Library, CSS media queries.

## Global Constraints

- One user turn produces exactly one content-bearing assistant response after all of its detached tasks are terminal.
- `doc_digest` accepts explicit `paths` or `path` plus optional `glob`; `glob` defaults to `*`, and recursion requires `**/`.
- A document batch is capped at exactly 64 files and must fail before extraction or LLM spending when over cap.
- Each PDF extraction has a 60-second deadline; an accepted stop must reach durable `stopped` within 2 seconds.
- Per-file extraction failures remain structured batch outcomes and never abort sibling files.
- Every live and reconnect `task_state` is a complete authoritative snapshot.
- `stopping` is non-terminal; `stopped`, `done`, and `failed` are terminal. User cancellation is neutral, not an execution failure.
- Add no third-party dependency or new IPC frame type.
- Preserve `prefers-reduced-motion`; state remains understandable without animation.
- Backend tests remain plain `asyncio` plus `assert`; UI tests remain Vitest plus Testing Library.
- Preserve unrelated changes and never replay torn task side effects after restart.

## File Structure

- Create `brain/brain/extract_worker.py`: own isolated extraction process startup, timeout, termination, kill, and reaping.
- Modify `brain/brain/ipc/contract.py`: contract version 1.5 and Python task-state validation.
- Modify `ui/src/ipc/contract.ts`: mirrored version, TypeScript task-state union, and runtime validation.
- Modify `brain/brain/task_runtime.py`: complete state publishing, `stopping`/`stopped`, origin-turn groups, and aggregate continuation dispatch.
- Modify `brain/brain/gate.py`: tool argument preparation hook and origin-turn propagation into detached tasks.
- Modify `brain/brain/graph.py`: detached-task marker, task-group sealing, internal-message filtering, and suppression of task-start prose.
- Modify `brain/brain/server.py`: aggregate continuation callback and correlated internal completion turns.
- Modify `brain/brain/tools/docs.py`: folder preparation, 64-file cap, isolated PDF extraction, progress, partial failures, and cancellation.
- Modify `brain/brain/mock.py`: mirrored stopping/stopped semantics with complete task snapshots.
- Modify `ui/src/state/reducer.ts`: drop empty completed placeholders and project the new states.
- Modify `ui/src/state/store.ts`: select active detached work through cancellation, not only `running`.
- Modify `ui/src/tasks/TasksView.tsx` and `ui/src/tasks/TasksView.css`: state copy, controls, animation, partial-stop presentation, and reduced-motion fallback.
- Modify `ui/src/workspace/StatusStrip.tsx` and `ui/src/workspace/WorkspaceRoot.css`: persistent animated work/cancellation status and correlated stop behavior.
- Modify focused Python and TypeScript tests named in each task.
- Update `systemdesign/12-task-runtime.md`, `systemdesign/13-document-ingestion.md`, `ui_ux/09-tasks.md`, `VERIFY.md`, and `mem/` after behavior is proven.

---

### Task 1: Truthful Task-State Lifecycle and Complete Snapshots

**Files:**
- Modify: `brain/tests/test_task_runtime.py`
- Modify: `brain/tests/test_mock.py`
- Modify: `ui/src/ipc/contract.selfcheck.ts`
- Modify: `brain/brain/ipc/contract.py:17-21,114-119`
- Modify: `ui/src/ipc/contract.ts:22-26,186-195,393-397`
- Modify: `brain/brain/task_runtime.py:48-110,297-425,427-470`
- Modify: `brain/brain/mock.py:220-245,375-393`
- Modify: `brain/brain/graph.py:772-824`

**Interfaces:**
- Produces: `TaskStateMsg["state"] = "waiting" | "running" | "paused" | "waiting_approval" | "stopping" | "stopped" | "done" | "failed"`.
- Produces: `TaskContext._state(state: str, **fields) -> None` persists and broadcasts a complete snapshot.
- Produces: `TaskRuntime.handle_op(... op="stop")` emits `stopping` before setting cancellation; `_finish_stopped` emits terminal `stopped`.
- Consumes: existing `store.upsert_task`, `store.get_task`, and WebSocket broadcast callback.

- [ ] **Step 1: Write failing lifecycle and full-frame tests**

Add a focused runtime check that proves both the new state sequence and metadata retention:

```python
async def check_stop_lifecycle_uses_complete_snapshots() -> None:
    frames: list[tuple[str, dict]] = []

    async def broadcast(kind: str, payload: dict) -> None:
        frames.append((kind, payload))

    async def waits(_args: dict, ctx) -> dict:
        await ctx.progress(3, 9, "invoice.pdf", checkpoint={"docs": ["a", "b", "c"]})
        await ctx.cancelled.wait()
        await ctx.checkpoint()
        raise AssertionError("checkpoint must raise TaskStopped")

    runtime = TaskRuntime(broadcast, concurrency=1)
    await runtime.submit(
        task_id="stateful-stop", conversation_id="tasks", tool="doc_digest",
        args={}, args_redacted={}, tier=1, lane=1, title="Digest 9 documents",
        steps_total=9, supports_pause=True, fn=waits,
    )
    await wait_until(lambda: store.get_task("stateful-stop")["step"] == 3)
    await runtime.handle_op({"id": "stop", "op": "stop", "task_id": "stateful-stop"}, broadcast)
    await wait_until(lambda: store.get_task("stateful-stop")["state"] == "stopped")
    states = [p for k, p in frames if k == "task_state" and p["task_id"] == "stateful-stop"]
    assert [p["state"] for p in states][-2:] == ["stopping", "stopped"]
    assert all(p.get("title") == "Digest 9 documents" for p in states[-2:])
    assert all(p.get("step") == 3 and p.get("steps_total") == 9 for p in states[-2:])
    await runtime.close()
```

Update the existing stop assertions from `failed` to `stopped`. Extend mock round-trip expectations from a direct `done` to `stopping` followed by `stopped`. Add contract self-check fixtures accepting both new states and rejecting an unknown `cancelling` state.

- [ ] **Step 2: Run the tests and verify the intended failures**

Run:

```powershell
python brain/tests/test_task_runtime.py
python brain/tests/test_mock.py
ui/node_modules/.bin/vite-node.cmd ui/src/ipc/contract.selfcheck.ts
```

Expected: runtime assertions fail because Stop still ends as `failed`; mock emits `done`; contract validation rejects `stopping` and `stopped`.

- [ ] **Step 3: Extend the mirrored contract to version 1.5**

Set both contract constants to `"1.5"` and use the exact state order below in both mirrors:

```python
["waiting", "running", "paused", "waiting_approval", "stopping", "stopped", "done", "failed"]
```

```ts
state: "waiting" | "running" | "paused" | "waiting_approval" | "stopping" | "stopped" | "done" | "failed";
```

- [ ] **Step 4: Publish complete authoritative task snapshots**

In `task_runtime.py`, centralize serialization and reuse it for every live transition:

```python
_TASK_FRAME_FIELDS = ("title", "step", "steps_total", "step_label", "reason")

def _task_frame(row: dict) -> dict:
    payload = {"task_id": row["task_id"], "state": row["state"], "lane": row["lane"]}
    payload.update({key: row[key] for key in _TASK_FRAME_FIELDS if row.get(key) is not None})
    return payload

async def _publish_task(broadcast: Broadcast, task_id: str) -> None:
    row = await asyncio.to_thread(store.get_task, task_id)
    if row is not None:
        await broadcast("task_state", _task_frame(row))
```

Make `TaskContext._state` and `TaskContext.progress` persist first and then call `_publish_task`. Make submit, success, stop, failure, and reconciliation use the same helper. Reuse this serializer from `graph.snapshot` rather than keeping a divergent local `_task_frame`.

- [ ] **Step 5: Implement `stopping` and terminal `stopped`**

Change Stop handling and terminal persistence:

```python
else:
    await ctx._state("stopping")
    ctx.cancelled.set()
```

```python
fields = {"state": "stopped", "reason": "stopped"}
```

Include `stopping` in restart reconciliation's live states. Keep a restart during cancellation truthful by reconciling it to `failed` with the existing restart reason, not replaying it. Update mock Stop to emit a full `stopping` snapshot followed by a full `stopped` snapshot.

- [ ] **Step 6: Run focused lifecycle and contract gates**

Run:

```powershell
python brain/tests/test_task_runtime.py
python brain/tests/test_mock.py
python -m brain.ipc.contract
ui/node_modules/.bin/vite-node.cmd ui/src/ipc/contract.selfcheck.ts
python shared/check_contract_sync.py
```

Expected: every command exits 0; the stop sequence is `stopping -> stopped`; contract sync reports version 1.5 with no drift.

- [ ] **Step 7: Commit Task 1**

```powershell
git add brain/brain/ipc/contract.py brain/brain/task_runtime.py brain/brain/mock.py brain/brain/graph.py brain/tests/test_task_runtime.py brain/tests/test_mock.py ui/src/ipc/contract.ts ui/src/ipc/contract.selfcheck.ts
git commit -m "fix: make task cancellation state truthful"
```

---

### Task 2: Origin-Turn Completion Groups and One Assistant Conclusion

**Files:**
- Modify: `brain/tests/test_task_runtime.py`
- Modify: `brain/tests/test_graph.py`
- Modify: `ui/src/state/reducer.test.ts`
- Modify: `brain/brain/task_runtime.py:143-240,297-385`
- Modify: `brain/brain/gate.py:50-75,257-344,350-390`
- Modify: `brain/brain/graph.py:110-160,190-240,669-710,860-980`
- Modify: `brain/brain/server.py:810-834`
- Modify: `ui/src/state/reducer.ts:179-215,445-480`

**Interfaces:**
- Produces: `TaskRuntime.submit(..., origin_turn_id: str | None = None) -> str`.
- Produces: `TaskRuntime.seal_group(conversation_id: str, origin_turn_id: str) -> None`.
- Produces: a single continuation callback call `(conversation_id, aggregate_text, origin_turn_id)` for each sealed terminal group.
- Consumes: Task 1's complete state publisher and terminal `stopped` state.

- [ ] **Step 1: Write failing aggregate-continuation tests**

Add a test with one slow success and one fast structured failure under the same origin:

```python
async def check_origin_group_continues_once_after_every_task() -> None:
    continuations: list[tuple[str, str, str]] = []

    async def noop_broadcast(_kind: str, _payload: dict) -> None:
        return None

    async def continuation(cid: str, text: str, origin: str) -> None:
        continuations.append((cid, text, origin))

    async def success(args: dict, _ctx) -> dict:
        await asyncio.sleep(args["delay"])
        return {"file": args["file"], "status": "ok"}

    async def failure(_args: dict, _ctx) -> dict:
        raise TaskFailed("unreadable", {"file": "bad.pdf", "status": "failed"})

    runtime = TaskRuntime(noop_broadcast, continuation, concurrency=2)
    await runtime.submit(task_id="good", conversation_id="c", origin_turn_id="turn-1",
                         tool="doc_digest", args={"delay": 0.05, "file": "good.pdf"},
                         args_redacted={}, tier=1, lane=1, title="Good", steps_total=1,
                         supports_pause=True, fn=success)
    await runtime.submit(task_id="bad", conversation_id="c", origin_turn_id="turn-1",
                         tool="doc_digest", args={}, args_redacted={}, tier=1, lane=1,
                         title="Bad", steps_total=1, supports_pause=True, fn=failure)
    await runtime.seal_group("c", "turn-1")
    await asyncio.sleep(0.02)
    assert continuations == []
    await wait_until(lambda: len(continuations) == 1)
    assert continuations[0][2] == "turn-1"
    assert '"status": "done"' in continuations[0][1]
    assert '"status": "failed"' in continuations[0][1]
    await runtime.close()
```

Add a second ordering case where both tasks finish before `seal_group`; sealing must dispatch once. In `reducer.test.ts`, add a case where `done` closes an empty assistant placeholder and assert the placeholder is removed rather than rendered as a blank bubble.

- [ ] **Step 2: Run focused tests and verify failures**

Run:

```powershell
python brain/tests/test_task_runtime.py
python brain/tests/test_graph.py
Set-Location ui
npm test -- --run src/state/reducer.test.ts
Set-Location ..
```

Expected: `origin_turn_id`/`seal_group` are missing and the reducer retains the empty completed placeholder.

- [ ] **Step 3: Implement an at-most-once in-memory task-group barrier**

Add focused group records and a lock:

```python
@dataclass
class _TaskGroup:
    conversation_id: str
    origin_turn_id: str
    task_ids: set[str] = field(default_factory=set)
    outcomes: dict[str, dict] = field(default_factory=dict)
    sealed: bool = False
    dispatched: bool = False
```

Register the task in `submit`. Replace direct `_continue` calls in success, stop, and failure with `_complete_task(job, status, output, reason)`. Under `_groups_lock`, dispatch only when `sealed`, `outcomes.keys() == task_ids`, and `dispatched` is false; set `dispatched=True` before scheduling the continuation. Cap each result with `gate._cap_result`, JSON-encode the outcome list, and cap the aggregate again.

Do not await the continuation callback from `seal_group`: graph sealing runs while the server still owns the conversation lock, and the continuation reacquires that lock. Schedule it with `asyncio.create_task`, retain it in `_continuation_tasks`, remove it in a done callback, log non-cancellation exceptions, and cancel/await the retained tasks during `TaskRuntime.close`. This makes sealing non-blocking and prevents a same-conversation deadlock.

Tasks submitted without `origin_turn_id` keep the current immediate single-task continuation path so direct runtime callers do not silently lose completion.

- [ ] **Step 4: Propagate and seal the origin turn**

Add `origin_turn_id` to `gate.gated_execute` and `_start_task_tail`, pass `state.get("turn_id")` from `_gate_node`, and pass it into runtime submission.

Add a state flag when a task detaches:

```python
return {
    "pending_tool_result": {"tool": tool, "status": "started", "task_id": task_id},
    "detached_task_started": True,
    "messages": [{"role": "assistant", "content": f"Started background task {task_id}."}],
}
```

Declare `detached_task_started: bool` in graph state, initialize it to `False` per turn, and route to `END` after the pending tool queue drains when it is true. This prevents a model-generated "task started" prose response while preserving required assistant/tool-call pairing in checkpoint history.

After `_finish_turn` completes (not while suspended on approval), call:

```python
runtime = task_runtime.current()
if runtime is not None and turn_id:
    await runtime.seal_group(cid, turn_id)
```

Apply the same sealing rule to `resume_turn` after its eventual non-suspended completion.

- [ ] **Step 5: Keep internal outcomes out of restored user transcripts**

Mark the synthetic continuation message stored by `run_turn`:

```python
message = {"role": "user", "content": content, "turn_id": turn_id}
if msg.get("_internal"):
    message["internal"] = True
```

Strip `internal` alongside `turn_id` in `_prompt_messages` before provider calls, and exclude messages with `internal=True` in `push_conversation_history`. In `server.continue_task`, send a distinct completion turn ID such as `task-group-{origin_turn_id}` so final tokens open one normal assistant bubble without reopening the already-closed placeholder.

- [ ] **Step 6: Remove empty completed assistant placeholders in the reducer**

In the `done` case, remove a non-interrupted open turn whose text is empty:

```ts
const turns = !frame.interrupted && open.text.length === 0
  ? conv.turns.filter((turn) => turn !== open)
  : patchOpenTurn(conv.turns, open, patch);
```

Do not remove errored or interrupted turns. The global task status supplies the live acknowledgement until the aggregate continuation produces the only content-bearing response.

- [ ] **Step 7: Run grouping, graph, history, and reducer tests**

Run:

```powershell
python brain/tests/test_task_runtime.py
python brain/tests/test_graph.py
python brain/tests/test_snapshot.py
Set-Location ui
npm test -- --run src/state/reducer.test.ts src/chat/ChatView.test.tsx
Set-Location ..
```

Expected: aggregate continuation count is exactly one, no continuation occurs before sealing/all-terminal, internal messages do not rehydrate as user turns, and no blank bubble remains.

- [ ] **Step 8: Commit Task 2**

```powershell
git add brain/brain/task_runtime.py brain/brain/gate.py brain/brain/graph.py brain/brain/server.py brain/tests/test_task_runtime.py brain/tests/test_graph.py ui/src/state/reducer.ts ui/src/state/reducer.test.ts
git commit -m "fix: aggregate background task conclusions"
```

---

### Task 3: Folder-Aware Digestion and Killable PDF Extraction

**Files:**
- Create: `brain/brain/extract_worker.py`
- Modify: `brain/tests/test_docs.py`
- Modify: `brain/tests/test_gate.py`
- Modify: `brain/brain/gate.py:50-75,350-390`
- Modify: `brain/brain/tools/docs.py:20-190`
- Modify: `shared/phase2_check.py`

**Interfaces:**
- Produces: `async extract_pdf_isolated(path: Path, cancelled: asyncio.Event, timeout: float = 60.0) -> str`.
- Produces: `docs._prepare_args(args: dict) -> dict` returning a normalized `paths` list and an optional string `focus`.
- Produces: optional registry hook `prepare(args) -> dict | Awaitable[dict]`, applied after permission approval but before task submission/title/steps calculation.
- Consumes: Task 2's `origin_turn_id` submission and Task 1's `TaskStopped`/checkpoint behavior.

- [ ] **Step 1: Write failing folder-preparation tests**

Extend `test_docs.py` with deterministic folder cases:

```python
def check_folder_glob_preparation() -> None:
    folder = ROOT / "reports"
    nested = folder / "archive"
    nested.mkdir(parents=True)
    for path in (folder / "b.pdf", folder / "a.pdf", nested / "c.pdf"):
        path.write_bytes(b"fixture")

    direct = docs._prepare_args({"path": str(folder), "glob": "*.pdf", "focus": "totals"})
    assert direct == {"paths": [str(folder / "a.pdf"), str(folder / "b.pdf")], "focus": "totals"}
    recursive = docs._prepare_args({"path": str(folder), "glob": "**/*.pdf"})
    assert recursive["paths"] == sorted({str(folder / "a.pdf"), str(folder / "b.pdf"), str(nested / "c.pdf")})
```

Add cases rejecting both `paths` and `path`, an absolute glob, `..` glob traversal, an empty match, and 65 files. Assert all errors name the invalid input or `max 64` and occur before `EXTRACTS` or `LLM_CALLS` changes.

- [ ] **Step 2: Write a failing stalled-extractor cancellation test**

Use a child-inherited offline seam:

```python
async def check_stalled_pdf_worker_is_reaped_on_stop() -> None:
    pdf = ROOT / "slow.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf.open("wb") as handle:
        writer.write(handle)
    os.environ["HALO_EXTRACT_STUB_DELAY"] = "30"
    ctx = _FakeCtx()
    running = asyncio.create_task(extract_worker.extract_pdf_isolated(pdf, ctx.cancelled))
    await asyncio.sleep(0.15)
    started = time.monotonic()
    ctx.cancelled.set()
    try:
        await running
        raise AssertionError("stopped extraction returned text")
    except TaskStopped:
        pass
    finally:
        os.environ.pop("HALO_EXTRACT_STUB_DELAY", None)
    assert time.monotonic() - started < 2.0
    assert not any(child.name.startswith("halo-extract-") for child in multiprocessing.active_children())
```

Add a 60-second timeout unit case by passing `timeout=0.1` with the same seam and assert the error names the file and deadline.

- [ ] **Step 3: Run document tests and verify failures**

Run:

```powershell
python brain/tests/test_docs.py
python brain/tests/test_gate.py
```

Expected: `_prepare_args`, `extract_worker`, and the registry preparation hook do not exist.

- [ ] **Step 4: Implement the isolated extraction worker**

Create `extract_worker.py` with a top-level spawn-safe child target and an async parent loop:

```python
def _extract_child(path: str, send) -> None:
    try:
        delay = float(os.environ.get("HALO_EXTRACT_STUB_DELAY", "0"))
        if delay > 0:
            time.sleep(delay)
        send.send(("ok", extract.extract_text(Path(path))))
    except BaseException as exc:
        send.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        send.close()

async def extract_pdf_isolated(path: Path, cancelled: asyncio.Event, timeout: float = 60.0) -> str:
    # spawn named halo-extract-<stem>, poll the pipe/cancel/deadline, and always reap
```

Use `multiprocessing.get_context("spawn")`, a one-way pipe, and a named non-daemon process. Poll at no more than 50 ms. On cancellation or timeout: `terminate()`, join for 250 ms in `asyncio.to_thread`, call `kill()` if still alive, then join again. Close both pipe ends in `finally`. Map cancellation to `TaskStopped`; map timeout and child error to a named `ValueError` that `doc_digest` records as a per-file failure.

- [ ] **Step 5: Prepare folder arguments before task submission**

Add `prepare=None` to `gate.register` and store it in the registry. In `_start_task_tail`, call it (awaiting when necessary) before redaction, title, steps, persistence, and runtime submission.

Implement `_prepare_args` with the exact rules:

```python
_PATHS_CAP = 64

def _prepare_args(args: dict) -> dict:
    explicit = args.get("paths")
    folder = args.get("path")
    if (explicit is None) == (folder is None):
        raise ValueError("provide exactly one of paths or path")
    # validate glob, resolve/confine, file-filter, de-duplicate, sort, cap
```

Reject absolute glob patterns and any glob path component equal to `..`. Preserve `focus`. Update tier classification to handle either shape without granting outside-root access. Register `prepare=_prepare_args`, compute title/steps from prepared paths, and update the JSON schema so the model sees both input forms.

- [ ] **Step 6: Use isolated PDF extraction and complete progress semantics**

Inside `one`, use the isolated worker only when `ctx is not None and p.suffix.lower() == ".pdf"`; retain direct deterministic extraction for non-task tests and non-PDF formats. Never swallow `TaskStopped` in the per-file broad error handler.

Emit progress for each file and the reduce stage:

```python
await ctx.progress(index, len(paths) + 1, f"Digested {Path(raw).name}", checkpoint={"docs": digests})
...
await ctx.progress(len(paths) + 1, len(paths) + 1, "Synthesizing final response", checkpoint={"docs": digests})
```

Make unreadable files structured degraded outcomes with `status="failed"`, keep siblings running, and include failures in the reduce input. On cancellation, let `TaskStopped` propagate so runtime uses the last checkpoint and never starts reduce.

- [ ] **Step 7: Add authenticated batch coverage**

Extend `shared/phase2_check.py` to submit one folder/glob `doc_digest`, observe more than one progress step under one task ID, wait for one terminal state, and count exactly one final assistant completion for the originating request. Include a broken PDF fixture and assert the final response names the omission without aborting the good fixture.

- [ ] **Step 8: Run focused backend and Phase-2 gates**

Run:

```powershell
python brain/tests/test_docs.py
python brain/tests/test_gate.py
python brain/tests/test_task_runtime.py
python shared/phase2_check.py
```

Expected: folder expansion is deterministic/capped, stalled workers are gone within 2 seconds, one batch survives a bad file, and one origin turn gets one conclusion.

- [ ] **Step 9: Commit Task 3**

```powershell
git add brain/brain/extract_worker.py brain/brain/tools/docs.py brain/brain/gate.py brain/tests/test_docs.py brain/tests/test_gate.py shared/phase2_check.py
git commit -m "feat: make document batches cancellable"
```

---

### Task 4: Persistent Work Animation and Honest Stop UX

**Files:**
- Modify: `ui/src/tasks/TasksView.test.tsx`
- Create: `ui/src/workspace/StatusStrip.test.tsx`
- Modify: `ui/src/state/store.ts:157-190`
- Modify: `ui/src/tasks/TasksView.tsx:20-230`
- Modify: `ui/src/tasks/TasksView.css`
- Modify: `ui/src/workspace/StatusStrip.tsx`
- Modify: `ui/src/workspace/WorkspaceRoot.css:135-205`

**Interfaces:**
- Produces: `selectActiveTask` prioritizes `stopping`, then `running`, then `waiting`, so queued work never hides active cancellation or execution.
- Produces: terminal predicate accepts `stopped`, `done`, or `failed` for stop confirmation.
- Consumes: Task 1's contract union and complete frames; Task 3's progress labels.

- [ ] **Step 1: Write failing task-card state tests**

Add component tests for the two new states:

```tsx
test("stopping keeps progress visible and disables every task control", () => {
  useHaloStore.setState({ tasks: { one: { ...task, state: "stopping", step: 3, steps_total: 9 } } });
  render(<TasksView sendTaskOp={vi.fn()} sendLanePin={vi.fn()} />);
  expect(screen.getByText("Stopping")).toBeTruthy();
  expect(screen.getByText(/3\/9/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  expect((screen.getByRole("combobox", { name: "Lane" }) as HTMLSelectElement).disabled).toBe(true);
});

test("stopped is neutral terminal history", () => {
  useHaloStore.setState({ tasks: { one: { ...task, state: "stopped", step: 3, steps_total: 9, reason: "stopped" } } });
  render(<TasksView sendTaskOp={vi.fn()} sendLanePin={vi.fn()} />);
  expect(screen.getByText("Stopped")).toBeTruthy();
  expect(screen.getByText("Stopped after 3 of 9.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
});
```

Add a click test proving Stop sends exactly once, shows `Stopping…`, remains pending through an authoritative `stopping` frame, and resolves on `stopped` or a correlated `task_op` error.

- [ ] **Step 2: Write failing status-strip tests**

Create `StatusStrip.test.tsx` and assert a running task and a stopping task remain visible:

```tsx
const task: TaskStateMsg = {
  type: "task_state", id: "frame", ts: "2026-08-10T00:00:00Z",
  task_id: "one", state: "running", lane: 1,
  title: "Digest 9 documents", step: 3, steps_total: 9,
};

test("detached work stays visible through cancellation", () => {
  useHaloStore.setState({ tasks: { one: { ...task, state: "stopping", step: 3, steps_total: 9 } } });
  render(<StatusStrip sendTaskOp={vi.fn()} />);
  expect(screen.getByRole("status", { name: "Task progress" }).textContent).toMatch(/Stopping.*3\/9/);
});
```

Assert the working icon/progress element carries a stable class or data attribute for CSS animation and that textual state remains present independently of animation.

- [ ] **Step 3: Run UI tests and verify failures**

Run:

```powershell
Set-Location ui
npm test -- --run src/tasks/TasksView.test.tsx src/workspace/StatusStrip.test.tsx
Set-Location ..
```

Expected: new state map entries/selectors/copy do not exist and `selectRunningTask` drops the task as soon as it becomes `stopping`.

- [ ] **Step 4: Implement state semantics and controls**

Replace `selectRunningTask` with `selectActiveTask`:

```ts
const ACTIVE_TASK_PRIORITY: TaskStateMsg["state"][] = ["stopping", "running", "waiting"];
export const selectActiveTask = (s: HaloStore) => {
  const tasks = Object.values(s.tasks);
  return ACTIVE_TASK_PRIORITY
    .map((state) => tasks.find((task) => task.state === state))
    .find((task) => task !== undefined);
};
```

Add `stopping` rank/chip with `LoaderCircle`, and `stopped` rank/chip with `Square`. Treat both `stopping` controls and all terminal states as non-actionable. Stop confirms only on `value == null || ["stopped", "done", "failed"].includes(value.state)`. Render `Stopped after ${step} of ${steps_total}.` when progress exists and `Stopped before completion.` otherwise.

- [ ] **Step 5: Add meaningful animation with reduced-motion fallback**

Use a rotating glyph and a moving sheen only for active work:

```css
@keyframes task-working-spin { to { transform: rotate(360deg); } }
@keyframes task-progress-sheen { to { background-position: -200% 0; } }

.task-card[data-state="running"] .task-state-chip svg,
.status-task-chip[data-state="running"] .status-task-spinner {
  animation: task-working-spin 1.1s linear infinite;
}

.task-card[data-state="running"] .task-progress-bar > span {
  background: linear-gradient(90deg, var(--primary), color-mix(in srgb, var(--primary) 45%, var(--on-primary)), var(--primary));
  background-size: 200% 100%;
  animation: task-progress-sheen 1.4s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .task-card[data-state="running"] .task-state-chip svg,
  .status-task-chip[data-state="running"] .status-task-spinner,
  .task-card[data-state="running"] .task-progress-bar > span {
    animation: none;
  }
}
```

Use the same motion token timing where possible. Keep determinate width and text unchanged in reduced motion.

- [ ] **Step 6: Make status accessible without noisy announcements**

Give the compact task status one polite atomic region:

```tsx
<div
  className="status-task-chip"
  data-state={activeTask.state}
  role="status"
  aria-label="Task progress"
  aria-live="polite"
  aria-atomic="true"
>
```

Announce state/step changes, not `task_log` chunks. Keep the visible Stop button outside nested live text where possible so its label change does not create duplicate announcements.

- [ ] **Step 7: Run UI unit, accessibility, type, and build checks**

Run:

```powershell
Set-Location ui
npm test -- --run src/tasks/TasksView.test.tsx src/workspace/StatusStrip.test.tsx src/state/reducer.test.ts src/chat/ChatView.a11y.test.tsx src/chat/ChatView.test.tsx
npx tsc --noEmit
npm run build
Set-Location ..
```

Expected: all tests pass, TypeScript is clean, and Vite production build exits 0 with no new warning.

- [ ] **Step 8: Commit Task 4**

```powershell
git add ui/src/state/store.ts ui/src/tasks/TasksView.tsx ui/src/tasks/TasksView.css ui/src/tasks/TasksView.test.tsx ui/src/workspace/StatusStrip.tsx ui/src/workspace/StatusStrip.test.tsx ui/src/workspace/WorkspaceRoot.css
git commit -m "feat: show cancellable task progress clearly"
```

---

### Task 5: Cross-Process QA, Documentation, and Durable Memory

**Files:**
- Modify: `systemdesign/12-task-runtime.md`
- Modify: `systemdesign/13-document-ingestion.md`
- Modify: `ui_ux/09-tasks.md`
- Modify: `VERIFY.md`
- Modify: `mem/Bugs.md`
- Modify: `mem/Decisions.md`
- Modify: `mem/Memory.md`

**Interfaces:**
- Consumes: completed Task 1-4 behavior and fresh verification output.
- Produces: aligned architecture/UX docs and a durable record of the root causes, decisions, and verified fixes.

- [ ] **Step 1: Run the complete focused backend suite**

Run:

```powershell
python brain/tests/test_extract.py
python brain/tests/test_docs.py
python brain/tests/test_gate.py
python brain/tests/test_graph.py
python brain/tests/test_task_runtime.py
python brain/tests/test_server.py
python shared/check_contract_sync.py
python shared/phase2_check.py
```

Expected: all scripts exit 0; output explicitly confirms one grouped continuation and stop under two seconds.

- [ ] **Step 2: Run the complete UI suite and self-checks**

Run:

```powershell
ui/node_modules/.bin/vite-node.cmd ui/src/ipc/contract.selfcheck.ts
ui/node_modules/.bin/vite-node.cmd ui/src/ipc/queue.selfcheck.ts
ui/node_modules/.bin/vite-node.cmd ui/src/state/reducer.selfcheck.ts
ui/node_modules/.bin/vite-node.cmd ui/src/state/conversations.selfcheck.ts
Set-Location ui
npm test -- --run
npx tsc --noEmit
npm run build
Set-Location ..
```

Expected: every self-check, Vitest test, typecheck, and build exits 0.

- [ ] **Step 3: Run rendered browser QA**

Start the functional browser workspace:

```powershell
./dev.ps1 -Browser
```

Using the Browser skill, verify the exact flow:

```text
folder request -> animated running status -> Tasks progress -> Stop -> Stopping -> Stopped card -> one assistant conclusion
```

Check page identity, non-blank DOM, no framework overlay, console errors/warnings, desktop viewport, one viewport at or below 640 px, focus retention after Stop, no clipped controls, no blank assistant bubble, and computed `animation-name: none` under emulated reduced motion. Save screenshots outside the repository.

- [ ] **Step 4: Run native Windows cancellation verification**

Run the real Brain document cancellation test without browser mocks and record the spawned extraction PID. Assert that PID no longer exists after the terminal `stopped` frame and that wall-clock stop latency is below 2 seconds. If Tauri is already holding Rust artifacts, do not misclassify that as a source failure; stop only the tracked dev session before retrying.

- [ ] **Step 5: Run the repository verification gate**

```powershell
./dev.ps1 -Verify
```

Expected: contract sync, all Python/Voice scripts, UI checks/build, Rust tests, and phase checks pass. If a native-only check cannot run, preserve the focused green evidence and name the exact unavailable check in `VERIFY.md` and the final report.

- [ ] **Step 6: Update source-of-truth documentation and memory**

Document these exact decisions:

```text
- origin-turn groups create one content-bearing conclusion;
- folder/glob batches default to direct children and cap at 64;
- PDF extraction is isolated, deadline-bounded, killable, and reaped;
- stopping/stopped are distinct from failed;
- task_state broadcasts are complete snapshots;
- running animation degrades to static text/progress under reduced motion.
```

Add a `mem/Bugs.md` entry for the per-task continuation flood and unkillable extraction root causes. Add the selected group barrier and neutral stopped state to `mem/Decisions.md`. Summarize the implemented/verified outcome in `mem/Memory.md`. Update `VERIFY.md` only with checks actually run.

- [ ] **Step 7: Inspect the final diff for scope, secrets, and stale claims**

Run:

```powershell
git status --short
git diff --stat
git diff --check
rg -n -i "api[_ -]?key|bearer |password|C:/Users" brain ui systemdesign ui_ux mem VERIFY.md docs/superpowers
```

Expected: only planned files changed, `git diff --check` is silent, and matches contain documentation placeholders/general words rather than a real credential or personal path.

- [ ] **Step 8: Commit Task 5**

```powershell
git add systemdesign/12-task-runtime.md systemdesign/13-document-ingestion.md ui_ux/09-tasks.md VERIFY.md mem/Bugs.md mem/Decisions.md mem/Memory.md
git commit -m "docs: record grouped cancellable task behavior"
```

- [ ] **Step 9: Final verification after documentation commit**

Run:

```powershell
git status --short --branch
git log -5 --oneline --decorate
```

Expected: clean worktree, the task commits are present in order, and no push or PR is performed unless explicitly requested.
