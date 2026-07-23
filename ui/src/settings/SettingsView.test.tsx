import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useHaloStore } from "../state/store";
import { SettingsView } from "./SettingsView";

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

test("shows an unknown key state and never claims mock model IDs", () => {
  const view = render(<SettingsView sendSettingsUpdate={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toContain("checking stored status…");
  expect(view.container.textContent).not.toContain("halo-mock");
  expect(screen.getByText("Selected automatically by the Brain")).not.toBeNull();
  useHaloStore.setState({ settings: { openrouter_key: "missing" } });
  view.rerender(<SettingsView sendSettingsUpdate={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toContain("not set");
});
