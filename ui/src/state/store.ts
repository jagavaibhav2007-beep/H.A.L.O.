// Phase 1 Step 4 — zustand wrapper around the pure reducer (D5). The only
// impure piece of the event store; reducer.ts stays framework-free.
// Components subscribe via the selector helpers below so, e.g., a `token`
// frame re-renders only the streaming bubble, not the whole tree.

import { create } from "zustand";
import type { IpcMessage } from "../ipc/contract";
import { applyConnectionEvent, applyFrame, initialState } from "./reducer";
import type { ConnectionEvent, HaloState } from "./reducer";

interface HaloStore extends HaloState {
  applyFrame: (frame: IpcMessage) => void;
  applyConnectionEvent: (event: ConnectionEvent) => void;
}

export const useHaloStore = create<HaloStore>((set) => ({
  ...initialState,
  applyFrame: (frame) => set((state) => applyFrame(state, frame)),
  applyConnectionEvent: (event) => set((state) => applyConnectionEvent(state, event)),
}));

// Per-slice selectors — use as `useHaloStore(selectTasks)` (or the curried
// per-id ones) for a narrow subscription instead of the whole store.
export const selectConnection = (s: HaloStore) => s.connection;
export const selectConversations = (s: HaloStore) => s.conversations;
export const selectConversation = (conversationId: string) => (s: HaloStore) =>
  s.conversations[conversationId];
export const selectActivities = (s: HaloStore) => s.activities;
export const selectTasks = (s: HaloStore) => s.tasks;
export const selectApprovals = (s: HaloStore) => s.approvals;
export const selectBeliefs = (s: HaloStore) => s.beliefs;
export const selectSkills = (s: HaloStore) => s.skills;
export const selectVoice = (s: HaloStore) => s.voice;
export const selectSpend = (s: HaloStore) => s.spend;
