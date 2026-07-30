// Phase 1 Step 4 — UI event store: pure projection of IPC frames into
// renderable state (D5, D6, D7; phase-1-plan.md "Step 4 — UI event store").
// No React, no WebSocket, no browser globals — importable and testable
// standalone (see reducer.selfcheck.ts). All impurity (zustand, subscriptions)
// lives in store.ts.

import type {
  ActivityMsg,
  ApprovalRequestMsg,
  BeliefStateMsg,
  ErrorMsg,
  IpcMessage,
  SkillStateMsg,
  StreamFrameMsg,
  TaskStateMsg,
  VoiceStateMsg,
} from "../ipc/contract";

// Keep this pure reducer runtime-dependency-free so its standalone Node
// self-check works without a bundler/TS module resolver.
const operationCorrelationKey = (kind: string, id: string) => `${kind}:${id}`;

// ---- Slice types ----

type WsStatus = "connecting" | "connected" | "reconnecting" | "unavailable";
type SidecarStatus = "unknown" | "starting" | "running" | "restarting" | "error";

// Two distinct signals that must never be conflated (Phase-0 rule, D5):
// WS-connected+authenticated (drives chat input / reconnect indicator) vs
// sidecar process health (drives the separate "Brain failed to start" banner).
interface ConnectionState {
  wsStatus: WsStatus;
  brainStatus: SidecarStatus;
  voiceStatus: SidecarStatus;
}

// The user's own messages never come back as frames (the mock doesn't echo,
// and Phase 2's real Brain won't either) — the reducer records them locally
// via appendUserTurn at send time, so one ordered `turns` array holds the
// whole conversation in arrival order (D7). `role` is the discriminant.
interface UserTurn {
  id: string;
  role: "user";
  text: string;
  viaVoice?: boolean; // spoken turn -> renders a small mic glyph
}

export interface AssistantTurn {
  id: string; // envelope id of the first `token` frame that opened this turn
  role: "assistant";
  status: "streaming" | "done" | "error" | "interrupted";
  text: string;
  taskId?: string;
  error?: { code: string; message: string; recoverable: boolean };
  note?: string; // e.g. "interrupted — connection lost"
}

export type Turn = UserTurn | AssistantTurn;

interface ConversationState {
  conversationId: string;
  turns: Turn[]; // full history, arrival order (D7) — never sorted by ts
  needsInputRestore: boolean; // rule 8: error/disconnect restores the user's text
  historyLoaded?: boolean;
}

interface VoiceState {
  state: VoiceStateMsg["state"];
  transcript: { text: string; final: boolean; conversationId: string } | null;
}

interface SpendState {
  sessionUsd: number;
  monthUsd: number;
  sessionTokens: number;
  lastTurnTokens: number;
}

interface CapabilityState {
  voiceInput: boolean | null;
  taskControls: boolean | null;
  skillControls: boolean | null;
  demoScenarios: boolean | null;
}

interface SnapshotState {
  pending: boolean;
  taskIds: Record<string, true>;
  approvalIds: Record<string, true>;
  activityCounts: Record<string, number>;
}

// Keyed by settings key (only "openrouter_key" exists so far) so the reducer
// stays total if Settings grows more server-confirmed keys later.
type SettingsState = Record<string, "set" | "missing" | "invalid" | "unverified">;

export interface HaloState {
  connection: ConnectionState;
  conversations: Record<string, ConversationState>;
  activities: ActivityMsg[]; // ring buffer capped at ACTIVITY_CAP, arrival order (D7)
  tasks: Record<string, TaskStateMsg>; // keyed by task_id (idempotent upsert, D6)
  streams: Record<string, StreamFrameMsg>; // latest sandbox stream_frame per task_id (stale seq dropped)
  approvals: Record<string, ApprovalRequestMsg>; // keyed by approval_id; presence = pending
  beliefs: Record<string, BeliefStateMsg>; // keyed by belief_id
  skills: Record<string, SkillStateMsg>; // keyed by skill_name
  voice: VoiceState;
  spend: SpendState;
  settings: SettingsState;
  capabilities: CapabilityState;
  operationErrors: Record<string, ErrorMsg>;
  globalErrors: ErrorMsg[];
  memoryHistoryLoaded: boolean;
  /** Connect snapshots end at snapshot_complete (authoritative), with spend_update
   * as a backstop for paths that don't send it. While one is pending, entity IDs
   * are collected so absence can reconcile stale reconnect state. */
  snapshot: SnapshotState;
}

