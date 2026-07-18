// usePeekSource "most-recent-wins" across the two narration sources (live
// transcript while listening/speaking, and the newest narrate:true activity),
// plus the narrate filter (a non-narrated activity must never open the peek).
// Driven through the REAL store via applyFrame -- the same path the app uses --
// so this exercises the reducer -> selector -> hook chain, not a mock of it.
//
// Run: npx vitest run (from ui/), or npm test.

import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";
import type { IpcMessage } from "../ipc/contract";
import { useHaloStore } from "../state/store";
import { usePeekSource } from "./usePeekSource";

let seq = 0;

beforeEach(() => {
  // Reset the singleton store (state + actions) between tests.
  useHaloStore.setState(useHaloStore.getInitialState(), true);
});

function apply(partial: Record<string, unknown>) {
  act(() => {
    useHaloStore
      .getState()
      .applyFrame({ id: `m${seq++}`, ts: new Date().toISOString(), ...partial } as unknown as IpcMessage);
  });
}

test("newest narrated activity shows, a live transcript then wins, a non-narrated activity is ignored", () => {
  const { result } = renderHook(() => usePeekSource());
  expect(result.current).toBeNull();

  apply({ type: "activity", text: "Booked it.", narrate: true, task_id: "t1", undoable: false });
  expect(result.current).toBe("Booked it.");

  // A live transcript while listening is more recent -> it takes over.
  apply({ type: "voice_state", state: "listening" });
  apply({ type: "transcript", text: "remind me to", final: false, conversation_id: "c1" });
  expect(result.current).toBe("remind me to");

  // A non-narrated activity must NOT reopen the peek over the transcript.
  apply({ type: "activity", text: "(quiet background step)", narrate: false, task_id: "t2", undoable: false });
  expect(result.current).toBe("remind me to");
});
