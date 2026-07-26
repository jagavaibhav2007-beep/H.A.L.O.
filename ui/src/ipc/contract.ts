// IPC contract for the Halo WebSocket protocol.
// Source of truth: shared/ipc-contract.json (directions, fields, types, and enums).
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
  role?: "ui" | "voice"; // absent -> "ui" (full stream); Voice opts into its subset
  contract_version?: string; // major.minor; absent -> pre-versioning client, treated as compatible
}

// The protocol version this build speaks. Bump the major on any breaking
// envelope/field change; a major mismatch across the WS is refused loudly on
// both sides. Hand-mirrored with brain/brain/ipc/contract.py CONTRACT_VERSION
// and shared/ipc-contract.json "version" — check_contract_sync.py compares them.
export const CONTRACT_VERSION = "1.0";
export const contractMajor = (v: string | undefined): number | null => {
  if (typeof v !== "string") return null;
  const major = Number.parseInt(v.split(".")[0], 10);
  return Number.isNaN(major) ? null : major;
};

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

export interface UndoMsg extends IpcEnvelope {
  type: "undo";
  undo_token: string;
}

// ---- Outbound from Brain (to UI; Voice receives the subset it speaks) ----

export interface HelloAckMsg extends IpcEnvelope {
  type: "hello_ack";
  contract_version?: string; // the Brain's protocol version; absent -> old Brain, treated as compatible
}

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
  tier?: 1 | 2 | 3;
  lane?: 1 | 2 | 3;
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
  summary?: string;
  destructive?: boolean;
  // Which conversation is suspended on this card. Optional (older frames and
  // some mock paths omit it) but load-bearing once more than one conversation
  // can be open: "Stop this task" sends `interrupt`, which is keyed by
  // conversation_id — without this the UI would interrupt whichever thread the
  // user happens to be VIEWING, not the one that asked. Approve/deny don't
  // need it (the Brain resolves those by approval_id).
  conversation_id?: string;
}

export interface DoneMsg extends IpcEnvelope {
  type: "done";
  conversation_id: string;
  task_id?: string;
}

export type OperationKind = "undo" | "memory_edit" | "approval_response" | "task_op" | "lane_pin" | "mic" | "skill_op";

interface ErrorMsgBase extends IpcEnvelope {
  type: "error";
  code: string;
  message: string;
  recoverable: boolean;
  conversation_id?: string;
}
export type ErrorMsg = ErrorMsgBase & (
  | { operation_kind: OperationKind; operation_id: string }
  | { operation_kind?: never; operation_id?: never }
);
export const operationCorrelationKey = (kind: OperationKind, id: string) => `${kind}:${id}`;

export interface TaskStateMsg extends IpcEnvelope {
  type: "task_state";
  task_id: string;
  state: "running" | "paused" | "waiting_approval" | "done" | "failed";
  lane: 1 | 2 | 3;
  title?: string;
  step?: number;
  steps_total?: number;
  step_label?: string;
  reason?: string;
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
  session_tokens?: number;
  last_turn_tokens?: number;
}

export interface SettingsStateMsg extends IpcEnvelope {
  type: "settings_state";
  key: string;
  status: "set" | "missing" | "invalid" | "unverified";
}

export interface BeliefStateMsg extends IpcEnvelope {
  type: "belief_state";
  belief_id: string;
  text: string;
  kind: "preference" | "project" | "workflow" | "decision" | "lesson";
  provenance: "user" | "inferred";
  salience: number;
  status: "active" | "archived" | "superseded";
  superseded_by?: string;
  used_at?: string;
}