export const ACTIVITY_CAP = 10_000;

// Bound on resident conversations. The orb window never calls the workspace's
// chat registry (that's what bounds the workspace at RECENT_CAP=50), yet
// applyFrame's getConversation creates an entry for every conversation_id it
// projects — so on an always-on app the orb's map would grow without limit.
// 2x the registry cap keeps this a strict superset of the 50 threads the
// workspace can render, so the LRU here never evicts a thread that's still
// visible; it only reaps threads both surfaces have already forgotten.
export const CONVERSATION_CAP = 100;

export const initialState: HaloState = {
  connection: { wsStatus: "connecting", brainStatus: "unknown", voiceStatus: "unknown" },
  conversations: {},
  activities: [],
  tasks: {},
  streams: {},
  approvals: {},
  beliefs: {},
  skills: {},
  voice: { state: "idle", transcript: null },
  spend: { sessionUsd: 0, monthUsd: 0, sessionTokens: 0, lastTurnTokens: 0 },
  settings: {},
  capabilities: {
    voiceInput: null,
    taskControls: null,
    skillControls: null,
    demoScenarios: null,
  },
  operationErrors: {},
  globalErrors: [],
  memoryHistoryLoaded: false,
  snapshot: { pending: false, taskIds: {}, approvalIds: {}, activityCounts: {} },
};

// ---- Helpers ----

// TS can't narrow `t === open` to AssistantTurn by identity alone, so the
// role check rides along at every patch site — centralized here.
function patchOpenTurn(turns: Turn[], open: Turn, patch: Partial<AssistantTurn>): Turn[] {
  return turns.map((t) => (t === open && t.role === "assistant" ? { ...t, ...patch } : t));
}

function getConversation(state: HaloState, conversationId: string): ConversationState {
  return (
    state.conversations[conversationId] ?? {
      conversationId,
      turns: [],
      needsInputRestore: false,
    }
  );
}

/** The oldest assistant turn still streaming. A queued user follow-up may sit
 * after it while the Brain finishes the current serialized turn. */
function openTurn(conv: ConversationState): AssistantTurn | undefined {
  // Requests to one conversation are serialized by the Brain. Pick the oldest
  // unresolved placeholder so a rapid follow-up cannot steal the first turn's
  // tokens or completion frame.
  for (let i = 0; i < conv.turns.length; i += 1) {
    const turn = conv.turns[i];
    if (turn.role === "assistant" && turn.status === "streaming") return turn;
  }
  return undefined;
}

/** Oldest-first matching means a placeholder that never receives a terminal
 * frame would otherwise absorb every later turn's tokens/done/error forever.
 * A placeholder that is still EMPTY when the user sends again is that case: a
 * live turn has either produced text already or is seconds old. Close it here
 * so the next turn opens a bubble of its own.
 * ponytail: arrival order is the only correlator the contract offers; a turn
 * id on token/done/error would let each frame address its own turn and retire
 * this heuristic. */
function closeAbandonedPlaceholders(state: HaloState, conv: ConversationState): ConversationState {
  // A Tier-3 approval pauses its turn for as long as the user takes to decide
  // — that placeholder is legitimately open, empty and silent.
  const awaitingApproval = Object.values(state.approvals).some(
    (approval) => approval.conversation_id === conv.conversationId,
  );
  const abandoned = (turn: Turn) =>
    turn.role === "assistant" && turn.status === "streaming" && turn.text === "";
  if (awaitingApproval || !conv.turns.some(abandoned)) return conv;
  return {
    ...conv,
    turns: conv.turns.map((turn) =>
      turn.role === "assistant" && abandoned(turn)
        ? { ...turn, status: "interrupted" as const, note: "interrupted — no reply arrived" }
        : turn,
    ),
  };
}

