import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { TaskStateMsg } from "../ipc/contract";
import { useHaloStore } from "../state/store";
import { TasksView } from "./TasksView";

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
  useHaloStore.setState({
    capabilities: {
      voiceInput: false,
      taskControls: true,
      skillControls: false,
      demoScenarios: false,
    },
  });
});

afterEach(cleanup);

test("terminal tasks keep their history without actionable controls", () => {
  useHaloStore.setState({
    tasks: {
      failed: {
        ...task,
        task_id: "failed",
        state: "failed",
        reason: "Worker exited.",
      },
    },
  });

  render(<TasksView sendTaskOp={vi.fn()} sendLanePin={vi.fn()} />);

  expect(screen.getByText("Worker exited.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  expect((screen.getByRole("combobox", { name: "Lane" }) as HTMLSelectElement).disabled).toBe(true);
});

test("running tasks retain their available controls", () => {
  useHaloStore.setState({ tasks: { [task.task_id]: task } });

  render(<TasksView sendTaskOp={vi.fn()} sendLanePin={vi.fn()} />);

  expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  expect((screen.getByRole("combobox", { name: "Lane" }) as HTMLSelectElement).disabled).toBe(false);
});
