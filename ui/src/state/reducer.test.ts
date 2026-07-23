import { expect, test } from "vitest";
import type { ActivityMsg, ApprovalRequestMsg, TaskStateMsg } from "../ipc/contract";
import { appendUserTurn, applyConnectionEvent, applyFrame, initialState, type HaloState } from "./reducer";

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

function task(task_id: string, state: TaskStateMsg["state"] = "running"): TaskStateMsg {
  return { type: "task_state", ...envelope(), task_id, state, lane: 1 };
}

function approval(approval_id: string, task_id: string): ApprovalRequestMsg {
  return {
    type: "approval_request",
    ...envelope(),
    approval_id,
    task_id,
    tool: "file_delete",
    args_redacted: { path: "***" },
    tier: 3,
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

test("correlated operation errors stay out of chat and release the exact approval", () => {
  let state = applyFrame(initialState, approval("approval-1", "task-1"));
  state = applyFrame(state, approval("approval-2", "task-2"));
  state = applyFrame(state, {
    type: "error", ...envelope(), code: "approval_already_handled", message: "Already handled.",
    recoverable: true, conversation_id: "chat", operation_kind: "approval_response", operation_id: "approval-1",
  });
  expect(state.approvals["approval-1"]).toBeUndefined();
  expect(state.approvals["approval-2"]).toBeDefined();
  expect(state.operationErrors["approval_response:approval-1"].message).toBe("Already handled.");
  expect(state.conversations.chat).toBeUndefined();
});
