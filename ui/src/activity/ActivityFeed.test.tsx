import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ActivityMsg, TaskStateMsg } from "../ipc/contract";
import { useHaloStore } from "../state/store";
import { ActivityFeed } from "./ActivityFeed";

const activity: ActivityMsg = {
  type: "activity",
  id: "activity-1",
  ts: "2026-08-02T00:00:00Z",
  text: "Started preparing the release.",
  narrate: false,
  task_id: "task-1",
  undoable: false,
};

const task: TaskStateMsg = {
  type: "task_state",
  id: "task-frame",
  ts: "2026-08-02T00:00:00Z",
  task_id: "task-1",
  state: "running",
  lane: 1,
  title: "Prepare release",
};

beforeEach(() => {
  useHaloStore.setState(useHaloStore.getInitialState(), true);
});

afterEach(cleanup);

test("the task filter labels known tasks by title", () => {
  useHaloStore.setState({
    activities: [activity],
    tasks: { [task.task_id]: task },
  });

  render(<ActivityFeed sendUndo={vi.fn()} />);

  const option = screen.getByRole("option", { name: "Prepare release" }) as HTMLOptionElement;
  expect(option.value).toBe("task-1");
});

test("the task filter falls back to an activity task id without a task record", () => {
  useHaloStore.setState({
    activities: [{ ...activity, id: "activity-2", task_id: "orphan-task" }],
  });

  render(<ActivityFeed sendUndo={vi.fn()} />);

  const option = screen.getByRole("option", { name: "orphan-task" }) as HTMLOptionElement;
  expect(option.value).toBe("orphan-task");
});
