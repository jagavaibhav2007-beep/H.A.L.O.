// Exercises the pure conversation registry: titling heuristic, open-vs-recent
// transitions, unread bookkeeping, cap/eviction, persistence round-trip.
// No test framework (repo convention) — run via
// `npx tsx ui/src/state/conversations.selfcheck.ts`.
import {
  DEFAULT_TITLE,
  RECENT_CAP,
  closeConversation,
  createRegistry,
  deleteConversation,
  deserialize,
  markUnread,
  newConversation,
  renameConversation,
  serialize,
  setActive,
  titleFromMessage,
} from "./conversations.ts";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(`[conversations.selfcheck] FAILED: ${msg}`);
}

const title = (reg: ReturnType<typeof createRegistry>, id: string) =>
  reg.all.find((c) => c.id === id)?.title;

// ---- Scenario 1: only a started conversation survives close in Recent ----
{
  let reg = createRegistry("a", 1);
  reg = newConversation(reg, "b", 2);
  assert(reg.activeId === "b", "new conversation becomes active");
  assert(reg.open.join() === "a,b", "new tab appends to the strip");

  reg = titleFromMessage(reg, "b", "hi");
  reg = closeConversation(reg, "b", "fallback", 3);
  assert(reg.open.join() === "a", "closed tab leaves the strip");
  assert(reg.all.some((c) => c.id === "b"), "a conversation with a user message stays in Recent");
  assert(reg.activeId === "a", "closing the active tab activates a neighbour");

  reg = setActive(reg, "b", 4);
  assert(reg.open.join() === "a,b" && reg.activeId === "b", "reopening from Recent restores the tab");

  reg = deleteConversation(reg, "b", "fallback", 5);
  assert(!reg.all.some((c) => c.id === "b"), "delete removes the thread from Recent too");
}

// ---- Scenario 2: untouched tabs are disposable, including the last tab ----
{
  let reg = createRegistry("a", 1);
  reg = newConversation(reg, "b", 2);
  reg = closeConversation(reg, "b", "unused", 3);
  assert(!reg.all.some((c) => c.id === "b"), "closing an untouched tab does not save it in Recent");
  assert(reg.open.join() === "a", "the neighbouring tab remains open");

  reg = closeConversation(reg, "a", "fresh", 4);
  assert(reg.open.join() === "fresh", "last close opens the fallback conversation");
  assert(reg.activeId === "fresh", "and activates it — chat is never tab-less");
  assert(!reg.all.some((c) => c.id === "a"), "an untouched last tab is discarded rather than saved");
  assert(reg.all.length === 1, "only the fresh replacement remains");
}

// ---- Scenario 3: titling heuristic ----
{
  let reg = createRegistry("a", 1);
  reg = titleFromMessage(reg, "a", "hey");
  assert(title(reg, "a") === DEFAULT_TITLE, "a too-short first message does not title the thread");
  assert(reg.all.find((c) => c.id === "a")?.hasUserMessage === true, "a short sent message still marks the chat as started");

  reg = titleFromMessage(reg, "a", "  summarise the   quarterly report for me  ");
  assert(title(reg, "a") === "summarise the quarterly report for me", "substantive message titles it, whitespace collapsed");

  reg = titleFromMessage(reg, "a", "a completely different follow-up question");
  assert(title(reg, "a") === "summarise the quarterly report for me", "only the FIRST substantive message titles it");

  let long = createRegistry("b", 1);
  long = titleFromMessage(long, "b", "x".repeat(80));
  assert(title(long, "b")!.length === 40 && title(long, "b")!.endsWith("…"), "long titles truncate to 40 with an ellipsis");

  long = renameConversation(long, "b", "My thread");
  assert(title(long, "b") === "My thread", "manual rename wins");
  long = titleFromMessage(long, "b", "another substantive sentence here");
  assert(title(long, "b") === "My thread", "a manual rename is never overwritten by the heuristic");
}

// ---- Scenario 4: unread bookkeeping ----
{
  let reg = createRegistry("a", 1);
  reg = newConversation(reg, "b", 2); // b active
  reg = markUnread(reg, "a");
  assert(reg.all.find((c) => c.id === "a")!.unread === true, "a background thread gets an unread dot");
  reg = markUnread(reg, "b");
  assert(!reg.all.find((c) => c.id === "b")!.unread, "the active thread is never marked unread");
  reg = markUnread(reg, "ghost");
  assert(reg.all.length === 2, "an unknown conversation_id is ignored, not registered");
  reg = setActive(reg, "a", 3);
  assert(!reg.all.find((c) => c.id === "a")!.unread, "activating clears the dot");
}

// ---- Scenario 5: recent cap evicts the oldest ----
{
  let reg = createRegistry("c0", 0);
  for (let i = 1; i <= RECENT_CAP + 5; i += 1) reg = newConversation(reg, `c${i}`, i);
  assert(reg.all.length === RECENT_CAP, `recent list capped at ${RECENT_CAP}`);
  assert(!reg.all.some((c) => c.id === "c0"), "the oldest thread is evicted");
  assert(reg.open.every((id) => reg.all.some((c) => c.id === id)), "evicted threads never linger as open tabs");
}

// ---- Scenario 6: persistence round-trip ----
{
  let reg = createRegistry("a", 1);
  reg = newConversation(reg, "b", 2);
  reg = titleFromMessage(reg, "a", "hello");
  reg = renameConversation(reg, "a", "First thread");
  reg = markUnread(reg, "a");
  reg = closeConversation(reg, "a", "unused", 3);

  const back = deserialize(serialize(reg))!;
  assert(back.open.join() === reg.open.join(), "open tabs survive a round-trip");
  assert(back.activeId === reg.activeId, "active id survives a round-trip");
  assert(title(back, "a") === "First thread", "titles survive a round-trip");
  assert(back.all.find((c) => c.id === "a")?.hasUserMessage === true, "started state survives a round-trip");
  assert(!back.all.find((c) => c.id === "a")!.unread, "unread is per-session, never persisted");
  assert(back.all.every((c) => c.restored), "loaded threads are flagged restored (honest empty state)");

  assert(deserialize(null) === null, "no stored blob -> null");
  assert(deserialize("{not json") === null, "malformed blob -> null, not a crash");
  assert(deserialize('{"all":[],"open":[],"activeId":"x"}') === null, "empty blob -> null");
  const legacy = deserialize('{"all":[{"id":"old","title":"New chat","lastUsedAt":1}],"open":["old"],"activeId":"old"}')!;
  assert(legacy.all[0].hasUserMessage === true, "legacy saved chats are preserved during migration");
}

console.log("[conversations.selfcheck] OK — 6 scenarios passed.");
