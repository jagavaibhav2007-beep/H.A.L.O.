// Multi-conversation registry — pure logic (same pure/impure split as
// reducer.ts vs store.ts). This is UI-only state: which threads exist, which
// are open as tabs, which one is active. It is NOT projected from IPC frames
// (the Brain keys history by conversation_id and never tells us what the tab
// strip should look like), so it can't live in reducer.ts.
//
// The browser model: `open` is the tab strip; everything in `all` that isn't
// open is a "recent" thread reachable from the + menu. Closing a tab never
// deletes the thread — its history is still in the Brain's checkpoints.
//
// No React, no browser globals (callers pass ids and timestamps in) — see
// conversations.selfcheck.ts.

export const DEFAULT_TITLE = "New chat";
export const TITLE_MAX = 40;
/** Shorter than this and it's "hey" / "thanks" — not a title. Wait for the
 * next substantive message instead of pinning a meaningless label. */
export const TITLE_MIN_SOURCE = 15;
// ponytail: flat cap, oldest-used dropped first. A real "archive" view with
// search is the upgrade path if 50 ever feels small.
export const RECENT_CAP = 50;

export interface ConversationMeta {
  id: string;
  title: string;
  lastUsedAt: number;
  /** True only after the user submits at least one non-empty message. Drafts,
   * manual renames, and merely opening a tab do not make it durable. */
  hasUserMessage: boolean;
  /** Received token/done/error while not active. Transient — never persisted. */
  unread?: boolean;
  /** Loaded from a previous session, so the store holds no turns for it.
   * Drives the honest "earlier messages are on the Brain" empty state. */
  restored?: boolean;
}

export interface ConversationRegistry {
  all: ConversationMeta[]; // most-recently-used first
  open: string[]; // tab strip, left-to-right; creation/reopen order
  activeId: string;
}

function truncate(text: string): string {
  const clean = text.trim().replace(/\s+/g, " ");
  return clean.length > TITLE_MAX ? `${clean.slice(0, TITLE_MAX - 1)}…` : clean;
}

function touch(reg: ConversationRegistry, id: string, patch: Partial<ConversationMeta>): ConversationRegistry {
  return { ...reg, all: reg.all.map((c) => (c.id === id ? { ...c, ...patch } : c)) };
}

export function createRegistry(id: string, now: number): ConversationRegistry {
  return {
    all: [{ id, title: DEFAULT_TITLE, lastUsedAt: now, hasUserMessage: false }],
    open: [id],
    activeId: id,
  };
}

export function newConversation(reg: ConversationRegistry, id: string, now: number): ConversationRegistry {
  const meta: ConversationMeta = { id, title: DEFAULT_TITLE, lastUsedAt: now, hasUserMessage: false };
  const all = [meta, ...reg.all].slice(0, RECENT_CAP);
  const kept = new Set(all.map((c) => c.id));
  return { all, open: [...reg.open.filter((i) => kept.has(i)), id], activeId: id };
}

/** Activate an existing thread, reopening its tab if it was only in Recent. */
export function setActive(reg: ConversationRegistry, id: string, now: number): ConversationRegistry {
  if (!reg.all.some((c) => c.id === id)) return reg;
  const next = touch(reg, id, { lastUsedAt: now, unread: false });
  return {
    ...next,
    open: next.open.includes(id) ? next.open : [...next.open, id],
    activeId: id,
  };
}

/** Close the tab. Started threads stay in `all` (Recent); untouched tabs are
 * discarded. `fallbackId` is only used when this was the last open tab — a
 * chat view with no conversation has nowhere to type. */
export function closeConversation(
  reg: ConversationRegistry,
  id: string,
  fallbackId: string,
  now: number,
): ConversationRegistry {
  const meta = reg.all.find((c) => c.id === id);
  const base =
    meta && !meta.hasUserMessage
      ? { ...reg, all: reg.all.filter((c) => c.id !== id) }
      : reg;
  const open = reg.open.filter((i) => i !== id);
  if (open.length === 0) return newConversation({ ...base, open }, fallbackId, now);
  if (reg.activeId !== id) return { ...base, open };
  // Prefer the tab that slid into this one's slot, else the new last one.
  const wasAt = reg.open.indexOf(id);
  return setActive({ ...base, open }, open[Math.min(wasAt, open.length - 1)], now);
}

