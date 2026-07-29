// Phase 1 Step 4 — zustand wrapper around the pure reducer (D5). The only
// impure piece of the event store; reducer.ts stays framework-free.
// Components subscribe via the selector helpers below so, e.g., a `token`
// frame re-renders only the streaming bubble, not the whole tree.

import { create } from "zustand";
import type { IpcMessage } from "../ipc/contract";
import { appendUserTurn, beginUserRequest, applyConnectionEvent, applyFrame, initialState } from "./reducer";
import type { ConnectionEvent, HaloState } from "./reducer";
import * as chats from "./conversations";
import type { ConversationRegistry } from "./conversations";

// UI navigation state (Step 6, ui_ux/02-workspace.md) — not IPC-derived, so
// it lives here as plain store state rather than in the pure reducer.
export type ActiveView = "chat" | "tasks" | "activity" | "memory" | "skills" | "settings";

// Conversation registry (multi-chat): UI-only, same reasoning as activeView —
// no frame tells us what the tab strip should look like. Pure logic lives in
// conversations.ts; this file only holds it and persists it.
//
// TWO WEBVIEWS, ONE localStorage KEY: the orb and workspace boot separate
// store instances with no shared JS heap, so both writing this key would let a
// stale copy clobber a fresh one. Only the workspace has chat UI, so only it
// ever calls the mutating actions below — and those are the ONLY writers.
// `markUnread` (which does run in both windows, off applyFrame) deliberately
// does not persist: `unread` is per-session state, so the orb never writes.
// Both wrapped: localStorage throws outright in private mode / over quota, and
// is a non-conforming stub under the jsdom test env — neither is a reason to
// fail to boot. Worst case the tab strip is session-only.
function loadChats(): ConversationRegistry {
  try {
    const stored = chats.deserialize(localStorage.getItem(chats.STORAGE_KEY));
    if (stored) return stored;
  } catch {
    /* fall through to a fresh registry */
  }
  return chats.createRegistry(crypto.randomUUID(), Date.now());
}

function saveChats(reg: ConversationRegistry): ConversationRegistry {
  try {
    localStorage.setItem(chats.STORAGE_KEY, chats.serialize(reg));
  } catch {
    /* tabs still work for this session, just not the next */
  }
  return reg;
}

/** Persist a new registry AND drop the projected turns of any thread it no
 * longer lists. Every other slice is bounded (activities ring buffer, errors
 * .slice(-5), tasks/approvals/streams reconciled per snapshot); without this,
 * a deleted thread, a closed-untouched tab, and every RECENT_CAP eviction
 * leaves its full `turns` array resident for the life of an always-on session.
 *
 * Only the three actions that can REMOVE an id use this — rename/setActive/
 * titleFromMessage stay on plain saveChats. titleFromMessage runs on every
 * send, and a conversation the registry never listed (a voice-initiated
 * thread, say — markUnread tolerates those by design) must not be swept away
 * by an unrelated keystroke in another tab. */
function commitChats(state: HaloState, reg: ConversationRegistry) {
  const known = new Set(reg.all.map((c) => c.id));
  const conversations = Object.fromEntries(
    Object.entries(state.conversations).filter(([id]) => known.has(id)),
  );
  return { chats: saveChats(reg), conversations };
}

interface HaloStore extends HaloState {
  applyFrame: (frame: IpcMessage) => void;
  applyConnectionEvent: (event: ConnectionEvent) => void;
  // Chat (Step 8): record the user's own outgoing message as a turn, and
  // clear the input-restore flag once the view has consumed it (rule 8).
  appendUserTurn: (conversationId: string, text: string, id: string) => void;
  appendLocalUserTurn: (conversationId: string, text: string, id: string) => void;
  acknowledgeInputRestore: (conversationId: string) => void;
  dismissError: (id: string) => void;
  activeView: ActiveView;
  setActiveView: (view: ActiveView) => void;
  chats: ConversationRegistry;
  newConversation: () => void;
  setActiveConversation: (id: string) => void;
  closeConversation: (id: string) => void;
  deleteConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  /** Title from the first substantive user message — called at send time,
   * alongside appendUserTurn (a user_msg is never echoed back as a frame). */
  titleFromMessage: (id: string, text: string) => void;
}

