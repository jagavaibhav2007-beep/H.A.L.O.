import { expect, test } from "vitest";
import type { ActivityMsg, ApprovalRequestMsg, TaskStateMsg } from "../ipc/contract";
import {
  appendUserTurn,
  applyConnectionEvent,
  applyFrame,
  beginUserRequest,
  CONVERSATION_CAP,
  initialState,
  type HaloState,
} from "./reducer";
import { useHaloStore } from "./store";

let sequence = 0;
const envelope = () => ({ id: `test-${++sequence}`, ts: `2026-07-22T00:00:${String(sequence).padStart(2, "0")}Z` });

test("an error before the first token creates a visible assistant error turn", () => {
  let state = appendUserTurn(initialState, "chat", "hello", "user-1");
  state = applyFrame(state, {
    type: "error",
    ...envelope(),
    code: "no_api_key",
    message: "add your OpenRouter key in Settings",
    recoverable: true,
    conversation_id: "chat",
  });

  const turn = state.conversations.chat.turns[1];
  expect(turn).toMatchObject({
    role: "assistant",
    status: "error",
    error: { code: "no_api_key", message: "add your OpenRouter key in Settings" },
  });
  expect(state.conversations.chat.needsInputRestore).toBe(true);
});

test("a queued follow-up does not split the assistant turn already streaming", () => {
  let state = appendUserTurn(initialState, "chat", "first", "user-1");
  state = applyFrame(state, { type: "token", ...envelope(), text: "Hello", conversation_id: "chat" });
  state = appendUserTurn(state, "chat", "second", "user-2");
  state = applyFrame(state, { type: "token", ...envelope(), text: " world", conversation_id: "chat" });
  state = applyFrame(state, { type: "done", ...envelope(), conversation_id: "chat" });

  expect(state.conversations.chat.turns).toHaveLength(3);
  expect(state.conversations.chat.turns[1]).toMatchObject({ role: "assistant", text: "Hello world", status: "done" });
  expect(state.conversations.chat.turns[2]).toMatchObject({ role: "user", text: "second" });
});

test("stored history hydrates only an empty conversation", () => {
  const history = {
    type: "conversation_history_state" as const,
    ...envelope(),
    conversation_id: "old-chat",
    turns: [
      { role: "user" as const, text: "What did we decide?" },
      { role: "assistant" as const, text: "Use the smaller implementation." },
    ],
  };
  let state = applyFrame(initialState, history);
  expect(state.conversations["old-chat"].historyLoaded).toBe(true);
  expect(state.conversations["old-chat"].turns).toMatchObject([
    { role: "user", text: "What did we decide?" },
    { role: "assistant", text: "Use the smaller implementation.", status: "done" },
  ]);
  const hydrated = state;
  state = applyFrame(state, {
    ...history,
    id: "duplicate-history",
    turns: [{ role: "assistant", text: "A duplicate must not replace the transcript." }],
  });
  expect(state).toBe(hydrated);

  state = appendUserTurn(state, "live-chat", "A newer message", "live-user");
  state = applyFrame(state, { ...history, conversation_id: "live-chat" });
  expect(state.conversations["live-chat"].turns).toHaveLength(1);
  expect(state.conversations["live-chat"].turns[0]).toMatchObject({ role: "user", text: "A newer message" });
  expect(state.conversations["live-chat"].historyLoaded).toBe(true);
});

test("terminal discovery failure has a distinct unavailable connection state", () => {
  const state = applyConnectionEvent(initialState, { type: "ws_unavailable" });
  expect(state.connection.wsStatus).toBe("unavailable");
});

test("a queued follow-up placeholder does not steal the turn already streaming", () => {
  let state = beginUserRequest(initialState, "chat", "first", "req-1");
  state = applyFrame(state, { type: "token", ...envelope(), text: "Hel", conversation_id: "chat" });
  state = beginUserRequest(state, "chat", "second", "req-2");
  state = applyFrame(state, { type: "token", ...envelope(), text: "lo", conversation_id: "chat" });
  state = applyFrame(state, { type: "done", ...envelope(), conversation_id: "chat" });

  const turns = state.conversations.chat.turns;
  expect(turns[1]).toMatchObject({ role: "assistant", text: "Hello", status: "done" });
  expect(turns[3]).toMatchObject({ role: "assistant", text: "", status: "streaming" });
});

