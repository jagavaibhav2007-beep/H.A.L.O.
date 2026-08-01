import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  isTauri: vi.fn(() => false),
  listen: vi.fn(),
  readSession: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: mocks.invoke,
  isTauri: mocks.isTauri,
}));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("./sessionSource", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./sessionSource")>()),
  readSession: mocks.readSession,
}));

import { SessionDiscoveryError } from "./sessionSource";
import { useHaloConnection } from "./useHaloConnection";

const nativeWebSocket = globalThis.WebSocket;
const sockets: FakeWebSocket[] = [];

class FakeWebSocket {
  static readonly OPEN = 1;
  readonly sent: string[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(readonly url: string) {
    sockets.push(this);
  }

  send(value: string) {
    this.sent.push(value);
  }

  close() {
    this.readyState = 3;
  }
}

beforeEach(() => {
  mocks.invoke.mockReset();
  mocks.listen.mockReset();
  mocks.readSession.mockReset();
  mocks.isTauri.mockReturnValue(false);
  sockets.length = 0;
  Object.defineProperty(globalThis, "WebSocket", { configurable: true, value: FakeWebSocket });
});
afterEach(() => {
  cleanup();
  Object.defineProperty(globalThis, "WebSocket", { configurable: true, value: nativeWebSocket });
});

test("a disabled browser bridge becomes unavailable without retries or Tauri sidecar calls", async () => {
  mocks.readSession.mockRejectedValue(
    new SessionDiscoveryError("Start Halo with ./dev.ps1 -Browser.", false),
  );
  const hook = renderHook(() => useHaloConnection(vi.fn()));

  await waitFor(() => expect(hook.result.current.connState).toBe("unavailable"));
  await new Promise((resolve) => setTimeout(resolve, 20));

  expect(mocks.readSession).toHaveBeenCalledTimes(1);
  expect(mocks.invoke).not.toHaveBeenCalled();
  expect(mocks.listen).not.toHaveBeenCalled();
});

test("a discovered session sends hello before accepting hello_ack", async () => {
  mocks.readSession.mockResolvedValue({ port: 43123, token: "session-secret" });
  const hook = renderHook(() => useHaloConnection(vi.fn()));
  await waitFor(() => expect(sockets).toHaveLength(1));

  const socket = sockets[0];
  socket.onopen?.();
  expect(JSON.parse(socket.sent[0])).toMatchObject({
    type: "hello",
    token: "session-secret",
    role: "ui",
  });

  socket.onmessage?.({
    data: JSON.stringify({
      type: "hello_ack",
      id: "ack",
      ts: "2026-07-30T00:00:00Z",
      contract_version: "1.1",
    }),
  });
  await waitFor(() => expect(hook.result.current.connState).toBe("connected"));
});

test("authentication enters snapshot mode before the next websocket frame is projected", async () => {
  mocks.readSession.mockResolvedValue({ port: 43123, token: "session-secret" });
  const order: string[] = [];
  renderHook(() => useHaloConnection(
    (message) => order.push(message.type),
    () => order.push("authenticated"),
  ));
  await waitFor(() => expect(sockets).toHaveLength(1));
  const socket = sockets[0];
  socket.onopen?.();
  socket.onmessage?.({
    data: JSON.stringify({
      type: "hello_ack", id: "ack", ts: "2026-07-30T00:00:00Z", contract_version: "1.4",
    }),
  });
  socket.onmessage?.({
    data: JSON.stringify({
      type: "task_state", id: "task-frame", ts: "2026-07-30T00:00:01Z",
      task_id: "task-1", state: "running", lane: 1,
    }),
  });
  expect(order).toEqual(["authenticated", "task_state"]);
});
