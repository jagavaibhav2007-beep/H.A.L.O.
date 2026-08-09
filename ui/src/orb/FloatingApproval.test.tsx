import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ApprovalRequestMsg } from "../ipc/contract";
import { useHaloStore } from "../state/store";
import { FloatingApproval } from "./FloatingApproval";

beforeEach(() => useHaloStore.setState(useHaloStore.getInitialState(), true));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const approval: ApprovalRequestMsg = {
  type: "approval_request",
  id: "frame-approval-1",
  ts: "2026-08-09T00:00:00Z",
  approval_id: "approval-1",
  tool: "file_delete",
  args_redacted: { path: "***" },
  tier: 3,
  task_id: "task-1",
  summary: "Delete the generated draft?",
};

test("renders the compact pending approval", () => {
  render(
    <FloatingApproval
      approval={approval}
      count={2}
      connected
      sendApprovalResponse={vi.fn(() => true)}
      onReview={vi.fn()}
    />,
  );

  expect(screen.getByText("Delete the generated draft?")).toBeTruthy();
  expect(screen.getByText("file_delete")).toBeTruthy();
  expect(screen.getByText("2 approvals waiting")).toBeTruthy();
});

test("approving a live request sends it and clears only that request locally", () => {
  const send = vi.fn(() => true);
  const secondApproval: ApprovalRequestMsg = {
    ...approval,
    id: "frame-approval-2",
    approval_id: "approval-2",
    task_id: "task-2",
  };
  useHaloStore.setState({ approvals: { "approval-1": approval, "approval-2": secondApproval } });

  render(
    <FloatingApproval
      approval={approval}
      count={2}
      connected
      sendApprovalResponse={send}
      onReview={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(send).toHaveBeenCalledWith("approval-1", "approve");
  expect(useHaloStore.getState().approvals["approval-1"]).toBeUndefined();
  expect(useHaloStore.getState().approvals["approval-2"]).toBeDefined();
});

test("does not dismiss a request when the approval response is not sent", () => {
  const send = vi.fn(() => false);
  useHaloStore.setState({ approvals: { "approval-1": approval } });

  render(
    <FloatingApproval
      approval={approval}
      count={1}
      connected
      sendApprovalResponse={send}
      onReview={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(send).toHaveBeenCalledWith("approval-1", "approve");
  expect(useHaloStore.getState().approvals["approval-1"]).toBeDefined();
});

test("disables every action while disconnected without sending a response", () => {
  const send = vi.fn(() => true);
  const review = vi.fn();
  useHaloStore.setState({ approvals: { "approval-1": approval } });

  render(
    <FloatingApproval
      approval={approval}
      count={1}
      connected={false}
      sendApprovalResponse={send}
      onReview={review}
    />,
  );

  for (const name of ["Approve", "Deny", "Review"]) {
    const action = screen.getByRole("button", { name });
    expect((action as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(action);
  }
  expect(send).not.toHaveBeenCalled();
  expect(review).not.toHaveBeenCalled();
});

test("sends a denial through the same guarded action", () => {
  const send = vi.fn(() => true);
  useHaloStore.setState({ approvals: { "approval-1": approval } });

  render(
    <FloatingApproval
      approval={approval}
      count={1}
      connected
      sendApprovalResponse={send}
      onReview={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Deny" }));
  expect(send).toHaveBeenCalledWith("approval-1", "deny");
  expect(useHaloStore.getState().approvals["approval-1"]).toBeUndefined();
});

test("opens the full approval review without deciding", () => {
  const send = vi.fn(() => true);
  const review = vi.fn();

  render(
    <FloatingApproval
      approval={approval}
      count={1}
      connected
      sendApprovalResponse={send}
      onReview={review}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Review" }));
  expect(review).toHaveBeenCalledOnce();
  expect(send).not.toHaveBeenCalled();
});

test("requires a 700 ms hold and retries a rejected destructive send", () => {
  const callbacks = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => {
    frameId += 1;
    callbacks.set(frameId, callback);
    return frameId;
  }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn((id: number) => callbacks.delete(id)));
  vi.spyOn(performance, "now").mockReturnValue(0);
  const setPointerCapture = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "setPointerCapture");
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", { configurable: true, value: vi.fn() });

  const runFrame = (now: number) => {
    const next = callbacks.entries().next().value as [number, FrameRequestCallback] | undefined;
    expect(next).toBeDefined();
    callbacks.delete(next![0]);
    act(() => next![1](now));
  };
  const send = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  const destructiveApproval = { ...approval, destructive: true };
  useHaloStore.setState({ approvals: { "approval-1": destructiveApproval } });

  try {
    render(
      <FloatingApproval
        approval={destructiveApproval}
        count={1}
        connected
        sendApprovalResponse={send}
        onReview={vi.fn()}
      />,
    );

    const hold = screen.getByRole("button", { name: "Hold to approve" });
    fireEvent.pointerDown(hold, { button: 0, isPrimary: true, pointerId: 1 });
    runFrame(0);
    runFrame(699);
    fireEvent.pointerUp(hold, { pointerId: 1 });
    expect(send).not.toHaveBeenCalled();

    fireEvent.pointerDown(hold, { button: 0, isPrimary: true, pointerId: 1 });
    runFrame(0);
    runFrame(700);
    expect(send).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledWith("approval-1", "approve");
    expect(useHaloStore.getState().approvals["approval-1"]).toBeDefined();

    fireEvent.pointerDown(hold, { button: 0, isPrimary: true, pointerId: 1 });
    runFrame(0);
    runFrame(700);
    expect(send).toHaveBeenCalledTimes(2);
    expect(useHaloStore.getState().approvals["approval-1"]).toBeUndefined();
  } finally {
    if (setPointerCapture) Object.defineProperty(HTMLElement.prototype, "setPointerCapture", setPointerCapture);
    else delete (HTMLElement.prototype as { setPointerCapture?: unknown }).setPointerCapture;
  }
});
