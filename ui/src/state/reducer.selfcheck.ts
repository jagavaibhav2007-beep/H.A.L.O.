// Replays a canned frame log through the pure reducer and asserts the
// projected state. No test framework (repo convention) — run via
// `npx tsx ui/src/state/reducer.selfcheck.ts` (or from ui/: `npx tsx
// src/state/reducer.selfcheck.ts`).
import {
  ACTIVITY_CAP,
  MAX_TURNS,
  appendUserTurn,
  beginUserRequest,
  applyConnectionEvent,
  applyFrame,
  initialState,
  type AssistantTurn,
  type HaloState,
  type Turn,
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

/** Narrow a Turn to AssistantTurn (turns are now a user|assistant union). */
function assistantTurn(t: Turn | undefined, msg: string): AssistantTurn {
  assert(t && t.role === "assistant", msg);
  return t;
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
  const t0 = assistantTurn(conv.turns[0], "happy path: turn is an assistant turn");
  assert(t0.text === "Hello, world", "happy path: tokens concatenated in arrival order");
  assert(t0.status === "done", "happy path: turn closed by done");
  assert(t0.taskId === "task-1", "happy path: done's task_id attached to the turn");
}

// ---- Scenario 2: reconnect mid-stream (open turn -> ws_closed -> interrupted) ----
{
  let state: HaloState = initialState;
  state = applyFrame(state, token("Wor", "c2"));
  state = applyConnectionEvent(state, { type: "ws_closed" });

  const conv = state.conversations["c2"];
  const c2t0 = assistantTurn(conv.turns[0], "reconnect: turn is an assistant turn");
  assert(c2t0.status === "interrupted", "reconnect: open turn marked interrupted on ws_closed");
  assert(c2t0.note === "interrupted — connection lost", "reconnect: interrupted marker text set");
  assert(c2t0.text === "Wor", "reconnect: partial text preserved, not discarded");
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

// ---- Scenario 7: user turn interleaves with a following assistant stream ----
// appendUserTurn (Step 8) records the user's own message; the next `token`
// for that conversation must open a FRESH assistant turn after it, not fold
// into the user turn — the one behavior the user|assistant union could
// silently regress.
{
  let state: HaloState = initialState;
  state = appendUserTurn(state, "c7", "demo everything", "u-1");
  state = applyFrame(state, token("On ", "c7"));
  state = applyFrame(state, token("it.", "c7"));
  state = applyFrame(state, { type: "done", ...envelope(), conversation_id: "c7" });

  const conv = state.conversations["c7"];
  assert(conv.turns.length === 2, "interleave: user turn + assistant turn, both present in order");
  assert(conv.turns[0].role === "user" && conv.turns[0].text === "demo everything", "interleave: user turn first");
  const reply = assistantTurn(conv.turns[1], "interleave: second turn is the assistant reply");
  assert(reply.text === "On it." && reply.status === "done", "interleave: assistant stream is its own turn");

  // An error after a user send flags input-restore; a subsequent send clears it.
  state = applyFrame(state, {
    type: "error",
    ...envelope(),
    code: "model_unreachable",
    message: "Model unreachable",
    recoverable: true,
    conversation_id: "c7",
  });
  assert(state.conversations["c7"].needsInputRestore, "interleave: error flags input-restore (rule 8)");
  state = appendUserTurn(state, "c7", "retry", "u-2");
  assert(!state.conversations["c7"].needsInputRestore, "interleave: a fresh send clears input-restore");
}

// ---- Scenario 8: stream_frame keeps only the latest, drops stale seq ----
{
  let state: HaloState = initialState;
  const frame = (seq: number, jpeg: string) =>
    ({ type: "stream_frame", ...envelope(), task_id: "task-sb", jpeg_b64: jpeg, seq }) as const;

  state = applyFrame(state, frame(0, "f0"));
  state = applyFrame(state, frame(2, "f2"));
  assert(state.streams["task-sb"].jpeg_b64 === "f2", "stream: newer seq replaces the current frame");

  state = applyFrame(state, frame(1, "f1-stale")); // arrives late, out of order
  assert(state.streams["task-sb"].jpeg_b64 === "f2", "stream: a stale (lower-seq) frame is dropped, not rendered");
  assert(state.streams["task-sb"].seq === 2, "stream: latest seq retained");
}

// ---- Scenario 9: voice transcript — partials ghost, final becomes a turn ----
{
  let state: HaloState = initialState;
  state = applyFrame(state, { type: "transcript", ...envelope(), text: "what's on", final: false, conversation_id: "cv" });
  assert(state.voice.transcript?.text === "what's on", "voice: partial transcript held as ghost");
  assert(state.conversations["cv"] === undefined, "voice: a partial does not create a turn yet");

  state = applyFrame(state, { type: "transcript", ...envelope(), text: "what's on my calendar", final: true, conversation_id: "cv" });
  assert(state.voice.transcript === null, "voice: ghost cleared on final (no lingering partial)");
  const turn = state.conversations["cv"].turns[0];
  assert(turn.role === "user" && turn.text === "what's on my calendar", "voice: final transcript solidifies into a user turn");
  assert(turn.role === "user" && turn.viaVoice === true, "voice: the solidified turn is flagged as spoken");
}

// ---- Scenario 10: settings_state upserts by key ----
{
  let state: HaloState = initialState;
  assert(state.settings.openrouter_key === undefined, "settings: no status before any frame");

  state = applyFrame(state, { type: "settings_state", ...envelope(), key: "openrouter_key", status: "missing" });
  assert(state.settings.openrouter_key === "missing", "settings: missing after fresh-connect push");

  state = applyFrame(state, { type: "settings_state", ...envelope(), key: "openrouter_key", status: "set" });
  assert(state.settings.openrouter_key === "set", "settings: set after a confirmed save");
}

// ---- Scenario 11: two conversations project independently ----
// The whole basis of the multi-chat tab strip: one thread's stream must never
// bleed into another's. The Brain keys LangGraph threads by conversation_id;
// this asserts the UI projection honours the same boundary.
{
  let state: HaloState = initialState;
  state = appendUserTurn(state, "cA", "how do I deploy?", "uA");
  state = appendUserTurn(state, "cB", "unrelated question", "uB");

  state = applyFrame(state, token("deploy ", "cA"));
  state = applyFrame(state, token("hello from B", "cB"));
  state = applyFrame(state, token("with ./dev.ps1", "cA"));

  const a = state.conversations["cA"];
  const b = state.conversations["cB"];
  assert(a.turns.length === 2 && b.turns.length === 2, "multi: each conversation has its own turns");
  assert(assistantTurn(a.turns[1], "multi: A streamed").text === "deploy with ./dev.ps1", "multi: A's tokens concatenate without B's");
  assert(assistantTurn(b.turns[1], "multi: B streamed").text === "hello from B", "multi: B is untouched by A's tokens");

  // done/error land only in the addressed conversation.
  state = applyFrame(state, { type: "done", ...envelope(), conversation_id: "cA", task_id: "tA" });
  assert(assistantTurn(state.conversations["cA"].turns[1], "multi: A closed").status === "done", "multi: done closes only A's turn");
  assert(assistantTurn(state.conversations["cB"].turns[1], "multi: B open").status === "streaming", "multi: B stays streaming");

  state = applyFrame(state, {
    type: "error", ...envelope(), code: "turn_failed", message: "nope", recoverable: true, conversation_id: "cB",
  });
  assert(state.conversations["cB"].needsInputRestore === true, "multi: error flags input restore on B");
  assert(state.conversations["cA"].needsInputRestore === false, "multi: A's input is not disturbed by B's error");
}

// ---- Scenario 12: a pre-token error is visible and restorable ----
{
  let state: HaloState = appendUserTurn(initialState, "c-error", "hello", "u-error");
  state = applyFrame(state, {
    type: "error",
    ...envelope(),
    code: "no_api_key",
    message: "Add an OpenRouter key in Settings.",
    recoverable: true,
    conversation_id: "c-error",
  });
  const conv = state.conversations["c-error"];
  const errorTurn = assistantTurn(conv.turns[1], "pre-token error: assistant error turn is created");
  assert(errorTurn.status === "error", "pre-token error: turn is visibly terminal");
  assert(errorTurn.error?.code === "no_api_key", "pre-token error: cause is retained");
  assert(conv.needsInputRestore, "pre-token error: failed input is marked for restoration");
}

// ---- Scenario 13: a rapid follow-up does not split an active stream ----
{
  let state: HaloState = appendUserTurn(initialState, "c-fast", "first", "u-fast-1");
  state = applyFrame(state, token("Hello", "c-fast"));
  state = appendUserTurn(state, "c-fast", "second", "u-fast-2");
  state = applyFrame(state, token(" world", "c-fast"));
  state = applyFrame(state, { type: "done", ...envelope(), conversation_id: "c-fast" });
  const conv = state.conversations["c-fast"];
  assert(conv.turns.length === 3, "rapid follow-up: no extra assistant bubble is created");
  const reply = assistantTurn(conv.turns[1], "rapid follow-up: original assistant turn remains addressable");
  assert(reply.text === "Hello world" && reply.status === "done", "rapid follow-up: stream remains contiguous");
  assert(conv.turns[2].role === "user" && conv.turns[2].text === "second", "rapid follow-up: queued user turn remains intact");
}

// ---- Scenario 14: snapshot absence reconciles and backlog is idempotent ----
{
  let state: HaloState = applyFrame(initialState, {
    type: "task_state", ...envelope(), task_id: "stale-task", state: "running", lane: 1,
  });
  state = applyFrame(state, {
    type: "activity", ...envelope(), text: "Created a file.", narrate: false,
    task_id: "shared-task", undoable: true, undo_token: "undo-live", tier: 2, lane: 1,
  });
  state = applyConnectionEvent(state, { type: "ws_closed" });
  state = applyConnectionEvent(state, { type: "authenticated" });
  state = applyFrame(state, {
    type: "task_state", ...envelope(), task_id: "shared-task", state: "waiting_approval", lane: 1,
  });
  state = applyFrame(state, {
    type: "approval_request", ...envelope(), approval_id: "pending-after-reconnect",
    tool: "file_delete", args_redacted: { path: "***" }, tier: 3, task_id: "shared-task",
  });
  state = applyFrame(state, {
    type: "activity", ...envelope(), text: "Created a file.", narrate: false,
    task_id: "shared-task", undoable: true, undo_token: "undo-snapshot", tier: 2, lane: 1,
  });
  state = applyFrame(state, { type: "spend_update", ...envelope(), session_usd: 0, month_usd: 0 });
  assert(state.tasks["stale-task"] !== undefined, "snapshot: spend update is not a terminator");
  state = applyFrame(state, { type: "snapshot_complete", ...envelope() });
  assert(state.tasks["stale-task"] === undefined, "snapshot: absent stale task is removed");
  assert(state.activities.length === 1, "snapshot: replayed activity is deduplicated");
  assert(state.approvals["pending-after-reconnect"] !== undefined, "snapshot: backlog does not erase pending approval");
}

// ---- Scenario: turns are capped per conversation, streaming turn survives (B5) ----
{
  let state: HaloState = initialState;
  // Drive well past MAX_TURNS with completed user+assistant pairs.
  for (let i = 0; i < MAX_TURNS + 50; i += 1) {
    state = beginUserRequest(state, "cap", `msg-${i}`, `msg-${i}`);
    state = applyFrame(state, { type: "token", ...envelope(), text: `reply ${i}`, conversation_id: "cap", turn_id: `msg-${i}` });
    state = applyFrame(state, { type: "done", ...envelope(), conversation_id: "cap", turn_id: `msg-${i}` });
  }
  const turns = state.conversations["cap"].turns;
  assert(turns.length <= MAX_TURNS, `turns capped: got ${turns.length}, cap ${MAX_TURNS}`);
  // Oldest were trimmed from the front; the most recent pair is retained.
  assert(
    turns.some((t) => t.role === "user" && t.text === `msg-${MAX_TURNS + 49}`),
    "cap: newest user turn retained",
  );
  assert(
    !turns.some((t) => t.role === "user" && t.text === "msg-0"),
    "cap: oldest user turn trimmed from the front",
  );

  // Now open a fresh streaming turn and cross the cap again: the OPEN streaming
  // turn must never be trimmed (ChatView's send-gate depends on finding it).
  let s2: HaloState = initialState;
  for (let i = 0; i < MAX_TURNS - 1; i += 1) {
    s2 = beginUserRequest(s2, "live", `m-${i}`, `m-${i}`);
    s2 = applyFrame(s2, { type: "done", ...envelope(), conversation_id: "live", turn_id: `m-${i}` });
  }
  // Start a streaming turn (user + pending assistant), then append more user
  // turns via appendUserTurn to push the array over the cap without closing it.
  s2 = beginUserRequest(s2, "live", "streaming-one", "streaming-one");
  for (let i = 0; i < 10; i += 1) s2 = appendUserTurn(s2, "live", `later-${i}`, `later-${i}`);
  const liveTurns = s2.conversations["live"].turns;
  assert(liveTurns.length <= MAX_TURNS, `live cap holds: ${liveTurns.length}`);
  assert(
    liveTurns.some((t) => t.role === "assistant" && t.status === "streaming"),
    "cap: the open streaming turn is never trimmed",
  );
}

console.log("[reducer.selfcheck] OK");
