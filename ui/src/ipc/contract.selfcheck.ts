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

console.log("[contract.selfcheck.ts] self-check OK");