/** Record the user's own outgoing message as a turn (see UserTurn comment).
 * Called from the store at send time, not from a frame — user_msg is never
 * echoed back. Clears needsInputRestore: a fresh send means the prior
 * error/disconnect text has been dealt with. */
export function appendUserTurn(
  state: HaloState,
  conversationId: string,
  text: string,
  id: string,
): HaloState {
  const conv = getConversation(state, conversationId);
  const turn: UserTurn = { id, role: "user", text };
  return replaceConversation(state, {
    ...conv,
    turns: [...conv.turns, turn],
    needsInputRestore: false,
  });
}

/** Begin one UI-originated request. The local assistant placeholder gives
 * immediate feedback before the provider emits its first token. */
export function beginUserRequest(
  state: HaloState,
  conversationId: string,
  text: string,
  id: string,
): HaloState {
  const withUser = appendUserTurn(state, conversationId, text, id);
  const conv = closeAbandonedPlaceholders(withUser, getConversation(withUser, conversationId));
  const pending: AssistantTurn = {
    id: `pending-${id}`,
    role: "assistant",
    status: "streaming",
    text: "",
  };
  return replaceConversation(withUser, { ...conv, turns: [...conv.turns, pending] });
}

function replaceConversation(state: HaloState, conv: ConversationState): HaloState {
  // Re-insert last so object key order tracks recency, then reap the oldest
  // beyond the cap (JS preserves string-key insertion order). Pure: builds a
  // fresh object, never mutates state.conversations.
  const { [conv.conversationId]: _prev, ...rest } = state.conversations;
  const merged: Record<string, ConversationState> = { ...rest, [conv.conversationId]: conv };
  const keys = Object.keys(merged);
  if (keys.length > CONVERSATION_CAP) {
    for (const stale of keys.slice(0, keys.length - CONVERSATION_CAP)) delete merged[stale];
  }
  return { ...state, conversations: merged };
}

function pushActivity(state: HaloState, activity: ActivityMsg): HaloState {
  const next = [...state.activities, activity];
  if (next.length > ACTIVITY_CAP) next.splice(0, next.length - ACTIVITY_CAP); // drop oldest
  return { ...state, activities: next };
}

function activityIdentity(activity: ActivityMsg): string {
  // Snapshot replays get fresh envelope ids/timestamps and may change
  // undoable/token after an undo. The stable action shape plus occurrence
  // count lets a repeated identical action remain distinct.
  return JSON.stringify([
    activity.task_id,
    activity.text,
    activity.narrate,
    activity.tier ?? null,
    activity.lane ?? null,
  ]);
}

function reconcileSnapshotActivity(state: HaloState, activity: ActivityMsg): HaloState {
  const key = activityIdentity(activity);
  const occurrence = state.snapshot.activityCounts[key] ?? 0;
  let matched = -1;
  let found = 0;
  for (let i = 0; i < state.activities.length; i += 1) {
    if (activityIdentity(state.activities[i]) !== key) continue;
    if (found === occurrence) {
      matched = i;
      break;
    }
    found += 1;
  }

  const snapshot = {
    ...state.snapshot,
    activityCounts: { ...state.snapshot.activityCounts, [key]: occurrence + 1 },
  };
  if (matched < 0) {
    const withActivity = pushActivity(state, activity);
    return { ...withActivity, snapshot };
  }

  const activities = [...state.activities];
  activities[matched] = { ...activity, id: activities[matched].id, ts: activities[matched].ts };
  return { ...state, activities, snapshot };
}

/** Reconcile the id-keyed RUNTIME slices against what the snapshot reported:
 * a task or approval the Brain no longer knows about is gone (D6).
 *
 * `beliefs` and `skills` are deliberately excluded, and must stay that way.
 * They are DB-backed collections the snapshot only sends a *slice* of —
 * `memory.push_beliefs` replays live beliefs only, capped at 50, and archived/
 * superseded ones arrive later behind `memory_query`; the real Brain sends no
 * `skill_state` at connect at all. Filtering them by snapshot presence would
 * wipe the user's memory and skills panels on every reconnect. */
