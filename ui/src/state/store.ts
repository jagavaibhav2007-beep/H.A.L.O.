// Phase 1 Step 4 — zustand wrapper around the pure reducer (D5). The only
// impure piece of the event store; reducer.ts stays framework-free.
// Components subscribe via the selector helpers below so, e.g., a `token`
// frame re-renders only the streaming bubble, not the whole tree.

import { create } from "zustand";
import type { IpcMessage } from "../ipc/contract";
import { appendUserTurn, applyConnectionEvent, applyFrame, initialState } from "./reducer";
import type { ConnectionEvent, HaloState } from "./reducer";

// UI navigation state (Step 6, ui_ux/02-workspace.md) — not IPC-derived, so
// it lives here as plain store state rather than in the pure reducer.
export type ActiveView = "chat" | "tasks" | "activity" | "memory" | "skills" | "settings";

interface HaloStore extends HaloState {
  applyFrame: (frame: IpcMessage) => void;
  applyConnectionEvent: (event: ConnectionEvent) => void;
  // Chat (Step 8): record the user's own outgoing message as a turn, and
  // clear the input-restore flag once the view has consumed it (rule 8).
  appendUserTurn: (conversationId: string, text: string, id: string) => void;
  acknowledgeInputRestore: (conversationId: string) => void;
  activeView: ActiveView;
  setActiveView: (view: ActiveView) => void;
}

export const useHaloStore = create<HaloStore>((set) => ({
  ...initialState,
  activeView: "chat",
  applyFrame: (frame) => set((state) => applyFrame(state, frame)),
  applyConnectionEvent: (event) => set((state) => applyConnectionEvent(state, event)),
  appendUserTurn: (conversationId, text, id) => set((state) => appendUserTurn(state, conversationId, text, id)),
  acknowledgeInputRestore: (conversationId) =>
    set((state) => {
      const conv = state.conversations[conversationId];
      if (!conv?.needsInputRestore) return state;
      return {
        conversations: { ...state.conversations, [conversationId]: { ...conv, needsInputRestore: false } },
      };
    }),
  setActiveView: (view) => set({ activeView: view }),
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
export const selectSkills = (s: HaloStore) => s.skills;
export const selectVoice = (s: HaloStore) => s.voice;
export const selectSpend = (s: HaloStore) => s.spend;
export const selectActiveView = (s: HaloStore) => s.activeView;
export const selectBrainStatus = (s: HaloStore) => s.connection.brainStatus;
export const selectPendingApprovalCount = (s: HaloStore) => Object.keys(s.approvals).length;
export const selectRunningTask = (s: HaloStore) =>
  Object.values(s.tasks).find((t) => t.state === "running");
