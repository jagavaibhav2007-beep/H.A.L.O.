import { beforeEach, expect, test, vi } from "vitest";

const tauri = vi.hoisted(() => ({
  invoke: vi.fn(),
  isTauri: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => tauri);

import { readSession, SessionDiscoveryError } from "./sessionSource";

beforeEach(() => {
  vi.restoreAllMocks();
  tauri.invoke.mockReset();
  tauri.isTauri.mockReset();
});

test("browser discovery uses the local dev bridge and never invokes Tauri", async () => {
  tauri.isTauri.mockReturnValue(false);
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ port: 43123, token: "secret" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );

  await expect(readSession()).resolves.toEqual({ port: 43123, token: "secret" });
  expect(fetchMock).toHaveBeenCalledWith("/__halo/session", { cache: "no-store" });
  expect(tauri.invoke).not.toHaveBeenCalled();
});

test("native discovery keeps using the fresh Tauri read_session command", async () => {
  tauri.isTauri.mockReturnValue(true);
  tauri.invoke.mockResolvedValue({ port: 43124, token: "native-secret" });
  const fetchMock = vi.spyOn(globalThis, "fetch");

  await expect(readSession()).resolves.toEqual({ port: 43124, token: "native-secret" });
  expect(tauri.invoke).toHaveBeenCalledWith("read_session");
  expect(fetchMock).not.toHaveBeenCalled();
});

test("a starting Brain is retryable but a disabled browser bridge is terminal", async () => {
  tauri.isTauri.mockReturnValue(false);
  const fetchMock = vi.spyOn(globalThis, "fetch");
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));

  await expect(readSession()).rejects.toMatchObject({ retryable: true } satisfies Partial<SessionDiscoveryError>);

  fetchMock.mockResolvedValueOnce(new Response(null, { status: 404 }));
  await expect(readSession()).rejects.toMatchObject({ retryable: false } satisfies Partial<SessionDiscoveryError>);
});
