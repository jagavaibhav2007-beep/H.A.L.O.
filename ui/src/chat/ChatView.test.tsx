import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useHaloStore } from "../state/store";
import { ChatView } from "./ChatView";

beforeEach(() => {
  useHaloStore.setState(useHaloStore.getInitialState(), true);
  useHaloStore.setState({
    chats: {
      all: [
        { id: "a", title: "A", lastUsedAt: 2, hasUserMessage: true },
        { id: "b", title: "B", lastUsedAt: 1, hasUserMessage: true },
      ],
      open: ["a", "b"],
      activeId: "a",
    },
  });
});
afterEach(cleanup);

test("drafts and failed-message restoration stay scoped to their conversation", () => {
  const props = {
    connState: "connected" as const,
    sendUserMsg: vi.fn(),
    sendInterrupt: vi.fn(),
    sendMic: vi.fn(),
    inputId: "composer",
  };
  const view = render(<ChatView {...props} conversationId="a" />);
  const composer = () => screen.getByRole("textbox");

  fireEvent.change(composer(), { target: { value: "draft A" } });
  view.rerender(<ChatView {...props} conversationId="b" />);
  expect((composer() as HTMLTextAreaElement).value).toBe("");
  fireEvent.change(composer(), { target: { value: "draft B" } });
  view.rerender(<ChatView {...props} conversationId="a" />);
  expect((composer() as HTMLTextAreaElement).value).toBe("draft A");

  fireEvent.change(composer(), { target: { value: "sent from A" } });
  fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });
  view.rerender(<ChatView {...props} conversationId="b" />);
  fireEvent.change(composer(), { target: { value: "sent from B" } });
  fireEvent.keyDown(composer(), { key: "Enter", shiftKey: false });

  view.rerender(<ChatView {...props} conversationId="a" />);
  useHaloStore.getState().applyFrame({
    type: "error",
    id: "error-b",
    ts: "2026-07-22T00:00:00Z",
    code: "turn_failed",
    message: "failed",
    recoverable: true,
    conversation_id: "b",
  });
  view.rerender(<ChatView {...props} conversationId="b" />);

  expect((composer() as HTMLTextAreaElement).value).toBe("sent from B");
  expect((composer() as HTMLTextAreaElement).value).not.toBe("sent from A");
});

test("sending immediately creates a pending Halo turn before the first token", () => {
  const sendUserMsg = vi.fn();
  render(
    <ChatView
      conversationId="a"
      connState="connected"
      sendUserMsg={sendUserMsg}
      sendInterrupt={vi.fn()}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );

  const composer = screen.getByRole("textbox", { name: "Message Halo" });
  fireEvent.change(composer, { target: { value: "Explain this" } });
  fireEvent.keyDown(composer, { key: "Enter", shiftKey: false });

  expect(sendUserMsg).toHaveBeenCalledWith("a", "Explain this");
  expect(useHaloStore.getState().conversations.a.turns).toMatchObject([
    { role: "user", text: "Explain this" },
    { role: "assistant", status: "streaming", text: "" },
  ]);
  expect(screen.getByRole("status").textContent).toContain("I'm thinking.");
});

test("an incompatible contract preserves the draft and cannot queue a send", () => {
  const sendUserMsg = vi.fn();
  render(
    <ChatView
      conversationId="a"
      connState="incompatible"
      sendUserMsg={sendUserMsg}
      sendInterrupt={vi.fn()}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );

  const composer = screen.getByRole("textbox", { name: "Message Halo" }) as HTMLTextAreaElement;
  fireEvent.change(composer, { target: { value: "do not lose this" } });
  fireEvent.keyDown(composer, { key: "Enter", shiftKey: false });

  expect(sendUserMsg).not.toHaveBeenCalled();
  expect(composer.value).toBe("do not lose this");
  expect(composer.disabled).toBe(true);
});

test("the active Halo turn exposes the existing conversation interrupt", () => {
  const sendInterrupt = vi.fn(() => "interrupt-1");
  useHaloStore.setState({
    conversations: {
      a: {
        conversationId: "a",
        needsInputRestore: false,
        turns: [{ id: "pending", role: "assistant", status: "streaming", text: "" }],
      },
    },
  });
  render(
    <ChatView
      conversationId="a"
      connState="connected"
      sendUserMsg={vi.fn()}
      sendInterrupt={sendInterrupt}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Stop response" }));
  expect(sendInterrupt).toHaveBeenCalledWith("a");
  expect((screen.getByRole("button", { name: "Stop response" }) as HTMLButtonElement).disabled).toBe(true);

  act(() => {
    useHaloStore.getState().applyFrame({
      type: "error",
      id: "error-interrupt",
      ts: "2026-07-22T00:00:00Z",
      code: "interrupt_failed",
      message: "Could not stop the response.",
      recoverable: true,
      operation_kind: "interrupt",
      operation_id: "interrupt-1",
    });
  });

  expect(screen.getByText("Could not stop the response.")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Stop response" }) as HTMLButtonElement).disabled).toBe(false);
});

test("a failed answer offers a one-click retry using the original user message", () => {
  const sendUserMsg = vi.fn();
  useHaloStore.setState({
    conversations: {
      a: {
        conversationId: "a",
        needsInputRestore: true,
        turns: [
          { id: "user-failed", role: "user", text: "Please try this" },
          {
            id: "assistant-failed",
            role: "assistant",
            status: "error",
            text: "",
            error: { code: "turn_failed", message: "Model unreachable", recoverable: true },
          },
        ],
      },
    },
  });
  render(
    <ChatView
      conversationId="a"
      connState="connected"
      sendUserMsg={sendUserMsg}
      sendInterrupt={vi.fn(() => "interrupt")}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Retry response" }));

  expect(sendUserMsg).toHaveBeenCalledWith("a", "Please try this");
  const turns = useHaloStore.getState().conversations.a.turns;
  expect(turns[turns.length - 1]).toMatchObject({
    role: "assistant",
    status: "streaming",
  });
});
