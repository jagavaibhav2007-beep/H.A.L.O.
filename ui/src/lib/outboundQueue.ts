// Policy for the outbound queue that useHaloConnection holds while the Brain
// is down: how much may sit in it, and what is still worth replaying to a
// Brain that has restarted. The transport half (send/flush) is ipc/queue.ts.
//
// Pure and runtime-dependency-free (type-only imports) so ipc/queue.selfcheck.ts
// can exercise it under plain Node — see mem/Gotchas.md on selfcheck imports.
// Both helpers mutate the array in place: it is the same array the live flush
// shifts from, so it can never be replaced by a new one.

import type { IpcMessage } from "../ipc/contract";

// ponytail: flat cap, oldest dropped first — same discipline as the activity
// ring buffer. The Brain's restart ladder tops out at 30s, so anything past
// this many frames means something is wrong that replaying won't fix.
export const OUTBOUND_CAP = 100;

/** Trim to the newest OUTBOUND_CAP entries. Returns how many were dropped. */
export function capQueue(queue: unknown[]): number {
  const excess = queue.length - OUTBOUND_CAP;
  if (excess <= 0) return 0;
  queue.splice(0, excess);
  return excess;
}

/** Drop everything but `user_msg` once the Brain has restarted (detected by a
 * fresh port in session.json). A queued `approval_response`/`interrupt`/
 * `task_op`/`lane_pin`/... names an id that died with the old process, so
 * replaying it is at best a no-op; `user_msg` is checkpoint-keyed and resumes
 * the same thread. Deliberately conservative: a few ids (belief_id,
 * undo_token) are DB-backed and would in fact survive, but nothing in the
 * frame says which, and re-issuing a control action beats acting on a stale
 * one. Returns how many were dropped. */
export function dropStaleControlFrames<T extends IpcMessage>(queue: T[]): number {
  const replayable = queue.filter((msg) => msg.type === "user_msg");
  const dropped = queue.length - replayable.length;
  if (dropped > 0) queue.splice(0, queue.length, ...replayable);
  return dropped;
}