export interface SkillStateMsg extends IpcEnvelope {
  type: "skill_state";
  skill_name: string;
  origin: "auto" | "user";
  kind: "skill" | "playbook";
  uses: number;
  success_rate: number;
  status: "active" | "paused" | "retired";
  born_at: string;
  reason?: string;
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
  | UndoMsg
  | HelloAckMsg
  | TokenMsg
  | ActivityMsg
  | ApprovalRequestMsg
  | DoneMsg
  | ErrorMsg
  | TaskStateMsg
  | StreamFrameMsg
  | VoiceStateMsg
  | TranscriptMsg
  | SpendUpdateMsg
  | SettingsStateMsg
  | BeliefStateMsg
  | SkillStateMsg;

type MsgType = IpcMessage["type"];
export type IpcDirection = "inbound" | "outbound";
export type RuntimeFieldType = "string" | "boolean" | "integer" | "number" | "object" | "json";
export interface RuntimeFieldSpec { type: RuntimeFieldType; enum?: readonly unknown[] }
export interface RuntimeMessageSpec {
  direction: IpcDirection;
  required: readonly string[];
  fields: Readonly<Record<string, RuntimeFieldSpec>>;
}

const field = (type: RuntimeFieldType, values?: readonly unknown[]): RuntimeFieldSpec =>
  values === undefined ? { type } : { type, enum: values };
const message = (
  direction: IpcDirection,
  required: readonly string[],
  fields: Readonly<Record<string, RuntimeFieldSpec>>,
): RuntimeMessageSpec => ({ direction, required, fields });

const S = "string", B = "boolean", I = "integer", N = "number", O = "object", J = "json";
const IN = "inbound", OUT = "outbound";
const LANES = [1, 2, 3];

export const CONTRACT_SPEC = {
  envelope: { required: ["type", "id", "ts"], fields: {
    type: field(S), id: field(S), ts: field(S),
  } },
  messages: {
    hello: message(IN, ["token"], { token: field(S), role: field(S, ["ui", "voice"]), contract_version: field(S) }),
    user_msg: message(IN, ["text", "conversation_id", "source"], {
      text: field(S), conversation_id: field(S), source: field(S, ["ui", "voice"]),
    }),
    interrupt: message(IN, ["conversation_id"], { conversation_id: field(S) }),
    approval_response: message(IN, ["reply_to", "decision"], {
      reply_to: field(S), decision: field(S, ["approve", "deny", "edit"]), edited_args: field(O),
    }),
    memory_edit: message(IN, ["belief_id", "op"], {
      belief_id: field(S), op: field(S, ["edit", "delete", "restore"]), text: field(S),
    }),
    skill_op: message(IN, ["skill_name", "op"], {
      skill_name: field(S), op: field(S, ["trial", "disable", "restore", "delete"]),
    }),
    lane_pin: message(IN, ["task_id", "lane"], { task_id: field(S), lane: field(I, LANES) }),
    task_op: message(IN, ["op"], { task_id: field(S), op: field(S, ["pause", "resume", "stop"]) }),
    mic: message(IN, ["op"], { op: field(S, ["mute", "unmute"]) }),
    settings_update: message(IN, ["key", "value"], { key: field(S), value: field(J) }),
    undo: message(IN, ["undo_token"], { undo_token: field(S) }),
    hello_ack: message(OUT, [], { contract_version: field(S) }),
    token: message(OUT, ["text", "conversation_id"], { text: field(S), conversation_id: field(S) }),
    activity: message(OUT, ["text", "narrate", "task_id", "undoable"], {
      text: field(S), narrate: field(B), task_id: field(S), undoable: field(B), undo_token: field(S),
      tier: field(I, LANES), lane: field(I, LANES),
    }),
    approval_request: message(OUT, ["approval_id", "tool", "args_redacted", "tier", "task_id"], {
      approval_id: field(S), tool: field(S), args_redacted: field(O), tier: field(I, LANES),
      task_id: field(S), summary: field(S), destructive: field(B), conversation_id: field(S),
    }),
    done: message(OUT, ["conversation_id"], { conversation_id: field(S), task_id: field(S) }),
    error: message(OUT, ["code", "message", "recoverable"], {
      code: field(S), message: field(S), recoverable: field(B), conversation_id: field(S),
      operation_kind: field(S, ["undo", "memory_edit", "approval_response", "task_op", "lane_pin", "mic", "skill_op"]),
      operation_id: field(S),
    }),
    task_state: message(OUT, ["task_id", "state", "lane"], {
      task_id: field(S), state: field(S, ["running", "paused", "waiting_approval", "done", "failed"]),
      lane: field(I, LANES), title: field(S), step: field(I), steps_total: field(I),
      step_label: field(S), reason: field(S),
    }),
    stream_frame: message(OUT, ["task_id", "jpeg_b64", "seq"], {
      task_id: field(S), jpeg_b64: field(S), seq: field(I),
    }),
    voice_state: message(OUT, ["state"], {
      state: field(S, ["idle", "wake", "listening", "thinking", "speaking", "muted"]),
    }),
    transcript: message(OUT, ["text", "final", "conversation_id"], {
      text: field(S), final: field(B), conversation_id: field(S),
    }),
    spend_update: message(OUT, ["session_usd", "month_usd"], {
      session_usd: field(N), month_usd: field(N),
      session_tokens: field(I), last_turn_tokens: field(I),
    }),
    settings_state: message(OUT, ["key", "status"], {
      key: field(S), status: field(S, ["set", "missing", "invalid", "unverified"]),
    }),
    belief_state: message(OUT, ["belief_id", "text", "kind", "provenance", "salience", "status"], {
      belief_id: field(S), text: field(S),
      kind: field(S, ["preference", "project", "workflow", "decision", "lesson"]),
      provenance: field(S, ["user", "inferred"]), salience: field(N),
      status: field(S, ["active", "archived", "superseded"]), superseded_by: field(S), used_at: field(S),
    }),
    skill_state: message(OUT, ["skill_name", "origin", "kind", "uses", "success_rate", "status", "born_at"], {
      skill_name: field(S), origin: field(S, ["auto", "user"]), kind: field(S, ["skill", "playbook"]),
      uses: field(I), success_rate: field(N), status: field(S, ["active", "paused", "retired"]),
      born_at: field(S), reason: field(S),
    }),
  } satisfies Record<MsgType, RuntimeMessageSpec>,
};

const MESSAGES: Record<string, RuntimeMessageSpec> = CONTRACT_SPEC.messages;
const KNOWN_TYPES = new Set(Object.keys(MESSAGES));
export const REQUIRED_FIELDS: Record<MsgType, readonly string[]> = Object.fromEntries(
  Object.entries(MESSAGES).map(([name, spec]) => [name, spec.required]),
) as Record<MsgType, readonly string[]>;
export const DIRECTIONS: Record<MsgType, IpcDirection> = Object.fromEntries(
  Object.entries(MESSAGES).map(([name, spec]) => [name, spec.direction]),
) as Record<MsgType, IpcDirection>;

function isJsonValue(value: unknown): boolean {
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  if (typeof value === "object") return Object.entries(value).every(([, item]) => isJsonValue(item));
  return false;
}

function valueMatches(value: unknown, spec: RuntimeFieldSpec): boolean {
  const matches = spec.type === "string" ? typeof value === "string"
    : spec.type === "boolean" ? typeof value === "boolean"
    : spec.type === "integer" ? typeof value === "number" && Number.isSafeInteger(value)
    : spec.type === "number" ? typeof value === "number" && Number.isFinite(value)
    : spec.type === "object" ? typeof value === "object" && value !== null && !Array.isArray(value) && isJsonValue(value)
    : spec.type === "json" ? isJsonValue(value)
    : false;
  return matches && (spec.enum === undefined || spec.enum.includes(value));
}

/** Validate an arbitrary decoded-JSON frame against the contract. Throws on
 * an unknown `type` or a missing required field — never returns a partial
 * message.
 *
 * `tolerantFields` (off by default): skip the unknown-extra-field rejection so
 * a frame from a newer minor-version peer that added a field to a KNOWN type
 * still parses (the extra rides along unread — the reducer only touches
 * declared fields). Declared fields stay strictly typed either way. Only the
 * UI's inbound onmessage opts in; outbound sends and both selfchecks stay
 * strict so the trust boundary and drift checks don't loosen. */
export function parseIpcMessage(
  raw: unknown,
  expectedDirection?: IpcDirection,
  opts?: { tolerantFields?: boolean },
): IpcMessage {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("ipc: frame is not an object");
  }
  const obj = raw as Record<string, unknown>;
  const { type } = obj;
  if (typeof type !== "string" || !KNOWN_TYPES.has(type)) {
    throw new Error(`ipc: unknown message type ${JSON.stringify(type)}`);
  }
  const messageSpec = MESSAGES[type];
  if (expectedDirection !== undefined && messageSpec.direction !== expectedDirection) {
    throw new Error(`ipc: "${type}" is ${messageSpec.direction}, expected ${expectedDirection}`);
  }
  for (const field of CONTRACT_SPEC.envelope.required) {
    if (!(field in obj)) throw new Error(`ipc: envelope missing required field "${field}"`);
  }
  for (const field of messageSpec.required) {
    if (!(field in obj)) {
      throw new Error(`ipc: "${type}" missing required field "${field}"`);
    }
  }

