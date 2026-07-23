import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ApprovalRequestMsg } from "../ipc/contract";
import { useHaloStore } from "../state/store";
import { ApprovalOverlay } from "./ApprovalCard";

function approval(approval_id: string): ApprovalRequestMsg {
  return {
    type: "approval_request",
    id: `frame-${approval_id}`,
    ts: "2026-07-22T00:00:00Z",
    approval_id,
    tool: "file_delete",
    args_redacted: { path: "***" },
    tier: 3,
    task_id: `task-${approval_id}`,
    summary: `Delete file ${approval_id}?`,
  };
}

beforeEach(() => {
  installLocalStorage();
  useHaloStore.setState(useHaloStore.getInitialState(), true);
});
afterEach(cleanup);

function installLocalStorage() {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, String(value)),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    },
  });
}

test("announces and safely focuses the newest approval, then restores focus", () => {
  const opener = document.createElement("button");
  document.body.append(opener);
  opener.focus();

  useHaloStore.setState({ approvals: { first: approval("first") } });
  const view = render(
    <ApprovalOverlay
      conversationId="chat"
      sendApprovalResponse={vi.fn()}
      sendInterrupt={vi.fn()}
    />,
  );

  const dialog = screen.getByRole("alertdialog", { name: "Approval required" });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Deny" }));
  const descriptionId = dialog.getAttribute("aria-describedby");
  expect(descriptionId).not.toBeNull();
  expect(document.getElementById(descriptionId!)?.textContent).toBe("Delete file first?");

  act(() => useHaloStore.setState({ approvals: {} }));
  view.rerender(
    <ApprovalOverlay
      conversationId="chat"
      sendApprovalResponse={vi.fn()}
      sendInterrupt={vi.fn()}
    />,
  );
  expect(document.activeElement).toBe(opener);
  opener.remove();
});
