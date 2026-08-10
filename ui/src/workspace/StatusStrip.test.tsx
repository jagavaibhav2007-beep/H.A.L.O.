import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { TaskStateMsg } from "../ipc/contract";
import { useHaloStore } from "../state/store";
import { StatusStrip } from "./StatusStrip";

const task: TaskStateMsg = {
  type: "task_state",
  id: "frame",
  ts: "2026-08-10T00:00:00Z",
  task_id: "one",
  state: "running",
  lane: 1,
  title: "Digest 9 documents",
  step: 3,
  steps_total: 9,
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

test("detached work stays visible through cancellation", () => {
  useHaloStore.setState({ tasks: { one: task } });
  render(<StatusStrip sendTaskOp={vi.fn()} />);

  const running = screen.getByRole("status", { name: "Task progress" });
  expect(running.textContent).toMatch(/Running.*Digest 9 documents.*step 3\/9/i);
  expect(running.querySelector(".status-task-spinner")).toBeTruthy();

  act(() => {
    useHaloStore.setState({ tasks: { one: { ...task, state: "stopping" } } });
  });
  const stopping = screen.getByRole("status", { name: "Task progress" });
  expect(stopping.textContent).toMatch(/Stopping.*Digest 9 documents.*step 3\/9/i);
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  const stoppingButton = screen.getByRole("button", { name: "Stopping…" }) as HTMLButtonElement;
  expect(stoppingButton.disabled).toBe(false);
  expect(stoppingButton.getAttribute("aria-disabled")).toBe("true");
});

test("active task priority keeps cancellation ahead of running and queued work", () => {
  useHaloStore.setState({
    tasks: {
      queued: { ...task, task_id: "queued", state: "waiting", title: "Queued" },
      running: { ...task, task_id: "running", title: "Running" },
      stopping: { ...task, task_id: "stopping", state: "stopping", title: "Cancelling now" },
    },
  });
  render(<StatusStrip sendTaskOp={vi.fn()} />);

  expect(screen.getByRole("status", { name: "Task progress" }).textContent).toContain("Cancelling now");
});