  const allowed = new Set([...Object.keys(CONTRACT_SPEC.envelope.fields), ...Object.keys(messageSpec.fields)]);
  if (!opts?.tolerantFields) {
    for (const field of Object.keys(obj)) {
      if (!allowed.has(field)) throw new Error(`ipc: "${type}" has unknown field "${field}"`);
    }
  }
  for (const [field, spec] of Object.entries(CONTRACT_SPEC.envelope.fields)) {
    if (!valueMatches(obj[field], spec)) throw new Error(`ipc: envelope field "${field}" has an invalid value`);
  }
  // NOTE: these optional-field checks test `obj.field !== undefined`, not
  // `"field" in obj` — a sender built via object-literal spread of an unset
  // function param (e.g. `{ ..., belief_id, op, text }` with `text`
  // undefined) leaves `text` as an *own property* equal to undefined, so
  // `"text" in obj` is true even though nothing was actually supplied. That
  // false positive rejected every real delete/restore memory_edit outbound
  // (see mem/Bugs.md, "Delete gets stuck on Deleting... forever").
  const required = new Set(messageSpec.required);
  for (const [field, spec] of Object.entries(messageSpec.fields)) {
    if (!(field in obj)) continue;
    // Object-literal outbound builders retain known optional keys with an
    // undefined value. JSON.stringify omits those keys on the wire, so treat
    // them like absent optional fields while rejecting undefined when required.
    if (obj[field] === undefined && !required.has(field)) continue;
    if (!valueMatches(obj[field], spec)) {
      throw new Error(`ipc: "${type}" field "${field}" has an invalid value`);
    }
  }
  if (type === "error" && (("operation_kind" in obj) !== ("operation_id" in obj))) {
    throw new Error('ipc: "error" operation_kind and operation_id must appear together');
  }
  return obj as unknown as IpcMessage;
}
