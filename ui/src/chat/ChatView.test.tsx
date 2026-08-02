import { StrictMode } from "react";
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

test("a restored conversation requests its stored transcript", () => {
  useHaloStore.setState({
    chats: {
      ...useHaloStore.getState().chats,
      all: useHaloStore.getState().chats.all.map((chat) =>
        chat.id === "a" ? { ...chat, restored: true } : chat,
      ),
    },
  });
  const sendConversationHistoryQuery = vi.fn();
  render(
    <StrictMode>
      <ChatView
        conversationId="a"
        connState="connected"
        sendUserMsg={vi.fn()}
        sendConversationHistoryQuery={sendConversationHistoryQuery}
        sendInterrupt={vi.fn()}
        sendMic={vi.fn()}
        inputId="composer"
      />
    </StrictMode>,
  );
  expect(sendConversationHistoryQuery).toHaveBeenCalledWith("a");
  expect(sendConversationHistoryQuery).toHaveBeenCalledTimes(1);
});

test("a restored conversation retries history after reconnecting", () => {
  useHaloStore.setState({
    chats: {
      ...useHaloStore.getState().chats,
      all: useHaloStore.getState().chats.all.map((chat) =>
        chat.id === "a" ? { ...chat, restored: true } : chat,
      ),
    },
  });
  const sendConversationHistoryQuery = vi.fn();
  const props = {
    conversationId: "a",
    sendUserMsg: vi.fn(),
    sendConversationHistoryQuery,
    sendInterrupt: vi.fn(),
    sendMic: vi.fn(),
    inputId: "composer",
  };
  const view = render(<ChatView {...props} connState="connected" />);
  view.rerender(<ChatView {...props} connState="reconnecting" />);
  view.rerender(<ChatView {...props} connState="connected" />);
  expect(sendConversationHistoryQuery).toHaveBeenCalledTimes(2);
});

test("an empty stored transcript finishes loading instead of requesting forever", () => {
  useHaloStore.setState({
    chats: {
      ...useHaloStore.getState().chats,
      all: useHaloStore.getState().chats.all.map((chat) =>
        chat.id === "a" ? { ...chat, restored: true } : chat,
      ),
    },
  });
  const sendConversationHistoryQuery = vi.fn();
  render(
    <ChatView
      conversationId="a"
      connState="connected"
      sendUserMsg={vi.fn()}
      sendConversationHistoryQuery={sendConversationHistoryQuery}
      sendInterrupt={vi.fn()}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );
  act(() => {
    useHaloStore.getState().applyFrame({
      type: "conversation_history_state",
      id: "empty-history",
      ts: "2026-07-30T00:00:00Z",
      conversation_id: "a",
      turns: [],
    });
  });

  expect(screen.getByText("This conversation has no earlier messages.")).toBeTruthy();
  expect(sendConversationHistoryQuery).toHaveBeenCalledTimes(1);
});

test("out-of-order history responses stay with their own conversation", () => {
  useHaloStore.setState({
    chats: {
      ...useHaloStore.getState().chats,
      all: useHaloStore.getState().chats.all.map((chat) => ({ ...chat, restored: true })),
    },
  });
  const props = {
    connState: "connected" as const,
    sendUserMsg: vi.fn(),
    sendConversationHistoryQuery: vi.fn(),
    sendInterrupt: vi.fn(),
    sendMic: vi.fn(),
    inputId: "composer",
  };
  const view = render(<ChatView {...props} conversationId="a" />);
  view.rerender(<ChatView {...props} conversationId="b" />);
  act(() => {
    useHaloStore.getState().applyFrame({
      type: "conversation_history_state",
      id: "history-a",
      ts: "2026-07-30T00:00:00Z",
      conversation_id: "a",
      turns: [{ role: "assistant", text: "Answer from A" }],
    });
  });
  expect(screen.queryByText("Answer from A")).toBeNull();
  act(() => {
    useHaloStore.getState().applyFrame({
      type: "conversation_history_state",
      id: "history-b",
      ts: "2026-07-30T00:00:01Z",
      conversation_id: "b",
      turns: [{ role: "assistant", text: "Answer from B" }],
    });
  });
  expect(screen.getByText("Answer from B")).toBeTruthy();
  view.rerender(<ChatView {...props} conversationId="a" />);
  expect(screen.getByText("Answer from A")).toBeTruthy();
});

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

  expect(sendUserMsg).toHaveBeenCalledWith("a", "Explain this", expect.any(String));
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