test("an abandoned placeholder cannot absorb the next turn's frames", () => {
  // req-1 never gets a single frame back (dropped/failed turn). Oldest-first
  // matching would otherwise route req-2's whole reply into its bubble.
  let state = beginUserRequest(initialState, "chat", "first", "req-1");
  state = beginUserRequest(state, "chat", "second", "req-2");
  state = applyFrame(state, { type: "token", ...envelope(), text: "answer", conversation_id: "chat" });
  state = applyFrame(state, { type: "done", ...envelope(), conversation_id: "chat" });

  const turns = state.conversations.chat.turns;
  expect(turns[1]).toMatchObject({ role: "assistant", text: "", status: "interrupted" });
  expect(turns[3]).toMatchObject({ role: "assistant", text: "answer", status: "done" });
});

function task(task_id: string, state: TaskStateMsg["state"] = "running"): TaskStateMsg {
  return { type: "task_state", ...envelope(), task_id, state, lane: 1 };
}

function approval(approval_id: string, task_id: string, conversation_id?: string): ApprovalRequestMsg {
  return {
    type: "approval_request",
    ...envelope(),
    approval_id,
    task_id,
    tool: "file_delete",
    args_redacted: { path: "***" },
    tier: 3,
    ...(conversation_id ? { conversation_id } : {}),
  };
}

function activity(task_id: string, id?: string): ActivityMsg {
  return {
    type: "activity",
    ...envelope(),
    ...(id ? { id } : {}),
    text: "Created a file.",
    narrate: false,
    task_id,
    undoable: true,
    undo_token: "undo-1",
    tier: 2,
    lane: 1,
  };
}

function finishSnapshot(state: HaloState): HaloState {
  return applyFrame(state, { type: "spend_update", ...envelope(), session_usd: 0, month_usd: 0 });
}

test("a reconnect snapshot removes tasks and approvals that the Brain no longer reports", () => {
  let state = initialState;
  state = applyFrame(state, task("finished-while-away"));
  state = applyFrame(state, approval("handled-while-away", "finished-while-away"));
  state = applyConnectionEvent(state, { type: "ws_closed" });
  state = finishSnapshot(state);

  expect(state.tasks).toEqual({});
  expect(state.approvals).toEqual({});
});

test("snapshot backlog neither duplicates activity nor resolves a rehydrated approval", () => {
  let state = initialState;
  state = applyFrame(state, activity("shared-task", "live-activity"));
  state = applyConnectionEvent(state, { type: "ws_closed" });
  state = applyFrame(state, task("shared-task", "waiting_approval"));
  state = applyFrame(state, approval("still-pending", "shared-task"));
  state = applyFrame(state, activity("shared-task", "snapshot-replay"));
  state = finishSnapshot(state);

  expect(state.activities).toHaveLength(1);
  expect(state.approvals["still-pending"]).toBeDefined();
  expect(state.tasks["shared-task"].state).toBe("waiting_approval");
});

test("the first authenticated snapshot reconciles state just like a reconnect", () => {
  let state = applyFrame(initialState, task("stale-before-auth"));
  state = applyConnectionEvent(state, { type: "authenticated" });
  state = applyFrame(state, task("reported-by-snapshot"));
  state = finishSnapshot(state);

  expect(Object.keys(state.tasks)).toEqual(["reported-by-snapshot"]);
});

test("conversations are LRU-capped so the always-on orb window can't leak them", () => {
  // The orb never prunes via the workspace registry, so applyFrame itself must
  // bound the map. Open CAP+10 distinct conversations; the oldest 10 are reaped
  // and the most recent survive.
  let state = initialState;
  for (let i = 0; i < CONVERSATION_CAP + 10; i++) {
    state = applyFrame(state, { type: "token", ...envelope(), text: "x", conversation_id: `c-${i}` });
  }
  expect(Object.keys(state.conversations)).toHaveLength(CONVERSATION_CAP);
  expect(state.conversations["c-0"]).toBeUndefined();
  expect(state.conversations[`c-${CONVERSATION_CAP + 9}`]).toBeDefined();

  // Touching an old-but-surviving conversation refreshes its recency: it must
  // not be the next one evicted.
  const survivor = `c-10`;
  state = applyFrame(state, { type: "token", ...envelope(), text: "y", conversation_id: survivor });
  state = applyFrame(state, { type: "token", ...envelope(), text: "z", conversation_id: "c-new" });
  expect(state.conversations[survivor]).toBeDefined();
  expect(Object.keys(state.conversations)).toHaveLength(CONVERSATION_CAP);
});

