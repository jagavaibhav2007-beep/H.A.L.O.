// Round-trips a user_msg and confirms bad frames are rejected.
// Run via `node ui/src/ipc/contract.selfcheck.ts` (Node runs .ts natively;
// no test framework needed for this one-shot check).
import { parseIpcMessage, type UserMsg } from "./contract.ts";

const sample: UserMsg = {
  type: "user_msg",
  id: "11111111-1111-1111-1111-111111111111",
  ts: "2026-07-10T00:00:00Z",
  text: "hello",
  conversation_id: "conv-1",
  source: "ui",
};

const parsed = parseIpcMessage(sample);
if (JSON.stringify(parsed) !== JSON.stringify(sample)) {
  throw new Error("round-trip changed the message");
}

function expectRejected(raw: unknown, why: string) {
  try {
    parseIpcMessage(raw);
  } catch {
    return;
  }
  throw new Error(`expected rejection: ${why}`);
}

expectRejected({ type: "not_a_real_type", id: "x", ts: "x" }, "unknown type");
expectRejected(
  { type: "user_msg", id: "x", ts: "x", text: "hi" },
  "missing required fields",
);
expectRejected(
  { ...sample, conversation_id: [] },
  "invalid Phase 0 field type",
);
expectRejected(
  { ...sample, source: "other" },
  "invalid user_msg source",
);
expectRejected(
  { type: "approval_response", id: "x", ts: "x", reply_to: [], decision: "approve" },
  "invalid approval reply_to",
);
expectRejected(
  { type: "interrupt", id: "x", ts: "x", conversation_id: [] },
  "invalid interrupt conversation_id",
);
expectRejected(
  { type: "undo", id: "x", ts: "x", undo_token: [] },
  "invalid undo token",
);
expectRejected(
  { type: "task_op", id: "x", ts: "x", op: "restart" },
  "invalid task operation",
);
expectRejected(
  { type: "memory_edit", id: "x", ts: "x", belief_id: [], op: "delete" },
  "invalid belief id",
);
expectRejected(
  { type: "memory_edit", id: "x", ts: "x", belief_id: "belief", op: "erase" },
  "invalid memory operation",
);
expectRejected(
  { type: "skill_op", id: "x", ts: "x", skill_name: [], op: "disable" },
  "invalid skill name",
);
expectRejected(
  { type: "skill_op", id: "x", ts: "x", skill_name: "skill", op: "enable" },
  "invalid skill operation",
);
expectRejected(
  { type: "lane_pin", id: "x", ts: "x", task_id: "task", lane: 4 },
  "invalid lane",
);
expectRejected(
  { type: "lane_pin", id: "x", ts: "x", task_id: "task", lane: [] },
  "invalid non-scalar lane",
);
expectRejected(
  { type: "task_op", id: "x", ts: "x", task_id: [], op: "stop" },
  "invalid optional task id",
);
expectRejected(
  { type: "mic", id: "x", ts: "x", op: "explode" },
  "invalid mic operation",
);

function expectAccepted(raw: unknown, why: string) {
  try {
    parseIpcMessage(raw);
  } catch (e) {
    throw new Error(`expected acceptance (${why}): ${e}`);
  }
}

// Regression: useHaloConnection.ts builds outbound messages via object-literal
// spread of an optional param, e.g. `{ ..., belief_id, op, text }` where `text`
// is an unpassed function argument. That leaves an *own property* `text:
// undefined` on the object — `"text" in obj` is true for it even though no
// caller supplied a value, so a naive `"text" in obj && typeof obj.text !==
// "string"` check rejects a perfectly valid delete/restore memory_edit (and
// would do the same to task_op's optional task_id). This is the exact bug
// behind "Delete gets stuck on Deleting... forever" — the message never left
// the client, so no confirming belief_state ever arrived.
expectAccepted(
  { type: "memory_edit", id: "x", ts: "x", belief_id: "belief-1", op: "delete", text: undefined },
  "memory_edit delete with an explicit-but-undefined text key",
);
expectAccepted(
  { type: "memory_edit", id: "x", ts: "x", belief_id: "belief-1", op: "restore", text: undefined },
  "memory_edit restore with an explicit-but-undefined text key",
);
expectAccepted(
  { type: "task_op", id: "x", ts: "x", op: "stop", task_id: undefined },
  "task_op with an explicit-but-undefined task_id key",
);

console.log("[contract.selfcheck.ts] self-check OK");
