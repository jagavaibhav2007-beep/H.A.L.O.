import { beforeEach, expect, test, vi } from "vitest";

const readFile = vi.hoisted(() => vi.fn());
vi.mock("node:fs/promises", () => ({ default: { readFile }, readFile }));

import { browserSessionResponse } from "./browserSessionPlugin";

const request = {
  enabled: true,
  localAppData: "C:\\Users\\test\\AppData\\Local",
  method: "GET",
  url: "/__halo/session",
  host: "127.0.0.1:1420",
  origin: "http://127.0.0.1:1420",
};

beforeEach(() => readFile.mockReset());

test("the disabled bridge is terminal and never reads the token file", async () => {
  await expect(browserSessionResponse({ ...request, enabled: false })).resolves.toEqual({ status: 404 });
  expect(readFile).not.toHaveBeenCalled();
});

test("the bridge rejects foreign hosts and origins before reading the token", async () => {
  await expect(browserSessionResponse({ ...request, host: "attacker.example" })).resolves.toEqual({ status: 403 });
  await expect(browserSessionResponse({ ...request, origin: "https://attacker.example" })).resolves.toEqual({ status: 403 });
  expect(readFile).not.toHaveBeenCalled();
});

test("the enabled loopback bridge fresh-reads and validates the session", async () => {
  readFile.mockResolvedValue(JSON.stringify({ port: 43123, token: "secret" }));
  const log = vi.spyOn(console, "log").mockImplementation(() => {});

  await expect(browserSessionResponse(request)).resolves.toEqual({
    status: 200,
    body: { port: 43123, token: "secret" },
  });
  expect(readFile).toHaveBeenCalledTimes(1);
  expect(readFile.mock.calls[0][0]).toMatch(/Halo[\\/]session\.json$/);
  expect(log).not.toHaveBeenCalled();
});

test("a missing or malformed session is retryable", async () => {
  readFile.mockRejectedValueOnce(new Error("missing"));
  await expect(browserSessionResponse(request)).resolves.toEqual({ status: 503 });

  readFile.mockResolvedValueOnce("{}");
  await expect(browserSessionResponse(request)).resolves.toEqual({ status: 503 });
});
