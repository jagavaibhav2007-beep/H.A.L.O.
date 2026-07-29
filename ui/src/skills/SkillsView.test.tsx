import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SkillStateMsg } from "../ipc/contract";
import { useHaloStore } from "../state/store";
import { SkillsView } from "./SkillsView";

const skill: SkillStateMsg = {
  type: "skill_state", id: "skill-frame", ts: "2026-07-22T00:00:00Z",
  skill_name: "Daily summary", origin: "user", kind: "skill", uses: 2,
  success_rate: 1, status: "active", born_at: "2026-07-22T00:00:00Z",
};

beforeEach(() => {
  installLocalStorage();
  useHaloStore.setState(useHaloStore.getInitialState(), true);
  useHaloStore.setState({
    skills: { [skill.skill_name]: skill },
    capabilities: {
      voiceInput: false,
      taskControls: false,
      skillControls: true,
      demoScenarios: false,
    },
  });
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

test("trial dialog takes focus, traps Tab, closes on Escape, and restores focus", () => {
  render(<SkillsView sendSkillOp={vi.fn()} />);
  const opener = screen.getByRole("button", { name: /trial run/i });
  fireEvent.click(opener);
  const dialog = screen.getByRole("dialog", { name: "Trial run — Daily summary" });
  const close = screen.getByRole("button", { name: "Close trial run" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(dialog, { key: "Tab" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(opener);
});
