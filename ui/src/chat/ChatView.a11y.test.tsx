import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useHaloStore } from "../state/store";
import { ChatView } from "./ChatView";

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

test("keeps the composer labelled and announces connection and response status", () => {
  useHaloStore.setState({ conversations: { chat: {
    conversationId: "chat", needsInputRestore: false,
    turns: [{ id: "assistant", role: "assistant", status: "streaming", text: "" }],
  } } });
  const props = { conversationId: "chat", sendUserMsg: vi.fn(), sendMic: vi.fn(), inputId: "halo-composer" };
  const view = render(<ChatView {...props} connState="connected" />);
  expect(screen.getByLabelText("Message Halo")).not.toBeNull();
  expect(screen.getByRole("status").textContent).toContain("Halo is thinking.");
  view.rerender(<ChatView {...props} connState="reconnecting" />);
  expect(screen.getByRole("status").textContent).toContain("Halo is reconnecting. Messages will send when reconnected.");
});

test("assistant failures are assertive alerts", () => {
  useHaloStore.setState({ conversations: { chat: {
    conversationId: "chat", needsInputRestore: false,
    turns: [{ id: "assistant", role: "assistant", status: "error", text: "",
      error: { code: "failed", message: "Could not answer", recoverable: true } }],
  } } });
  render(<ChatView conversationId="chat" connState="connected" sendUserMsg={vi.fn()} sendMic={vi.fn()} inputId="halo-composer" />);
  expect(screen.getByRole("alert").textContent).toContain("Could not answer");
});
