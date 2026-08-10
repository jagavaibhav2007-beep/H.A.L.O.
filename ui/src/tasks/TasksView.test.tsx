import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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

test("stopping keeps progress visible without actionable controls", () => {
  useHaloStore.setState({
    tasks: {
      [task.task_id]: { ...task, state: "stopping", step: 3, steps_total: 9 },
    },
  });

  render(<TasksView sendTaskOp={vi.fn()} sendLanePin={vi.fn()} />);

  expect(screen.getByText("Stopping")).toBeTruthy();
  expect(screen.getByText(/step 3\/9/i)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  expect((screen.getByRole("combobox", { name: "Lane" }) as HTMLSelectElement).disabled).toBe(true);
});

test("stopped is neutral terminal history", () => {
  useHaloStore.setState({
    tasks: {
      [task.task_id]: {
        ...task,
        state: "stopped",
        step: 3,
        steps_total: 9,
        reason: "stopped",
      },
    },
  });

  render(<TasksView sendTaskOp={vi.fn()} sendLanePin={vi.fn()} />);

  expect(screen.getByText("Stopped")).toBeTruthy();
  expect(screen.getByText("Stopped after 3 of 9.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
});

test("Stop sends once, locks immediately, and follows authoritative states", () => {
  const sendTaskOp = vi.fn();
  useHaloStore.setState({ tasks: { [task.task_id]: task } });
  render(<TasksView sendTaskOp={sendTaskOp} sendLanePin={vi.fn()} />);

  const stop = screen.getByRole("button", { name: "Stop" });
  fireEvent.click(stop);
  fireEvent.click(stop);
  expect(sendTaskOp).toHaveBeenCalledTimes(1);
  expect(sendTaskOp).toHaveBeenCalledWith("stop", task.task_id);
  expect((screen.getByRole("button", { name: "Stopping…" }) as HTMLButtonElement).disabled).toBe(true);

  act(() => {
    useHaloStore.setState({
      tasks: { [task.task_id]: { ...task, state: "stopping", step: 2, steps_total: 4 } },
    });
  });
  expect(screen.getByText("Stopping")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull();

  act(() => {
    useHaloStore.setState({
      tasks: { [task.task_id]: { ...task, state: "stopped", step: 2, steps_total: 4 } },
    });
  });
  expect(screen.getByText("Stopped")).toBeTruthy();
  expect(screen.getByText("Stopped after 2 of 4.")).toBeTruthy();
});