function finishSnapshot(state: HaloState): HaloState {
  if (!state.snapshot.pending) return state;
  const tasks = Object.fromEntries(
    Object.entries(state.tasks).filter(([id]) => state.snapshot.taskIds[id]),
  );
  const approvals = Object.fromEntries(
    Object.entries(state.approvals).filter(([id]) => state.snapshot.approvalIds[id]),
  );
  const streams = Object.fromEntries(
    Object.entries(state.streams).filter(([taskId]) => state.snapshot.taskIds[taskId]),
  );
  return {
    ...state,
    tasks,
    approvals,
    streams,
    snapshot: { pending: false, taskIds: {}, approvalIds: {}, activityCounts: {} },
  };
}

function upsert<T>(record: Record<string, T>, key: string, value: T): Record<string, T> {
  return { ...record, [key]: value };
}

/** An approval resolves on a confirming `task_state`/`activity` for its task
 * — never optimistically on a button press (rule 3). The task is paused
 * waiting on the user while `waiting_approval`, so any other task_state or
 * activity for the same task_id is by construction the resolving signal. */
function resolveApprovalsForTask(state: HaloState, taskId: string): HaloState {
  const pending = Object.keys(state.approvals).filter((id) => state.approvals[id].task_id === taskId);
  if (pending.length === 0) return state;
  const approvals = { ...state.approvals };
  for (const id of pending) delete approvals[id];
  return { ...state, approvals };
}

/** Deny and "stop this task" intentionally execute no tool, so they have no
 * activity frame to confirm resolution. Their terminal done/error frame is
 * the authoritative signal instead. Matching by conversation keeps one
 * conversation from clearing another conversation's card. */
function resolveApprovalsForConversation(state: HaloState, conversationId: string): HaloState {
  const pending = Object.keys(state.approvals).filter(
    (id) => state.approvals[id].conversation_id === conversationId,
  );
  if (pending.length === 0) return state;
  const approvals = { ...state.approvals };
  for (const id of pending) delete approvals[id];
  return { ...state, approvals };
}

// ---- Frame projection ----