test("snapshot_complete terminates snapshot mode and reconciles, like spend_update", () => {
  let state = initialState;
  state = applyFrame(state, task("finished-while-away"));
  state = applyConnectionEvent(state, { type: "ws_closed" });
  state = applyFrame(state, task("reported-by-snapshot"));
  state = applyFrame(state, { type: "snapshot_complete", ...envelope() });

  // The authoritative marker reconciled: the task the snapshot didn't re-report is gone.
  expect(Object.keys(state.tasks)).toEqual(["reported-by-snapshot"]);
  // And we truly left snapshot mode: a later duplicate activity is no longer deduped.
  state = applyFrame(state, activity("reported-by-snapshot", "live-line"));
  const before = state.activities.length;
  state = applyFrame(state, activity("reported-by-snapshot", "live-line"));
  expect(state.activities.length).toBe(before + 1);
});

test("spend_update still terminates a snapshot when snapshot_complete never arrives (backstop)", () => {
  // The mock and any Brain that omits the marker must still converge, never
  // wedge in snapshot mode silently swallowing live frames.
  let state = initialState;
  state = applyFrame(state, task("finished-while-away"));
  state = applyConnectionEvent(state, { type: "ws_closed" });
  state = applyFrame(state, task("reported-by-snapshot"));
  state = finishSnapshot(state); // spend_update only, no snapshot_complete

  expect(Object.keys(state.tasks)).toEqual(["reported-by-snapshot"]);
});

test("a correlated approval failure is recorded and also closes its conversation turn", () => {
  let state = applyFrame(initialState, approval("approval-1", "task-1"));
  state = applyFrame(state, approval("approval-2", "task-2"));
  state = beginUserRequest(state, "chat", "run the approved action", "user-approval");
  state = applyFrame(state, {
    type: "error", ...envelope(), code: "approval_already_handled", message: "Already handled.",
    recoverable: true, conversation_id: "chat", operation_kind: "approval_response", operation_id: "approval-1",
  });
  expect(state.approvals["approval-1"]).toBeUndefined();
  expect(state.approvals["approval-2"]).toBeDefined();
  expect(state.operationErrors["approval_response:approval-1"].message).toBe("Already handled.");
  expect(state.conversations.chat.turns[1]).toMatchObject({
    role: "assistant",
    status: "error",
    error: { message: "Already handled." },
  });
});

test("terminal done clears only the approval owned by that conversation", () => {
  let state = applyFrame(initialState, approval("approval-chat", "task-chat", "chat"));
  state = applyFrame(state, approval("approval-other", "task-other", "other"));
  state = beginUserRequest(state, "chat", "run an action", "user-action");
  state = applyFrame(state, {
    type: "done",
    ...envelope(),
    conversation_id: "chat",
  });

  expect(state.approvals["approval-chat"]).toBeUndefined();
  expect(state.approvals["approval-other"]).toBeDefined();
  expect(state.conversations.chat.turns[1]).toMatchObject({ role: "assistant", status: "done" });
});

test("terminal error clears the conversation approval when no activity can arrive", () => {
  let state = applyFrame(initialState, approval("approval-chat", "task-chat", "chat"));
  state = applyFrame(state, {
    type: "error",
    ...envelope(),
    code: "turn_failed",
    message: "The approved action failed.",
    recoverable: true,
    conversation_id: "chat",
  });

  expect(state.approvals["approval-chat"]).toBeUndefined();
  const turns = state.conversations.chat.turns;
  expect(turns[turns.length - 1]).toMatchObject({
    role: "assistant",
    status: "error",
  });
});

