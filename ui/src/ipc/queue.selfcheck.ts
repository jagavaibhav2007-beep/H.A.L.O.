import { flushQueuedMessages, sendOrQueue } from "./queue.ts";
import type { UserMsg } from "./contract.ts";

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
