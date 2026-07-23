import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { useHaloStore } from "../state/store";
import { ChatView } from "./ChatView";

beforeEach(() => {
  useHaloStore.setState(useHaloStore.getInitialState(), true);
  useHaloStore.setState({
    chats: {
      all: [
        { id: "a", title: "A", lastUsedAt: 2 },
        { id: "b", title: "B", lastUsedAt: 1 },
      ],
      open: ["a", "b"],
      activeId: "a",
    },
  });
});

test("drafts and failed-message restoration stay scoped to their conversation", () => {
  const props = {
    connState: "connected" as const,
    sendUserMsg: vi.fn(),
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