export function applyFrame(state: HaloState, frame: IpcMessage): HaloState {
  switch (frame.type) {
    case "conversation_history_state": {
      const conv = getConversation(state, frame.conversation_id);
      if (conv.turns.length > 0) {
        return conv.historyLoaded ? state : replaceConversation(state, { ...conv, historyLoaded: true });
      }
      const turns: Turn[] = frame.turns.map((turn, index) =>
        turn.role === "user"
          ? { id: `${frame.id}:${index}`, role: "user", text: turn.text }
          : { id: `${frame.id}:${index}`, role: "assistant", status: "done", text: turn.text },
      );
      return replaceConversation(state, { ...conv, turns, historyLoaded: true });
    }

    case "token": {
      // Unknown conversation_id -> open a turn anyway; arrival order is
      // truth (the user_msg echo may be local-only).
      const conv = getConversation(state, frame.conversation_id);
      const open = openTurn(conv);
      // ponytail: rebuilding the whole string per token frame is O(n^2) over a
      // long reply. Leave it until a profiler complains — a chunk list joined
      // at render time is the upgrade path, and it costs the views a change.
      const turns: Turn[] = open
        ? conv.turns.map((t) => (t === open ? { ...t, text: t.text + frame.text } : t))
        : [...conv.turns, { id: frame.id, role: "assistant", status: "streaming", text: frame.text }];
      return replaceConversation(state, { ...conv, turns });
    }

    case "done": {
      const conv = getConversation(state, frame.conversation_id);
      const open = openTurn(conv);
      const next = open
        ? replaceConversation(state, {
            ...conv,
            turns: patchOpenTurn(
              conv.turns,
              open,
              frame.interrupted
                ? {
                    status: "interrupted",
                    taskId: frame.task_id,
                    note: "stopped · what should I do differently?",
                  }
                : { status: "done", taskId: frame.task_id },
            ),
          })
        : state;
      return resolveApprovalsForConversation(next, frame.conversation_id);
    }

    case "error": {
      let next = state;
      if (frame.operation_kind && frame.operation_id) {
        const key = operationCorrelationKey(frame.operation_kind, frame.operation_id);
        const approvals = frame.operation_kind === "approval_response"
          ? Object.fromEntries(Object.entries(state.approvals).filter(([id]) => id !== frame.operation_id))
          : state.approvals;
        next = { ...state, approvals, operationErrors: upsert(state.operationErrors, key, frame) };
      }
      if (!frame.conversation_id) {
        if (frame.operation_kind) return next;
        return { ...next, globalErrors: [...next.globalErrors, frame].slice(-5) };
      }
      const conv = getConversation(next, frame.conversation_id);
      const open = openTurn(conv);
      const error = { code: frame.code, message: frame.message, recoverable: frame.recoverable };
      const turns: Turn[] = open
        ? patchOpenTurn(conv.turns, open, { status: "error", error })
        : [...conv.turns, { id: frame.id, role: "assistant", status: "error", text: "", error }];
      // rule 8: a turn is never lost — flag the input for restore regardless
      // of whether a turn had started streaming yet.
      return resolveApprovalsForConversation(
        replaceConversation(next, { ...conv, turns, needsInputRestore: true }),
        frame.conversation_id,
      );
    }

    case "activity": {
      if (state.snapshot.pending) return reconcileSnapshotActivity(state, frame);
      const withActivity = pushActivity(state, frame);
      return resolveApprovalsForTask(withActivity, frame.task_id);
    }

    case "approval_request": {
      const next = { ...state, approvals: upsert(state.approvals, frame.approval_id, frame) };
      if (!state.snapshot.pending) return next;
      return {
        ...next,
        snapshot: {
          ...state.snapshot,
          approvalIds: { ...state.snapshot.approvalIds, [frame.approval_id]: true },
        },
      };
    }

    case "task_state": {
      // Unknown task_id -> create it; the reconnect snapshot may race deltas.
      let next = { ...state, tasks: upsert(state.tasks, frame.task_id, frame) };
      if (state.snapshot.pending) {
        next = {
          ...next,
          snapshot: {
            ...state.snapshot,
            taskIds: { ...state.snapshot.taskIds, [frame.task_id]: true },
          },
        };
      }
      return frame.state === "waiting_approval" ? next : resolveApprovalsForTask(next, frame.task_id);
    }

    case "stream_frame": {
      // Sandbox lane tile: keep only the latest frame per task, dropping any
      // that arrive out of order (never queue jpegs — plan Step 11 edge case).
      const prev = state.streams[frame.task_id];
      if (prev && frame.seq < prev.seq) return state;
      return { ...state, streams: upsert(state.streams, frame.task_id, frame) };
    }

    case "belief_state":
      return { ...state, beliefs: upsert(state.beliefs, frame.belief_id, frame) };

    case "belief_deleted": {
      const beliefs = { ...state.beliefs };
      delete beliefs[frame.belief_id];
      return { ...state, beliefs };
    }

    case "memory_history_state":
      return frame.complete
        ? { ...state, memoryHistoryLoaded: true }
        : {
            ...state,
            beliefs: Object.fromEntries(
              Object.entries(state.beliefs).filter(([, belief]) => belief.status === "active"),
            ),
            memoryHistoryLoaded: false,
          };

    case "skill_state":
      return { ...state, skills: upsert(state.skills, frame.skill_name, frame) };

    case "snapshot_complete":
      // Authoritative snapshot terminator (real Brain sends it last). spend_update
      // below is kept as a backstop: the mock never sends snapshot_complete, and a
      // Brain that omits it must still converge rather than wedge in snapshot mode
      // silently swallowing live activity that matches an earlier frame.
      return finishSnapshot(state);

    case "spend_update":
      return finishSnapshot({
        ...state,
        spend: {
          sessionUsd: frame.session_usd,
          monthUsd: frame.month_usd,
          // Optional token fields (C4): keep the prior value when a frame omits
          // them -- the snapshot carries session_tokens but no last_turn_tokens.
          sessionTokens: frame.session_tokens ?? state.spend.sessionTokens,
          lastTurnTokens: frame.last_turn_tokens ?? state.spend.lastTurnTokens,
        },
      });

    case "settings_state":
      return { ...state, settings: upsert(state.settings, frame.key, frame.status) };

    case "capabilities_state":
      return {
        ...state,
        capabilities: {
          voiceInput: frame.voice_input,
          taskControls: frame.task_controls,
          skillControls: frame.skill_controls,
          demoScenarios: frame.demo_scenarios,
        },
      };

    case "voice_state":
      return { ...state, voice: { ...state.voice, state: frame.state } };

    case "transcript": {
      // final -> the spoken turn solidifies into a real user message and the
      // ghost clears (glyph swap, no duplicate bubble); partials stay ghost.
      if (frame.final) {
        const conv = getConversation(state, frame.conversation_id);
        const turn: UserTurn = { id: frame.id, role: "user", text: frame.text, viaVoice: true };
        const withTurn = replaceConversation(state, { ...conv, turns: [...conv.turns, turn] });
        return { ...withTurn, voice: { ...withTurn.voice, transcript: null } };
      }
      return {
        ...state,
        voice: {
          ...state.voice,
          transcript: { text: frame.text, final: false, conversationId: frame.conversation_id },
        },
      };
    }

    default:
      // Frames the UI only ever sends (hello, user_msg, interrupt,
      // approval_response, ...) and hello_ack (consumed by useHaloConnection
      // before reaching the store) never arrive here in practice — no-op
      // keeps this projection total instead of throwing on the union.
      return state;
  }
}

