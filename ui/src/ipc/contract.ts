// IPC contract for the Halo WebSocket protocol.
// Source of truth: shared/ipc-contract.json (message names + required fields).
// Mirrored by hand in brain/brain/ipc/contract.py — keep both in sync;
// run `python shared/check_contract_sync.py` after editing either.
// Full contract prose: systemdesign/11-ipc-contract.md

export interface IpcEnvelope {
  type: string;
  id: string;
  ts: string;
}

// ---- Inbound to Brain (from UI or Voice) ----

export interface HelloMsg extends IpcEnvelope {
  type: "hello";
  token: string;
}

export interface UserMsg extends IpcEnvelope {
  type: "user_msg";
  text: string;
  conversation_id: string;
  source: "ui" | "voice";
}

export interface InterruptMsg extends IpcEnvelope {
  type: "interrupt";
  conversation_id: string;
}

export interface ApprovalResponseMsg extends IpcEnvelope {
  type: "approval_response";
  reply_to: string;
  decision: "approve" | "deny" | "edit";
  edited_args?: unknown;
}

export interface MemoryEditMsg extends IpcEnvelope {
  type: "memory_edit";
  belief_id: string;
  op: "edit" | "delete" | "restore";
  text?: string;
}

export interface SkillOpMsg extends IpcEnvelope {
  type: "skill_op";
  skill_name: string;
  op: "trial" | "disable" | "restore" | "delete";
}

export interface LanePinMsg extends IpcEnvelope {
  type: "lane_pin";
  task_id: string;
  lane: 1 | 2 | 3;
}

export interface TaskOpMsg extends IpcEnvelope {
  type: "task_op";
  task_id?: string;
  op: "pause" | "resume" | "stop";
}

export interface MicMsg extends IpcEnvelope {
  type: "mic";
  op: "mute" | "unmute";
}

export interface SettingsUpdateMsg extends IpcEnvelope {
  type: "settings_update";
  key: string;
  value: unknown;
}

// ---- Outbound from Brain (to UI; Voice receives the subset it speaks) ----

export interface TokenMsg extends IpcEnvelope {
  type: "token";
  text: string;
  conversation_id: string;
}

export interface ActivityMsg extends IpcEnvelope {
  type: "activity";
  text: string;
  narrate: boolean;
  task_id: string;
  undoable: boolean;
  undo_token?: string;
}

export interface ApprovalRequestMsg extends IpcEnvelope {
  type: "approval_request";
  // The approval's own domain id (distinct from the envelope message `id`).
  // approval_response.reply_to references this value.
  approval_id: string;
  tool: string;
  args_redacted: unknown;
  tier: 1 | 2 | 3;
  task_id: string;
}

export interface DoneMsg extends IpcEnvelope {
  type: "done";
  conversation_id: string;
  task_id?: string;
}

export interface ErrorMsg extends IpcEnvelope {
  type: "error";
  code: string;
  message: string;
  recoverable: boolean;
  conversation_id?: string;
}

export interface TaskStateMsg extends IpcEnvelope {
  type: "task_state";
  task_id: string;
  state: "running" | "paused" | "waiting_approval" | "done" | "failed";
  lane: 1 | 2 | 3;
}

export interface StreamFrameMsg extends IpcEnvelope {
  type: "stream_frame";
  task_id: string;
  jpeg_b64: string;
  seq: number;
}

export interface VoiceStateMsg extends IpcEnvelope {
  type: "voice_state";
  state: "idle" | "wake" | "listening" | "thinking" | "speaking" | "muted";
}

export interface TranscriptMsg extends IpcEnvelope {
  type: "transcript";
  text: string;
  final: boolean;
  conversation_id: string;
}

export interface SpendUpdateMsg extends IpcEnvelope {
  type: "spend_update";
  session_usd: number;
  month_usd: number;
}

export type IpcMessage =
  | HelloMsg
  | UserMsg
  | InterruptMsg
  | ApprovalResponseMsg
  | MemoryEditMsg
  | SkillOpMsg
  | LanePinMsg
  | TaskOpMsg
  | MicMsg
  | SettingsUpdateMsg
  | TokenMsg
  | ActivityMsg
  | ApprovalRequestMsg
  | DoneMsg
  | ErrorMsg
  | TaskStateMsg
  | StreamFrameMsg
  | VoiceStateMsg
  | TranscriptMsg
  | SpendUpdateMsg;

type MsgType = IpcMessage["type"];

// Required payload fields per type (envelope's type/id/ts are checked separately).
// Keep in lockstep with shared/ipc-contract.json "required" lists.
export const REQUIRED_FIELDS: Record<MsgType, readonly string[]> = {
  hello: ["token"],
  user_msg: ["text", "conversation_id", "source"],
  interrupt: ["conversation_id"],
  approval_response: ["reply_to", "decision"],
  memory_edit: ["belief_id", "op"],
  skill_op: ["skill_name", "op"],
  lane_pin: ["task_id", "lane"],
  task_op: ["op"],
  mic: ["op"],
  settings_update: ["key", "value"],
  token: ["text", "conversation_id"],
  activity: ["text", "narrate", "task_id", "undoable"],
  approval_request: ["approval_id", "tool", "args_redacted", "tier", "task_id"],
  done: ["conversation_id"],
  error: ["code", "message", "recoverable"],
  task_state: ["task_id", "state", "lane"],
  stream_frame: ["task_id", "jpeg_b64", "seq"],
  voice_state: ["state"],
  transcript: ["text", "final", "conversation_id"],
  spend_update: ["session_usd", "month_usd"],
};

const KNOWN_TYPES = new Set(Object.keys(REQUIRED_FIELDS));

/** Validate an arbitrary decoded-JSON frame against the contract. Throws on
 * an unknown `type` or a missing required field — never returns a partial
 * message. */
export function parseIpcMessage(raw: unknown): IpcMessage {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("ipc: frame is not an object");
  }
  const obj = raw as Record<string, unknown>;
  const { type, id, ts } = obj;
  if (typeof type !== "string" || !KNOWN_TYPES.has(type)) {
    throw new Error(`ipc: unknown message type ${JSON.stringify(type)}`);
  }
  if (typeof id !== "string" || typeof ts !== "string") {
    throw new Error("ipc: envelope missing id/ts");
  }
  for (const field of REQUIRED_FIELDS[type as MsgType]) {
    if (!(field in obj)) {
      throw new Error(`ipc: "${type}" missing required field "${field}"`);
    }
  }
  return obj as unknown as IpcMessage;
}