/** Forget the thread entirely (tab strip + Recent). The Brain's checkpoints
 * are untouched — this is a UI-side forget, not a server-side delete.
 * ponytail: a real thread delete needs a contract message; add one when the
 * Brain grows a checkpoint-delete path. */
export function deleteConversation(
  reg: ConversationRegistry,
  id: string,
  fallbackId: string,
  now: number,
): ConversationRegistry {
  const closed = closeConversation(reg, id, fallbackId, now);
  const all = closed.all.filter((c) => c.id !== id);
  if (all.length === 0) return createRegistry(fallbackId, now);
  return { ...closed, all };
}

export function renameConversation(reg: ConversationRegistry, id: string, title: string): ConversationRegistry {
  const clean = truncate(title);
  return clean ? touch(reg, id, { title: clean }) : reg;
}

/** Title from the first substantive user message. No-op once the thread has a
 * real title (a manual rename is never overwritten) or when the text is too
 * short to mean anything. */
export function titleFromMessage(reg: ConversationRegistry, id: string, text: string): ConversationRegistry {
  const meta = reg.all.find((c) => c.id === id);
  if (!meta) return reg;
  const started = meta.hasUserMessage
    ? reg
    : touch(reg, id, { hasUserMessage: true, restored: false });
  if (meta.title !== DEFAULT_TITLE) return started;
  if (text.trim().length < TITLE_MIN_SOURCE) return started;
  return touch(started, id, { title: truncate(text), restored: false });
}

/** A frame landed for `id`: dot it if the user isn't looking at it. Unknown
 * ids are ignored — a thread this window never opened has no tab to dot. */
export function markUnread(reg: ConversationRegistry, id: string): ConversationRegistry {
  if (id === reg.activeId) return reg;
  const meta = reg.all.find((c) => c.id === id);
  if (!meta || meta.unread) return reg;
  return touch(reg, id, { unread: true });
}

// ---- Persistence (localStorage) ----
// Only `{id, title, lastUsedAt, hasUserMessage}` + which tabs were open + the
// active id. `unread` is per-session by definition; `restored` is derived on
// load.

export const STORAGE_KEY = "halo.conversations.v1";

interface Persisted {
  all: { id: string; title: string; lastUsedAt: number; hasUserMessage?: boolean }[];
  open: string[];
  activeId: string;
}

export function serialize(reg: ConversationRegistry): string {
  const payload: Persisted = {
    all: reg.all.map(({ id, title, lastUsedAt, hasUserMessage }) => ({
      id,
      title,
      lastUsedAt,
      hasUserMessage,
    })),
    open: reg.open,
    activeId: reg.activeId,
  };
  return JSON.stringify(payload);
}

/** Rebuild from a stored blob. Anything malformed -> null, so the caller
 * falls back to a fresh registry instead of booting into a broken tab strip. */
export function deserialize(raw: string | null): ConversationRegistry | null {
  if (!raw) return null;
  try {
    const p = JSON.parse(raw) as Persisted;
    const all = (p.all ?? [])
      .filter((c) => typeof c?.id === "string" && typeof c.title === "string")
      .map((c) => ({
        ...c,
        lastUsedAt: Number(c.lastUsedAt) || 0,
        // v1 blobs created before this field existed may include real short
        // conversations still titled "New chat". Preserve them rather than
        // guessing and deleting history.
        hasUserMessage: typeof c.hasUserMessage === "boolean" ? c.hasUserMessage : true,
        restored: true,
      }));
    if (all.length === 0) return null;
    const ids = new Set(all.map((c) => c.id));
    const open = (p.open ?? []).filter((i) => ids.has(i));
    if (open.length === 0) return null;
    return { all, open, activeId: ids.has(p.activeId) ? p.activeId : open[0] };
  } catch {
    return null;
  }
}