export const useHaloStore = create<HaloStore>((set) => ({
  ...initialState,
  activeView: "chat",
  chats: loadChats(),
  applyFrame: (frame) =>
    set((state) => {
      const next = applyFrame(state, frame);
      // A reply landing in a thread the user isn't looking at earns a dot.
      // Deliberately not persisted — see the two-webviews note above.
      const cid =
        frame.type === "token" || frame.type === "done" || frame.type === "error"
          ? frame.conversation_id
          : undefined;
      return cid ? { ...next, chats: chats.markUnread(state.chats, cid) } : next;
    }),
  applyConnectionEvent: (event) => set((state) => applyConnectionEvent(state, event)),
  appendUserTurn: (conversationId, text, id) => set((state) => beginUserRequest(state, conversationId, text, id)),
  appendLocalUserTurn: (conversationId, text, id) =>
    set((state) => appendUserTurn(state, conversationId, text, id)),
  acknowledgeInputRestore: (conversationId) =>
    set((state) => {
      const conv = state.conversations[conversationId];
      if (!conv?.needsInputRestore) return state;
      return {
        conversations: { ...state.conversations, [conversationId]: { ...conv, needsInputRestore: false } },
      };
    }),
  dismissError: (id) =>
    set((state) => ({
      globalErrors: state.globalErrors.filter((error) => error.id !== id),
      operationErrors: Object.fromEntries(
        Object.entries(state.operationErrors).filter(([, error]) => error.id !== id),
      ),
    })),
  setActiveView: (view) => set({ activeView: view }),
  newConversation: () =>
    set((s) => commitChats(s, chats.newConversation(s.chats, crypto.randomUUID(), Date.now()))),
  setActiveConversation: (id) => set((s) => ({ chats: saveChats(chats.setActive(s.chats, id, Date.now())) })),
  closeConversation: (id) =>
    set((s) => commitChats(s, chats.closeConversation(s.chats, id, crypto.randomUUID(), Date.now()))),
  deleteConversation: (id) =>
    set((s) => commitChats(s, chats.deleteConversation(s.chats, id, crypto.randomUUID(), Date.now()))),
  renameConversation: (id, title) =>
    set((s) => ({ chats: saveChats(chats.renameConversation(s.chats, id, title)) })),
  titleFromMessage: (id, text) =>
    set((s) => ({ chats: saveChats(chats.titleFromMessage(s.chats, id, text)) })),
}));

// Per-slice selectors — use as `useHaloStore(selectTasks)` (or the curried
// per-id ones) for a narrow subscription instead of the whole store.
export const selectConversation = (conversationId: string) => (s: HaloStore) =>
  s.conversations[conversationId];
export const selectActivities = (s: HaloStore) => s.activities;
export const selectTasks = (s: HaloStore) => s.tasks;
export const selectStream = (taskId: string) => (s: HaloStore) => s.streams[taskId];
export const selectApprovals = (s: HaloStore) => s.approvals;
export const selectBeliefs = (s: HaloStore) => s.beliefs;
export const selectOperationErrors = (s: HaloStore) => s.operationErrors;
export const selectGlobalErrors = (s: HaloStore) => s.globalErrors;
export const selectMemoryHistoryLoaded = (s: HaloStore) => s.memoryHistoryLoaded;
export const selectSkills = (s: HaloStore) => s.skills;
export const selectVoice = (s: HaloStore) => s.voice;
export const selectSpend = (s: HaloStore) => s.spend;
export const selectCapabilities = (s: HaloStore) => s.capabilities;
export const selectActiveView = (s: HaloStore) => s.activeView;
export const selectChats = (s: HaloStore) => s.chats;
export const selectActiveConversationId = (s: HaloStore) => s.chats.activeId;
export const selectBrainStatus = (s: HaloStore) => s.connection.brainStatus;
export const selectVoiceStatus = (s: HaloStore) => s.connection.voiceStatus;
export const selectPendingApprovalCount = (s: HaloStore) => Object.keys(s.approvals).length;
export const selectRunningTask = (s: HaloStore) =>
  Object.values(s.tasks).find((t) => t.state === "running");
