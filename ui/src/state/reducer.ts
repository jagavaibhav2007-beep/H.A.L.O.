// Phase 1 Step 4 — UI event store: pure projection of IPC frames into
// renderable state (D5, D6, D7; phase-1-plan.md "Step 4 — UI event store").
// No React, no WebSocket, no browser globals — importable and testable
// standalone (see reducer.selfcheck.ts). All impurity (zustand, subscriptions)
// lives in store.ts.

import type {
  ActivityMsg,
  ApprovalRequestMsg,
  BeliefStateMsg,
  IpcMessage,
  SkillStateMsg,
  StreamFrameMsg,
  TaskStateMsg,
  VoiceStateMsg,
} from "../ipc/contract";

// ---- Slice types ----

export type WsStatus = "connecting" | "connected" | "reconnecting";
export type SidecarStatus = "unknown" | "starting" | "running" | "restarting" | "error";

// Step 7 -- the orb's nine visual states (ui_ux/01-companion-orb.md "State
// language"). Priority high->low: approval > error > task > voice, where
// voice itself is 5 sub-states + idle. One state at a time -- the selector
// below decides, never CSS.
export type OrbState =
  | "idle"
  | "wake"
  | "listening"
  | "thinking"
  | "speaking"
  | "task"
  | "approval"
  | "error"
  | "muted";

// Two distinct signals that must never be conflated (Phase-0 rule, D5):
// WS-connected+authenticated (drives chat input / reconnect indicator) vs
// sidecar process health (drives the separate "Brain failed to start" banner).
export interface ConnectionState {
  wsStatus: WsStatus;
  brainStatus: SidecarStatus;
  voiceStatus: SidecarStatus;
}

// The user's own messages never come back as frames (the mock doesn't echo,
// and Phase 2's real Brain won't either) — the reducer records them locally
// via appendUserTurn at send time, so one ordered `turns` array holds the
// whole conversation in arrival order (D7). `role` is the discriminant.
export interface UserTurn {
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

export interface ConversationState {
  conversationId: string;
  turns: Turn[]; // full history, arrival order (D7) — never sorted by ts
  needsInputRestore: boolean; // rule 8: error/disconnect restores the user's text
}

export interface VoiceState {
  state: VoiceStateMsg["state"];
  transcript: { text: string; final: boolean; conversationId: string } | null;
}

export interface SpendState {
  sessionUsd: number;
  monthUsd: number;
}

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
}

export const ACTIVITY_CAP = 10_000;

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
  spend: { sessionUsd: 0, monthUsd: 0 },
};

/** Priority selector, the one piece of UI logic subtle enough to regress
 * silently (phase-1-plan.md Step 7) -- unit-cased in reducer.selfcheck.ts. */
export function deriveOrbState(state: HaloState): OrbState {
  if (Object.keys(state.approvals).length > 0) return "approval";
  // ponytail: brainStatus==="error" is the only orb error source this step
  // (ruling 4). "Persist until seen" (clear only once the workspace is
  // opened) needs a store field the workspace error-banner work will add
  // later -- for now this clears the instant brainStatus leaves "error".
  if (state.connection.brainStatus === "error") return "error";
  if (Object.values(state.tasks).some((t) => t.state === "running")) return "task";
  // voice.state is already a single value, not several simultaneous signals,
  // and every VoiceStateMsg state is a valid OrbState — direct passthrough.
  return state.voice.state;
}

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

/** The last turn, if it's an assistant turn still streaming — undefined otherwise. */
function openTurn(conv: ConversationState): AssistantTurn | undefined {
  const last = conv.turns[conv.turns.length - 1];
  return last?.role === "assistant" && last.status === "streaming" ? last : undefined;
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

function replaceConversation(state: HaloState, conv: ConversationState): HaloState {
  return { ...state, conversations: { ...state.conversations, [conv.conversationId]: conv } };
}

function pushActivity(state: HaloState, activity: ActivityMsg): HaloState {
  const next = [...state.activities, activity];
  if (next.length > ACTIVITY_CAP) next.splice(0, next.length - ACTIVITY_CAP); // drop oldest
  return { ...state, activities: next };
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

// ---- Frame projection ----

export function applyFrame(state: HaloState, frame: IpcMessage): HaloState {
  switch (frame.type) {
    case "token": {
      // Unknown conversation_id -> open a turn anyway; arrival order is
      // truth (the user_msg echo may be local-only).
      const conv = getConversation(state, frame.conversation_id);
      const open = openTurn(conv);
      const turns: Turn[] = open
        ? conv.turns.map((t) => (t === open ? { ...t, text: t.text + frame.text } : t))
        : [...conv.turns, { id: frame.id, role: "assistant", status: "streaming", text: frame.text }];
      return replaceConversation(state, { ...conv, turns });
    }

    case "done": {
      const conv = getConversation(state, frame.conversation_id);
      const open = openTurn(conv);
      if (!open) return state; // nothing streaming to close
      const turns = patchOpenTurn(conv.turns, open, { status: "done", taskId: frame.task_id });
      return replaceConversation(state, { ...conv, turns });
    }

    case "error": {
      if (!frame.conversation_id) return state;
      const conv = getConversation(state, frame.conversation_id);
      const open = openTurn(conv);
      const error = { code: frame.code, message: frame.message, recoverable: frame.recoverable };
      const turns = open ? patchOpenTurn(conv.turns, open, { status: "error", error }) : conv.turns;
      // rule 8: a turn is never lost — flag the input for restore regardless
      // of whether a turn had started streaming yet.
      return replaceConversation(state, { ...conv, turns, needsInputRestore: true });
    }

    case "activity": {
      const withActivity = pushActivity(state, frame);
      return resolveApprovalsForTask(withActivity, frame.task_id);
    }

    case "approval_request":
      return { ...state, approvals: upsert(state.approvals, frame.approval_id, frame) };

    case "task_state": {
      // Unknown task_id -> create it; the reconnect snapshot may race deltas.
      const next = { ...state, tasks: upsert(state.tasks, frame.task_id, frame) };
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

    case "skill_state":
      return { ...state, skills: upsert(state.skills, frame.skill_name, frame) };

    case "spend_update":
      return { ...state, spend: { sessionUsd: frame.session_usd, monthUsd: frame.month_usd } };

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
  | { type: "sidecar_state"; process: "brain" | "voice"; state: Exclude<SidecarStatus, "unknown"> };

export function applyConnectionEvent(state: HaloState, event: ConnectionEvent): HaloState {
  switch (event.type) {
    case "ws_open":
      return { ...state, connection: { ...state.connection, wsStatus: "connecting" } };

    case "authenticated":
      return { ...state, connection: { ...state.connection, wsStatus: "connected" } };

    case "ws_closed": {
      // Close open streaming turns with an interrupted marker (a turn is
      // never visually stuck streaming); approvals are kept — they wait
      // forever until the reconnect snapshot reconciles them (D6).
      const conversations: Record<string, ConversationState> = {};
      for (const [id, conv] of Object.entries(state.conversations)) {
        const open = openTurn(conv);
        conversations[id] = open
          ? {
              ...conv,
              turns: patchOpenTurn(conv.turns, open, {
                status: "interrupted",
                note: "interrupted — connection lost",
              }),
            }
          : conv;
      }
      return { ...state, conversations, connection: { ...state.connection, wsStatus: "reconnecting" } };
    }

    case "sidecar_state": {
      const key = event.process === "brain" ? "brainStatus" : "voiceStatus";
      return { ...state, connection: { ...state.connection, [key]: event.state } };
    }

    default:
      return state;
  }
}
