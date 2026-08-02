# Phase 3 UI Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Phase 0–2 workspace resilient, truthful, and accessible at Phase-3-sized layouts without adding any Phase 3 capability UI.

**Architecture:** Keep all behavior in the existing React views and Zustand projections. Add one compact CSS breakpoint, reuse current primitives and task data, and make only local render/style changes. No IPC, reducer, Brain, Tauri, dependency, or design-token changes.

**Tech Stack:** React 19, TypeScript, Zustand, Vitest/Testing Library, existing CSS custom properties.

## Global Constraints

- Preserve every existing app capability and IPC shape.
- Add no dependencies, abstractions, components, stores, or breakpoints beyond `640px`.
- Do not implement coding, browser, takeover, real voice, GUI control, self-improvement, integrations, onboarding, orb deep-links, or sidebar collapse controls.
- Use existing `--hit-min`, typography, color, spacing, and primitive classes.
- Keep the orb unchanged.
- Validate light and dark themes at 1000×700, 720×480, and a 360px CSS viewport.

---

### Task 1: Make terminal task cards truthful

**Files:**
- Create: `ui/src/tasks/TasksView.test.tsx`
- Modify: `ui/src/tasks/TasksView.tsx`
- Modify: `ui/src/tasks/TasksView.css`

- [ ] **Step 1: Add failing terminal-control tests**

Create a test store reset and a `TaskStateMsg` fixture. Assert that a failed task still renders its reason and lane label but has no Stop button and a disabled lane selector; assert a running task retains Pause, Stop, and an enabled lane selector.

```tsx
test("terminal tasks keep history without actionable controls", () => {
  useHaloStore.setState({
    tasks: { failed: { ...task, task_id: "failed", state: "failed", reason: "Worker exited." } },
    capabilities: { ...useHaloStore.getState().capabilities, taskControls: true },
  });
  render(<TasksView sendTaskOp={vi.fn()} sendLanePin={vi.fn()} />);

  expect(screen.getByText("Worker exited.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  expect(screen.getByRole("combobox", { name: "Lane" })).toBeDisabled();
});
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `npm test -- --run src/tasks/TasksView.test.tsx`

Expected: FAIL because failed task cards currently render an enabled lane selector and Stop button.

- [ ] **Step 3: Implement the minimum terminal-state guard**

In `TaskCard`, derive `terminal` from `done`/`failed`, pass it into the existing disabled expression, and render `.task-actions` only for non-terminal states.

```tsx
const terminal = state === "done" || state === "failed";

<LanePin task={task} disabled={busy || !controlsAvailable || terminal} onPin={pin} />
```

Wrap the existing `.task-actions` block in `!terminal && (...)`. Allow the existing action row to wrap; do not change task state, persistence, or operation handling.

- [ ] **Step 4: Run the focused test and full UI test suite**

Run: `npm test -- --run src/tasks/TasksView.test.tsx`

Run: `npm test -- --run`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add ui/src/tasks/TasksView.tsx ui/src/tasks/TasksView.css ui/src/tasks/TasksView.test.tsx
git commit -m "Make terminal task controls truthful"
```

### Task 2: Replace raw Activity task IDs with existing titles

**Files:**
- Create: `ui/src/activity/ActivityFeed.test.tsx`
- Modify: `ui/src/activity/ActivityFeed.tsx`

- [ ] **Step 1: Add a failing task-title test**

Seed one activity and a matching task in the store, render `ActivityFeed`, and assert the task option reads the task title while retaining the task id as its value. Add a second activity with no task record and assert the id remains as the fallback label.

```tsx
expect(screen.getByRole("option", { name: "Prepare release" })).toHaveValue("task-1");
expect(screen.getByRole("option", { name: "orphan-task" })).toHaveValue("orphan-task");
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `npm test -- --run src/activity/ActivityFeed.test.tsx`

Expected: FAIL because the selector currently exposes raw ids for every task.

- [ ] **Step 3: Reuse the existing task projection**

Subscribe with `selectTasks` and render `tasks[id]?.title ?? id` inside the existing option. Do not add mapping state or duplicate task data.

```tsx
const tasks = useHaloStore(selectTasks);
<option key={id} value={id}>{tasks[id]?.title ?? id}</option>
```

- [ ] **Step 4: Run the focused and full UI test suites**

Run: `npm test -- --run src/activity/ActivityFeed.test.tsx`

Run: `npm test -- --run`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add ui/src/activity/ActivityFeed.tsx ui/src/activity/ActivityFeed.test.tsx
git commit -m "Show task titles in activity filters"
```

### Task 3: Add the compact workspace shell and semantic structure

**Files:**
- Modify: `ui/src/workspace/WorkspaceRoot.tsx`
- Modify: `ui/src/workspace/Sidebar.tsx`
- Modify: `ui/src/workspace/WorkspaceRoot.css`
- Modify: `ui/src/memory/MemoryView.tsx`
- Modify: `ui/src/skills/SkillsView.tsx`
- Modify: `ui/src/settings/SettingsView.tsx`

- [ ] **Step 1: Record the existing rendered failures**

At a 360px CSS viewport, verify the sidebar remains 176px and leaves about 181px for content. At 720×480 Settings, verify horizontal overflow. Confirm the document has no `main` landmark and group headings jump from `h1` to `h3`.

