import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { BeliefStateMsg } from "../ipc/contract";
import { useHaloStore } from "../state/store";
import { MemoryView } from "./MemoryView";

const archived: BeliefStateMsg = {
  type: "belief_state",
  id: "belief-frame",
  ts: "2026-07-27T00:00:00Z",
  belief_id: "archived-1",
  text: "I used to prefer tabs.",
  kind: "preference",
  provenance: "user",
  salience: 0.4,
  status: "archived",
};

beforeEach(() => {
  vi.useFakeTimers();
  useHaloStore.setState(useHaloStore.getInitialState(), true);
  useHaloStore.setState({
    connection: { ...useHaloStore.getState().connection, wsStatus: "connected" },
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("opening Memory requests durable archived and superseded history", () => {
  const sendMemoryQuery = vi.fn();
  render(
    <MemoryView
      active
      sendMemoryEdit={vi.fn()}
      sendMemoryQuery={sendMemoryQuery}
    />,
  );

  expect(sendMemoryQuery).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("status").textContent).toContain("Loading memory history");
});

test("an interrupted history request is retried after reconnection", () => {
  const sendMemoryQuery = vi.fn();
  render(
    <MemoryView active sendMemoryEdit={vi.fn()} sendMemoryQuery={sendMemoryQuery} />,
  );
  expect(sendMemoryQuery).toHaveBeenCalledTimes(1);

  act(() => {
    useHaloStore.getState().applyConnectionEvent({ type: "ws_closed" });
  });
  act(() => {
    useHaloStore.getState().applyConnectionEvent({ type: "authenticated" });
  });

  expect(sendMemoryQuery).toHaveBeenCalledTimes(2);
});

test("an unavailable Brain shows a stable empty state instead of a loading spinner", () => {
  useHaloStore.getState().applyConnectionEvent({ type: "ws_unavailable" });
  render(<MemoryView active sendMemoryEdit={vi.fn()} sendMemoryQuery={vi.fn()} />);

  expect(screen.getByRole("status").textContent).toContain("Memory is unavailable");
  expect(document.querySelector(".halo-spinner")).toBeNull();
});

test("soft delete copy stays truthful until the delayed archive request is sent", () => {
  const sendMemoryEdit = vi.fn();
  useHaloStore.setState({ beliefs: { [archived.belief_id]: { ...archived, status: "active" } } });
  render(
    <MemoryView
      active
      sendMemoryEdit={sendMemoryEdit}
      sendMemoryQuery={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(screen.getByText(/Will archive/).textContent).toContain("Will archive");
  expect(sendMemoryEdit).not.toHaveBeenCalled();

  act(() => vi.advanceTimersByTime(5000));
  expect(sendMemoryEdit).toHaveBeenCalledWith(archived.belief_id, "delete");
});

test("archived memories can be permanently removed only after explicit confirmation", () => {
  const sendMemoryEdit = vi.fn();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  useHaloStore.setState({
    beliefs: { [archived.belief_id]: archived },
    memoryHistoryLoaded: true,
  });
  render(
    <MemoryView
      active
      sendMemoryEdit={sendMemoryEdit}
      sendMemoryQuery={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: /archived/i }));
  fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));

  expect(window.confirm).toHaveBeenCalled();
  expect(sendMemoryEdit).toHaveBeenCalledWith(archived.belief_id, "purge");
});