test("an unavailable browser bridge preserves the draft and cannot queue a send", () => {
  const sendUserMsg = vi.fn();
  render(
    <ChatView
      conversationId="a"
      connState="unavailable"
      sendUserMsg={sendUserMsg}
      sendInterrupt={vi.fn()}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );

  const composer = screen.getByRole("textbox", { name: "Message Halo" }) as HTMLTextAreaElement;
  fireEvent.change(composer, { target: { value: "keep this draft" } });
  fireEvent.keyDown(composer, { key: "Enter", shiftKey: false });

  expect(sendUserMsg).not.toHaveBeenCalled();
  expect(composer.value).toBe("keep this draft");
  expect(composer.disabled).toBe(true);
  expect(screen.getByText("Start Halo with ./dev.ps1 -Browser to chat here.")).toBeTruthy();
});

test("a message queued during reconnect is labelled queued and cannot be stopped", () => {
  render(
    <ChatView
      conversationId="a"
      connState="reconnecting"
      sendUserMsg={vi.fn()}
      sendInterrupt={vi.fn()}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );
  const composer = screen.getByRole("textbox", { name: "Message Halo" });
  fireEvent.change(composer, { target: { value: "send after reconnect" } });
  fireEvent.keyDown(composer, { key: "Enter", shiftKey: false });

  expect(screen.getByText("Queued — waiting for Halo to reconnect.")).toBeTruthy();
  // The composer's send/stop control now stays mounted and goes disabled rather
  // than unmounting (no layout shift mid-stream) — still unpressable offline,
  // which is what "cannot be stopped" is actually asserting.
  expect((screen.getByRole("button", { name: "Stop response" }) as HTMLButtonElement).disabled).toBe(true);
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

  expect(sendUserMsg).toHaveBeenCalledWith("a", "Please try this", expect.any(String));
  const turns = useHaloStore.getState().conversations.a.turns;
  expect(turns[turns.length - 1]).toMatchObject({
    role: "assistant",
    status: "streaming",
  });
});

test("a second message cannot be sent while a response is still streaming", () => {
  const sendUserMsg = vi.fn();
  useHaloStore.setState({
    conversations: {
      a: {
        conversationId: "a",
        needsInputRestore: false,
        turns: [{ id: "pending", role: "assistant", status: "streaming", text: "thinking" }],
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

  const composer = screen.getByRole("textbox", { name: "Message Halo" }) as HTMLTextAreaElement;
  fireEvent.change(composer, { target: { value: "second question" } });
  // Impatient double-Enter: neither keystroke may open a second turn.
  fireEvent.keyDown(composer, { key: "Enter", shiftKey: false });
  fireEvent.keyDown(composer, { key: "Enter", shiftKey: false });

  expect(sendUserMsg).not.toHaveBeenCalled();
  // rule 8-adjacent: a blocked send must not eat the text the user typed.
  expect(composer.value).toBe("second question");

  // ...and the typed-"stop" interrupt shortcut still has to work while gated.
  const sendInterrupt = vi.fn(() => "interrupt-2");
  cleanup();
  render(
    <ChatView
      conversationId="a"
      connState="connected"
      sendUserMsg={sendUserMsg}
      sendInterrupt={sendInterrupt}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );
  const second = screen.getByRole("textbox", { name: "Message Halo" });
  fireEvent.change(second, { target: { value: "stop" } });
  fireEvent.keyDown(second, { key: "Enter", shiftKey: false });
  expect(sendInterrupt).toHaveBeenCalledWith("a");
});

test("the send button sends when idle and stops when streaming", () => {
  const sendUserMsg = vi.fn();
  const sendInterrupt = vi.fn(() => "interrupt-3");
  useHaloStore.setState({ conversations: {} });
  render(
    <ChatView
      conversationId="a"
      connState="connected"
      sendUserMsg={sendUserMsg}
      sendInterrupt={sendInterrupt}
      sendMic={vi.fn()}
      inputId="composer"
    />,
  );

  const composer = screen.getByRole("textbox", { name: "Message Halo" });
  // Idle + empty box: nothing to send, so the control is disabled.
  expect((screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(composer, { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  expect(sendUserMsg).toHaveBeenCalledWith("a", "hello", expect.any(String));

  // The same control is now the stop square for the turn it just opened.
  const stop = screen.getByRole("button", { name: "Stop response" }) as HTMLButtonElement;
  fireEvent.click(stop);
  expect(sendInterrupt).toHaveBeenCalledWith("a");
  // Spam-clicking stop must not fire a second interrupt.
  fireEvent.click(screen.getByRole("button", { name: "Stop response" }));
  expect(sendInterrupt).toHaveBeenCalledTimes(1);
});
