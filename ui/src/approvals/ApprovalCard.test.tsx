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

test("an answered card leaves the screen as soon as the decision is on the wire", () => {
  const sendApprovalResponse = vi.fn(() => true);
  useHaloStore.setState({
    approvals: { "ap-live": approval("ap-live") },
    connection: { ...useHaloStore.getState().connection, wsStatus: "connected" },
  });
  render(
    <ApprovalOverlay
      conversationId="chat"
      sendApprovalResponse={sendApprovalResponse}
      sendInterrupt={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: /^Deny/ }));

  expect(sendApprovalResponse).toHaveBeenCalledWith("ap-live", "deny");
  // Gone immediately — no spinner to sit and watch, and no second click target
  // for an impatient user to hit.
  expect(screen.queryByText("Delete file ap-live?")).toBeNull();
  expect(useHaloStore.getState().approvals["ap-live"]).toBeUndefined();

  // The Brain's confirming frame still arrives afterwards; it must be a no-op,
  // not a crash or a resurrected card.
  act(() => {
    useHaloStore.getState().applyFrame({
      type: "task_state",
      id: "confirm-1",
      ts: "2026-07-22T00:00:01Z",
      task_id: "task-ap-live",
      state: "done",
      lane: 1,
      title: "Delete file",
    });
  });
  expect(useHaloStore.getState().approvals["ap-live"]).toBeUndefined();
});

test("a card answered while the socket is down stays up until the decision can reach the Brain", () => {
  // The decision is only sitting in the reconnect queue here. Dismissing would
  // tell the user they approved something the Brain has never heard.
  const sendApprovalResponse = vi.fn(() => true);
  useHaloStore.setState({
    approvals: { "ap-offline": approval("ap-offline") },
    connection: { ...useHaloStore.getState().connection, wsStatus: "reconnecting" },
  });
  render(
    <ApprovalOverlay
      conversationId="chat"
      sendApprovalResponse={sendApprovalResponse}
      sendInterrupt={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: /^Deny/ }));

  expect(screen.getByText("Delete file ap-offline?")).toBeTruthy();
  expect(useHaloStore.getState().approvals["ap-offline"]).toBeTruthy();
  // ...and it stays locked (rule 3) rather than inviting a second answer.
  expect((screen.getByRole("button", { name: /^Deny/ }) as HTMLButtonElement).disabled).toBe(true);
});

test("a card whose offline decision was dropped on reconnect unlocks instead of hanging", () => {
  // dropStaleControlFrames discards a queued approval_response when the Brain
  // restarts on a fresh port, but the approval is durable and comes back in the
  // snapshot still pending. Without an unlock the card sits on "Denying…" with
  // no frame left that could ever clear it — the stuck-forever class from
  // mem/Bugs.md.
  useHaloStore.setState({
    approvals: { "ap-drop": approval("ap-drop") },
    connection: { ...useHaloStore.getState().connection, wsStatus: "reconnecting" },
  });
  render(
    <ApprovalOverlay
      conversationId="chat"
      sendApprovalResponse={vi.fn(() => true)}
      sendInterrupt={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: /^Deny/ }));
  expect((screen.getByRole("button", { name: /^Deny/ }) as HTMLButtonElement).disabled).toBe(true);

  // Brain restarts; the still-pending approval returns and the socket is live.
  act(() => {
    useHaloStore.setState({
      connection: { ...useHaloStore.getState().connection, wsStatus: "connected" },
    });
  });

  expect(screen.getByText("Delete file ap-drop?")).toBeTruthy();
  expect((screen.getByRole("button", { name: /^Deny/ }) as HTMLButtonElement).disabled).toBe(false);
});
