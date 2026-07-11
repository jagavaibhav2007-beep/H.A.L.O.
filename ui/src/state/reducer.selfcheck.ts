// Replays a canned frame log through the pure reducer and asserts the
// projected state. No test framework (repo convention) — run via
// `npx tsx ui/src/state/reducer.selfcheck.ts` (or from ui/: `npx tsx
// src/state/reducer.selfcheck.ts`).
import {
  ACTIVITY_CAP,
  applyConnectionEvent,
  applyFrame,
  initialState,
  type HaloState,
} from "./reducer.ts";
import type {
  ActivityMsg,
  ApprovalRequestMsg,
  BeliefStateMsg,
  TaskStateMsg,
  TokenMsg,
} from "../ipc/contract.ts";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`[reducer.selfcheck] FAILED: ${msg}`);
}

let idCounter = 0;
function envelope() {
  idCounter += 1;
  return { id: `id-${idCounter}`, ts: `2026-07-11T00:00:0${idCounter % 10}Z` };
}

function token(text: string, conversation_id: string): TokenMsg {
  return { type: "token", ...envelope(), text, conversation_id };
}

// ---- Scenario 1: happy-path stream (token x N -> done) ----
{
  let state: HaloState = initialState;
  state = applyFrame(state, token("Hel", "c1"));
  state = applyFrame(state, token("lo", "c1"));
  state = applyFrame(state, token(", world", "c1"));
  state = applyFrame(state, { type: "done", ...envelope(), conversation_id: "c1", task_id: "task-1" });

  const conv = state.conversations["c1"];
  assert(conv.turns.length === 1, "happy path: exactly one assistant turn");
  assert(conv.turns[0].text === "Hello, world", "happy path: tokens concatenated in arrival order");
  assert(conv.turns[0].status === "done", "happy path: turn closed by done");
  assert(conv.turns[0].taskId === "task-1", "happy path: done's task_id attached to the turn");
}

// ---- Scenario 2: reconnect mid-stream (open turn -> ws_closed -> interrupted) ----
{
  let state: HaloState = initialState;
  state = applyFrame(state, token("Wor", "c2"));
  state = applyConnectionEvent(state, { type: "ws_closed" });

  const conv = state.conversations["c2"];
  assert(conv.turns[0].status === "interrupted", "reconnect: open turn marked interrupted on ws_closed");
  assert(conv.turns[0].note === "interrupted — connection lost", "reconnect: interrupted marker text set");
  assert(conv.turns[0].text === "Wor", "reconnect: partial text preserved, not discarded");
  assert(state.connection.wsStatus === "reconnecting", "reconnect: connection slice flips to reconnecting");

  // A token frame after reconnect opens a NEW turn rather than reopening the dead one.
  state = applyFrame(state, token("ld", "c2"));
  assert(state.conversations["c2"].turns.length === 2, "reconnect: post-reconnect token starts a fresh turn");
}

// ---- Scenario 3: duplicate snapshot push (idempotent upserts converge, D6) ----
{
  let state: HaloState = initialState;
  const belief1: BeliefStateMsg = {
    type: "belief_state",
    ...envelope(),
    belief_id: "b1",
    text: "uses pnpm",
    kind: "preference",
    provenance: "inferred",
    salience: 0.5,
    status: "active",
  };
  const belief1Again: BeliefStateMsg = { ...belief1, ...envelope(), salience: 0.9 };
  const task1: TaskStateMsg = {
    type: "task_state",
    ...envelope(),
    task_id: "task-9",
    state: "running",
    lane: 1,
  };

  state = applyFrame(state, belief1);
  state = applyFrame(state, belief1Again); // snapshot re-push after reconnect
  state = applyFrame(state, task1);
  state = applyFrame(state, task1); // duplicate delta

  assert(Object.keys(state.beliefs).length === 1, "duplicate snapshot: beliefs converge, no duplicate entries");
  assert(state.beliefs["b1"].salience === 0.9, "duplicate snapshot: upsert keeps the latest write");
  assert(Object.keys(state.tasks).length === 1, "duplicate snapshot: tasks converge, no duplicate entries");
}

// ---- Scenario 4: approval round-trip, resolved via task_state/activity (rule 3) ----
{
  let state: HaloState = initialState;
  const approval: ApprovalRequestMsg = {
    type: "approval_request",
    ...envelope(),
    approval_id: "a1",
    tool: "fs.delete",
    args_redacted: { path: "***" },
    tier: 3,
    task_id: "task-2",
  };
  state = applyFrame(state, approval);
  assert(state.approvals["a1"] !== undefined, "approval: pending card present after approval_request");

  // Still waiting — must NOT resolve on the (implicit) button press, only on the confirming frame.
  state = applyFrame(state, {
    type: "task_state",
    ...envelope(),
    task_id: "task-2",
    state: "waiting_approval",
    lane: 1,
  });
  assert(state.approvals["a1"] !== undefined, "approval: still pending while task_state stays waiting_approval");

  // The confirming task_state resolves it.
  state = applyFrame(state, { type: "task_state", ...envelope(), task_id: "task-2", state: "running", lane: 1 });
  assert(state.approvals["a1"] === undefined, "approval: resolved (removed) by the confirming task_state");

  // Same round-trip via a confirming `activity` instead of `task_state`.
  const approval2: ApprovalRequestMsg = { ...approval, approval_id: "a2", task_id: "task-3" };
  state = applyFrame(state, approval2);
  state = applyFrame(state, {
    type: "activity",
    ...envelope(),
    text: "approved: ran fs.delete",
    narrate: false,
    task_id: "task-3",
    undoable: false,
  });
  assert(state.approvals["a2"] === undefined, "approval: resolved (removed) by a confirming activity");
}

// ---- Scenario 5: undo (activity with undo_token -> reversal activity) ----
{
  let state: HaloState = initialState;
  const original: ActivityMsg = {
    type: "activity",
    ...envelope(),
    text: "moved 3 files to Archive",
    narrate: true,
    task_id: "task-4",
    undoable: true,
    undo_token: "tok-1",
  };
  const reversal: ActivityMsg = {
    type: "activity",
    ...envelope(),
    text: "undone: moved 3 files back",
    narrate: true,
    task_id: "task-4",
    undoable: false,
  };
  state = applyFrame(state, original);
  state = applyFrame(state, reversal);

  assert(state.activities.length === 2, "undo: both the original and reversal activity are recorded");
  assert(state.activities[0].undo_token === "tok-1", "undo: original activity keeps its undo_token");
  assert(state.activities[1].text.startsWith("undone:"), "undo: reversal activity appended in arrival order");
}

// ---- Bonus: activities ring buffer caps at ACTIVITY_CAP, dropping oldest ----
{
  let state: HaloState = initialState;
  for (let i = 0; i < ACTIVITY_CAP + 5; i++) {
    state = applyFrame(state, {
      type: "activity",
      id: `act-${i}`,
      ts: envelope().ts,
      text: `entry ${i}`,
      narrate: false,
      task_id: "task-flood",
      undoable: false,
    });
  }
  assert(state.activities.length === ACTIVITY_CAP, "ring buffer: capped at ACTIVITY_CAP");
  assert(state.activities[0].id === "act-5", "ring buffer: oldest 5 entries dropped, order preserved");
  assert(
    state.activities[state.activities.length - 1].id === `act-${ACTIVITY_CAP + 4}`,
    "ring buffer: newest entry retained",
  );
}

console.log("[reducer.selfcheck] OK");