test("an interrupted done frame closes the pending turn as redirected, not complete", () => {
  let state = appendUserTurn(initialState, "chat", "keep going", "user-interrupt");
  state = applyFrame(state, { type: "token", ...envelope(), text: "partial", conversation_id: "chat" });
  state = applyFrame(state, {
    type: "done",
    ...envelope(),
    conversation_id: "chat",
    interrupted: true,
  });

  expect(state.conversations.chat.turns[1]).toMatchObject({
    role: "assistant",
    status: "interrupted",
    text: "partial",
    note: "stopped · what should I do differently?",
  });
});

test("capabilities are projected so unsupported controls can be disabled before use", () => {
  const state = applyFrame(initialState, {
    type: "capabilities_state",
    ...envelope(),
    voice_input: false,
    task_controls: false,
    skill_controls: false,
    demo_scenarios: false,
  });
  expect(state.capabilities).toEqual({
    voiceInput: false,
    taskControls: false,
    skillControls: false,
    demoScenarios: false,
  });
});

test("disconnect closes every queued assistant placeholder instead of leaving later turns spinning", () => {
  let state = beginUserRequest(initialState, "chat", "first", "queued-1");
  state = beginUserRequest(state, "chat", "second", "queued-2");
  state = applyConnectionEvent(state, { type: "ws_closed" });

  const assistantTurns = state.conversations.chat.turns.filter((turn) => turn.role === "assistant");
  expect(assistantTurns).toHaveLength(2);
  expect(assistantTurns.every((turn) => turn.status === "interrupted")).toBe(true);
  expect(state.conversations.chat.needsInputRestore).toBe(true);
});

test("a placeholder paused on a pending approval is not treated as abandoned", () => {
  let state = beginUserRequest(initialState, "chat", "delete the folder", "req-1");
  state = applyFrame(state, approval("approval-wait", "task-1", "chat"));
  state = beginUserRequest(state, "chat", "actually, hold on", "req-2");

  expect(state.conversations.chat.turns[1]).toMatchObject({ role: "assistant", status: "streaming" });
});

test("global errors are retained for an accessible workspace surface", () => {
  const state = applyFrame(initialState, {
    type: "error",
    ...envelope(),
    code: "keystore_unavailable",
    message: "Credential storage is unavailable.",
    recoverable: true,
  });

  expect(state.globalErrors).toHaveLength(1);
  expect(state.globalErrors[0].message).toBe("Credential storage is unavailable.");
});

test("deleting a thread drops its turns, not just its tab (always-on session leak)", () => {
  const first = useHaloStore.getState().chats.activeId;
  useHaloStore.getState().appendUserTurn(first, "hello", "leak-1");
  useHaloStore.getState().newConversation();
  const second = useHaloStore.getState().chats.activeId;
  useHaloStore.getState().appendUserTurn(second, "another thread", "leak-2");
  expect(Object.keys(useHaloStore.getState().conversations)).toEqual([first, second]);

  useHaloStore.getState().deleteConversation(first);

  expect(useHaloStore.getState().conversations[first]).toBeUndefined();
  expect(useHaloStore.getState().conversations[second]).toBeDefined();
});

test("memory history completion and permanent deletion update the projected memory state", () => {
  const belief = {
    type: "belief_state" as const,
    ...envelope(),
    belief_id: "archived-1",
    text: "Old preference",
    kind: "preference" as const,
    provenance: "user" as const,
    salience: 0.4,
    status: "archived" as const,
  };
  const activeBelief = { ...belief, belief_id: "active-1", status: "active" as const };
  let state = applyFrame(applyFrame(initialState, activeBelief), belief);
  state = applyFrame(state, { type: "memory_history_state", ...envelope(), complete: false });
  expect(state.beliefs[belief.belief_id]).toBeUndefined();
  expect(state.beliefs[activeBelief.belief_id]).toBeDefined();
  state = applyFrame(state, belief);
  state = applyFrame(state, { type: "memory_history_state", ...envelope(), complete: true });
  expect(state.memoryHistoryLoaded).toBe(true);

  state = applyFrame(state, { type: "belief_deleted", ...envelope(), belief_id: belief.belief_id });
  expect(state.beliefs[belief.belief_id]).toBeUndefined();
});
