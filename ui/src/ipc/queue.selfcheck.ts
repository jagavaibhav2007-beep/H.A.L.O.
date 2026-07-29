import { flushQueuedMessages, sendOrQueue } from "./queue.ts";
import { OUTBOUND_CAP, capQueue, dropStaleControlFrames } from "../lib/outboundQueue.ts";
import type { InterruptMsg, UserMsg } from "./contract.ts";

function message(text: string): UserMsg {
  return {
    type: "user_msg",
    id: text,
    ts: "2026-07-10T00:00:00Z",
    text,
    conversation_id: "conv-1",
    source: "ui",
  };
}

const queue = [message("first"), message("second"), message("third")];
const sent: string[] = [];
const socket = {
  send(raw: string) {
    const msg = JSON.parse(raw) as UserMsg;
    if (msg.text === "second") throw new Error("socket closed during flush");
    sent.push(msg.text);
  },
};

try {
  flushQueuedMessages(socket, queue);
  throw new Error("expected the interrupted flush to throw");
} catch (error) {
  if (!(error instanceof Error) || error.message !== "socket closed during flush") throw error;
}

if (sent.join(",") !== "first" || queue.map((msg) => msg.text).join(",") !== "second,third") {
  throw new Error("flush discarded messages that were not sent");
}

console.log("[queue.selfcheck.ts] interrupted flush preserves unsent messages: OK");

const waiting: UserMsg[] = [];
const preAuthSent: string[] = [];
sendOrQueue({ send: (raw) => preAuthSent.push(raw) }, false, message("waiting"), waiting);
if (preAuthSent.length || waiting.length !== 1) {
  throw new Error("open-but-unauthenticated socket sent a queued message");
}

console.log("[queue.selfcheck.ts] messages wait for authentication acknowledgement: OK");

// ---- Queue policy (../lib/outboundQueue.ts) ----

function interrupt(id: string): InterruptMsg {
  return { type: "interrupt", id, ts: "2026-07-28T00:00:00Z", conversation_id: "conv-1" };
}

const overflowing: UserMsg[] = [];
for (let i = 0; i < OUTBOUND_CAP + 5; i += 1) overflowing.push(message(`msg-${i}`));
if (capQueue(overflowing) !== 5 || overflowing.length !== OUTBOUND_CAP) {
  throw new Error("queue was not capped to OUTBOUND_CAP");
}
if (overflowing[0].text !== "msg-5" || overflowing[OUTBOUND_CAP - 1].text !== `msg-${OUTBOUND_CAP + 4}`) {
  throw new Error("cap dropped the newest frames instead of the oldest");
}
if (capQueue(overflowing) !== 0) throw new Error("cap dropped frames from an already-capped queue");

console.log("[queue.selfcheck.ts] outbound queue is capped, oldest dropped first: OK");

// A restart gives the Brain a new port; anything naming an id from the dead
// process must not be replayed to the new one.
const pending = [message("first"), interrupt("i-1"), message("second"), interrupt("i-2")];
if (dropStaleControlFrames(pending) !== 2) throw new Error("stale control frames were not dropped");
if (pending.map((msg) => (msg.type === "user_msg" ? msg.text : msg.type)).join(",") !== "first,second") {
  throw new Error("reconnect pruning dropped or reordered replayable user messages");
}
if (dropStaleControlFrames(pending) !== 0) throw new Error("pruning a user_msg-only queue dropped frames");

console.log("[queue.selfcheck.ts] a reconnect to a new port keeps user_msg and drops control frames: OK");