Expected: all four checks reproduce the approved audit evidence.

- [ ] **Step 2: Implement semantic markup with no behavior changes**

Change `.workspace-main` from `div` to `main`. Give every sidebar button an explicit `aria-label` and wrap its visible label in `.sidebar-label`. Change Memory, Skills, and Settings group titles from `h3` to `h2` while preserving their classes.

- [ ] **Step 3: Add the single compact shell breakpoint**

Push Settings to the bottom by giving `.sidebar-separator` an automatic top margin. At `max-width: 640px`, reduce the sidebar to 56px, center icons, hide `.sidebar-label`, keep the approval count as a compact numeric badge, and let the status strip/task chip wrap.

```css
.sidebar-separator { margin: auto 16px 8px; }

@media (max-width: 640px) {
  .sidebar { width: 56px; }
  .sidebar-item { justify-content: center; padding-inline: 8px; }
  .sidebar-label { display: none; }
  .sidebar-badge { position: absolute; top: 2px; right: 2px; margin: 0; }
  .sidebar-badge svg { display: none; }
  .status-strip { flex-wrap: wrap; }
  .status-task-chip { flex: 1 1 100%; margin-left: 0; }
}
```

- [ ] **Step 4: Typecheck and render-check the shell**

Run: `npx tsc --noEmit`

Render-check Chat, Tasks, and Settings at 1000×700, 720×480, and 360px. Confirm Settings stays at the rail bottom, the main content remains usable, labels remain available to accessibility APIs, headings are sequential, and the status strip does not clip controls.

Expected: PASS with no horizontal shell overflow and no console errors.

- [ ] **Step 5: Commit**

```powershell
git add ui/src/workspace/WorkspaceRoot.tsx ui/src/workspace/Sidebar.tsx ui/src/workspace/WorkspaceRoot.css ui/src/memory/MemoryView.tsx ui/src/skills/SkillsView.tsx ui/src/settings/SettingsView.tsx
git commit -m "Add compact accessible workspace shell"
```

### Task 4: Harden existing surfaces at compact widths

**Files:**
- Modify: `ui/src/components/primitives.css`
- Modify: `ui/src/chat/ChatView.css`
- Modify: `ui/src/activity/ActivityFeed.css`
- Modify: `ui/src/memory/MemoryView.css`
- Modify: `ui/src/skills/SkillsView.css`
- Modify: `ui/src/settings/SettingsView.css`
- Modify: `ui/src/approvals/ApprovalCard.css`

- [ ] **Step 1: Preserve the approved overflow/target evidence**

At 720×480 Settings, retain the measured `scrollWidth > clientWidth` failure. At 360px, retain screenshots showing clipped Settings rows, Activity filters, task heads/actions, approval actions, and the disconnected Chat queue notice. Retain the target-size inventory for existing text-like controls below `--hit-min`.

- [ ] **Step 2: Fix input sizing once at the existing primitive**

Add `box-sizing: border-box` and `min-width: 0` to `.halo-input`. This fixes the `width:100%` Settings roots field without creating a special wrapper.

- [ ] **Step 3: Apply local wrapping and target-size corrections**

Use existing selectors only:

- Chat: wrap the composer at compact width and move `.chat-queued-badge` to its own full row; raise markdown copy and did-you-mean controls to `--hit-min`.
- Activity: constrain selects/search to the view, give the checkbox label `--hit-min`, and make search a full compact row.
- Memory/Skills: wrap existing action rows and raise history/drawer controls to `--hit-min`.
- Settings: allow rows and the OpenRouter field to wrap/shrink; constrain values; raise the Advanced summary target.
- Approvals: make cards border-box, wrap action rows, and raise text-like secondary controls to `--hit-min`.

Do not change copy, component structure, data flow, or tokens.

- [ ] **Step 4: Run focused automated checks**

Run: `npm test -- --run`

Run: `npx tsc --noEmit`

Run: `npm run build`

Expected: PASS.

- [ ] **Step 5: Perform the rendered UI and accessibility matrix**

Render every workspace view plus an approval card in light and dark themes at 1000×700, 720×480, and 360px. Verify no horizontal overflow, clipped primary controls, or unreadable labels. Verify Ctrl+K returns to Chat and focuses the composer. Check browser console errors/warnings and inspect main/heading/label semantics.

Expected: PASS. The orb remains unchanged and relies on its existing native-size evidence.

- [ ] **Step 6: Run Ponytail review and repository verification**

Review the final diff for removable wrappers, duplicate declarations, unnecessary state, abstractions, dependencies, and deferred Phase 3 feature UI. Remove anything not required by the approved matrix.

Run: `.\verify.ps1 -PythonCommand '.\.venv\Scripts\python.exe'`

Expected: complete repository gate passes.

- [ ] **Step 7: Update durable project memory and commit**

Record only reusable findings: terminal task cards are history-only, and the workspace uses a 640px compact rail rather than a second layout.

```powershell
git add ui/src/components/primitives.css ui/src/chat/ChatView.css ui/src/activity/ActivityFeed.css ui/src/memory/MemoryView.css ui/src/skills/SkillsView.css ui/src/settings/SettingsView.css ui/src/approvals/ApprovalCard.css mem docs/superpowers/plans/2026-08-02-phase-3-ui-foundation.md
git commit -m "Harden Phase 3 UI foundation"
```