// ---- Connection / non-frame events ----
// Not IPC frames — the WS-lifecycle and sidecar-process signals
// useHaloConnection and the Tauri "sidecar-state" event surface.

export type ConnectionEvent =
  | { type: "ws_open" }
  | { type: "authenticated" }
  | { type: "ws_closed" }
  | { type: "ws_unavailable" }
  | { type: "sidecar_state"; process: "brain" | "voice"; state: Exclude<SidecarStatus, "unknown"> };

export function applyConnectionEvent(state: HaloState, event: ConnectionEvent): HaloState {
  switch (event.type) {
    case "ws_open":
      return { ...state, connection: { ...state.connection, wsStatus: "connecting" } };

    case "authenticated":
      // The Brain sends a complete state snapshot immediately after hello_ack.
      // Start reconciliation here so ordinary live events are never mistaken
      // for snapshot backlog before a connection has authenticated.
      return {
        ...state,
        connection: { ...state.connection, wsStatus: "connected" },
        memoryHistoryLoaded: false,
        snapshot: { pending: true, taskIds: {}, approvalIds: {}, activityCounts: {} },
      };

    case "ws_closed": {
      // Close open streaming turns with an interrupted marker (a turn is
      // never visually stuck streaming); approvals are kept — they wait
      // forever until the reconnect snapshot reconciles them (D6).
      const conversations: Record<string, ConversationState> = {};
      for (const [id, conv] of Object.entries(state.conversations)) {
        const interrupted = conv.turns.some(
          (turn) => turn.role === "assistant" && turn.status === "streaming",
        );
        const next: ConversationState = interrupted
          ? {
              ...conv,
              turns: conv.turns.map((turn) =>
                turn.role === "assistant" && turn.status === "streaming"
                  ? { ...turn, status: "interrupted", note: "interrupted — connection lost" }
                  : turn,
              ),
              needsInputRestore: true,
            }
          : conv;
        conversations[id] = { ...next, historyLoaded: false };
      }
      return {
        ...state,
        conversations,
        memoryHistoryLoaded: false,
        connection: { ...state.connection, wsStatus: "reconnecting" },
        snapshot: { pending: true, taskIds: {}, approvalIds: {}, activityCounts: {} },
      };
    }

    case "ws_unavailable": {
      const disconnected = applyConnectionEvent(state, { type: "ws_closed" });
      return {
        ...disconnected,
        connection: { ...disconnected.connection, wsStatus: "unavailable" },
        snapshot: { pending: false, taskIds: {}, approvalIds: {}, activityCounts: {} },
      };
    }

    case "sidecar_state": {
      const key = event.process === "brain" ? "brainStatus" : "voiceStatus";
      return { ...state, connection: { ...state.connection, [key]: event.state } };
    }

    default:
      return state;
  }
}
