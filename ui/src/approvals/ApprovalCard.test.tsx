import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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

test("announces an arriving approval via a live region without ever stealing focus", () => {
  // The user is typing in the composer when an approval lands. The card's own
  // initial-focus target is Deny — a destructive answer — so grabbing focus
  // would arm Deny under the user's next keystroke. It must announce, not grab.
  const composer = document.createElement("input");
  document.body.append(composer);
  composer.focus();

  const view = render(
    <ApprovalOverlay
      conversationId="chat"
      sendApprovalResponse={vi.fn()}
      sendInterrupt={vi.fn()}
    />,
  );
  // The live region is mounted empty first so a later text change is announced.
  const announcer = screen.getByRole("status");
  expect(announcer.textContent).toBe("");

  act(() => useHaloStore.setState({ approvals: { first: approval("first") } }));
  view.rerender(
    <ApprovalOverlay
      conversationId="chat"
      sendApprovalResponse={vi.fn()}
      sendInterrupt={vi.fn()}
    />,
  );

  // Focus never left the composer, and the card is not modal.
  expect(document.activeElement).toBe(composer);
  const dialog = screen.getByRole("alertdialog", { name: "Approval required" });
  expect(dialog.getAttribute("aria-modal")).toBe("false");
  // The arrival is announced with the summary, and the card still describes itself.
  expect(announcer.textContent).toContain("Delete file first?");
  const descriptionId = dialog.getAttribute("aria-describedby");
  expect(descriptionId).not.toBeNull();
  expect(document.getElementById(descriptionId!)?.textContent).toBe("Delete file first?");

  composer.remove();
});

test("invalid edited arguments stay editable and never send or lock the card", () => {
  const sendApprovalResponse = vi.fn();
  useHaloStore.setState({ approvals: { first: approval("first") } });
  render(
    <ApprovalOverlay
      conversationId="chat"
      sendApprovalResponse={sendApprovalResponse}
      sendInterrupt={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const editor = screen.getByRole("textbox", { name: "Edit arguments" });
  expect(screen.getByText(/preserve the original value/i)).toBeTruthy();
  expect(editor.getAttribute("aria-describedby")).toContain("edit-help");
  fireEvent.change(editor, { target: { value: "{not valid json" } });
  fireEvent.click(screen.getByRole("button", { name: "Approve with edits" }));

  expect(sendApprovalResponse).not.toHaveBeenCalled();
  expect(screen.getByRole("alert").textContent).toContain("valid JSON object");
  expect((screen.getByRole("button", { name: "Approve with edits" }) as HTMLButtonElement).disabled).toBe(false);
  expect((editor as HTMLTextAreaElement).value).toBe("{not valid json");
});
