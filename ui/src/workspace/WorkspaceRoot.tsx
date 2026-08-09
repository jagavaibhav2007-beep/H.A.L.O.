// Workspace window shell: orb-anchored show/hide animation, sidebar, status
// strip, view routing, deep-jump, Ctrl+K focus, and the implemented Phase 1
// panels. Spec: phase-1-plan.md and ui_ux/02-workspace.md.

import { useEffect, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { GlassPanel } from "../components/GlassPanel";
import { Sidebar } from "./Sidebar";
import { StatusStrip } from "./StatusStrip";
import { ChatView } from "../chat/ChatView";
import { ActivityFeed } from "../activity/ActivityFeed";
import { TasksView } from "../tasks/TasksView";
import { MemoryView } from "../memory/MemoryView";
import { SkillsView } from "../skills/SkillsView";
import { SettingsView } from "../settings/SettingsView";
import { ApprovalOverlay } from "../approvals/ApprovalCard";
import { useStoreConnection } from "../state/useStoreConnection";
import {
  useHaloStore,
  selectActiveView,
  selectActiveConversationId,
  selectBrainStatus,
  selectGlobalErrors,
  selectOperationErrors,
  selectVoiceStatus,
} from "../state/store";
import type { ActiveView } from "../state/store";
import "./WorkspaceRoot.css";

interface WorkspaceAnchor {
  x: number;
  y: number;
}

// The chat view's textarea carries this id so the workspace's Ctrl+K focus
// (below) lands on the real input.
const CHAT_INPUT_ID = "halo-chat-input";

const VIEWS: { id: ActiveView; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "tasks", label: "Tasks" },
  { id: "activity", label: "Activity" },
  { id: "memory", label: "Memory" },
  { id: "skills", label: "Skills" },
  { id: "settings", label: "Settings" },
];

export function WorkspaceRoot() {
  const shellRef = useRef<HTMLDivElement>(null);
  const [anim, setAnim] = useState<"idle" | "enter" | "leave">("idle");
  const [origin, setOrigin] = useState("center");

  const activeView = useHaloStore(selectActiveView);
  const setActiveView = useHaloStore((s) => s.setActiveView);
  const brainStatus = useHaloStore(selectBrainStatus);
  const voiceStatus = useHaloStore(selectVoiceStatus);

  // The workspace window owns its own live connection + store instance; the
  // shared hook feeds inbound frames into the store and forwards the two
  // Phase-0 connection signals. connState is still read below (status strip).
  const {
    connState,
    sendTaskOp,
    sendUserMsg,
    sendConversationHistoryQuery,
    sendUndo,
    sendApprovalResponse,
    sendInterrupt,
    sendLanePin,
    sendMemoryEdit,
    sendMemoryQuery,
    sendSkillOp,
    sendSettingsUpdate,
    sendMic,
  } = useStoreConnection();

  // Which conversation the chat view and any conversation-scoped send targets.
  // Owned by the store (UI decision), never by the transport hook.
  const conversationId = useHaloStore(selectActiveConversationId);
  const globalErrors = useHaloStore(selectGlobalErrors);
  const operationErrors = useHaloStore(selectOperationErrors);
  const dismissError = useHaloStore((state) => state.dismissError);
  const workspaceErrors = [
    ...globalErrors,
    ...Object.values(operationErrors).filter(
      (error) => error.operation_kind === "approval_response" && !error.conversation_id,
    ),
  ];
  const workspaceError = workspaceErrors[workspaceErrors.length - 1];

  // Ctrl+K focuses the chat input from anywhere — not Tauri-specific, so it
  // works in the D9 browser fallback too.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (!e.ctrlKey || e.key.toLowerCase() !== "k") return;
      e.preventDefault();
      setActiveView("chat");
      // setActiveView is batched (React 18/19 auto-batching on a raw window
      // listener) — the chat view is still `hidden` until after this handler
      // returns, and .focus() on a display:none ancestor is a no-op. Defer
      // one frame past the commit.
      requestAnimationFrame(() => document.getElementById(CHAT_INPUT_ID)?.focus());
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [setActiveView]);

  // Conversation tab shortcuts (browser conventions): Ctrl+T new, Ctrl+W close
  // the current one, Ctrl+Tab / Ctrl+Shift+Tab cycle. All require Ctrl, so
  // they never swallow a keystroke while the user is typing, and none collide
  // with the existing Ctrl+K (focus input) or bare Escape (collapse) handlers.
  // ponytail: preventDefault is enough in the Tauri webview; in the D9 browser
  // fallback Chrome still owns Ctrl+T/Ctrl+W at the OS level and will win —
  // acceptable, the native window is the real target.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (!e.ctrlKey) return;
      const store = useHaloStore.getState();
      const { open, activeId } = store.chats;
      const key = e.key.toLowerCase();
      if (key === "t") {
        e.preventDefault();
        store.setActiveView("chat");
        store.newConversation();
      } else if (key === "w") {
        e.preventDefault();
        store.closeConversation(activeId);
      } else if (e.key === "Tab" && open.length > 1) {
        e.preventDefault();
        const i = open.indexOf(activeId);
        store.setActiveView("chat");
        store.setActiveConversation(open[(i + (e.shiftKey ? -1 : 1) + open.length) % open.length]);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Rust emits `workspace-anchor` (the orb's screen position) right before
  // showing this window — anchor the scale+fade transform-origin there for
  // spatial continuity (D3), then play the entrance.
  useEffect(() => {
    if (!isTauri()) return;
    const unlistenPromise = listen<WorkspaceAnchor>("workspace-anchor", async (event) => {
      const win = getCurrentWindow();
      const [winPos, scale] = await Promise.all([win.outerPosition(), win.scaleFactor()]);
      const originX = (event.payload.x - winPos.x) / scale;
      const originY = (event.payload.y - winPos.y) / scale;
      setOrigin(`${originX}px ${originY}px`);
      setAnim("enter");
      // Two rAFs: let the browser paint the "enter" (scaled-down/faded) state
      // once before switching to "idle", so the transition actually animates.
      requestAnimationFrame(() => requestAnimationFrame(() => setAnim("idle")));
    });
    return () => {
      void unlistenPromise.then((f) => f());
    };
  }, []);

  // Esc collapses back to the orb — never quits (rule: closing != quitting).
  useEffect(() => {
    if (!isTauri()) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      const shell = shellRef.current;
      if (!shell) {
        void getCurrentWindow().hide();
        return;
      }
      // tokens.css keeps reduced-motion transitions at ~0ms (not exactly 0)
      // specifically so transitionend still fires — wait for it instead of
      // guessing a duration, so this respects reduced motion for free.
      // Step 6 added transitioning descendants (sidebar items, chips, the
      // stop button) — transitionend bubbles, so filter to the shell's own
      // transition or an unrelated descendant transition ending during the
      // leave animation would hide the window early.
      function onTransitionEnd(e: TransitionEvent) {
        if (e.target !== shell) return;
        shell!.removeEventListener("transitionend", onTransitionEnd);
        void getCurrentWindow().hide();
        setAnim("idle");
      }
      shell.addEventListener("transitionend", onTransitionEnd);
      setAnim("leave");
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="workspace-root">
      <div ref={shellRef} className="workspace-shell" data-anim={anim} style={{ transformOrigin: origin }}>
        <GlassPanel elevation="panel" className="workspace-content">
          <Sidebar />
          <main className="workspace-main">
            {brainStatus === "error" && (
              <div className="process-health process-health-error" role="alert">
                Halo’s Brain failed to start. Chat and actions are unavailable until it recovers.
              </div>
            )}
            {brainStatus === "restarting" && (
              <div className="process-health" role="status">
                Halo’s Brain is restarting. Your draft is safe; sending will resume after reconnection.
              </div>
            )}
            {connState === "unavailable" && (
              <div className="process-health" role="status">
                Browser connection unavailable. Start Halo with ./dev.ps1 -Browser to continue.
              </div>
            )}
            {voiceStatus === "error" && (
              <div className="process-health process-health-voice" role="status">
                Voice failed to start. You can keep using typed chat.
              </div>
            )}
            {workspaceError && (
              <div className="workspace-error" role="alert">
                <span>{workspaceError.message}</span>
                <button type="button" onClick={() => dismissError(workspaceError.id)}>
                  Dismiss
                </button>
              </div>
            )}
            <StatusStrip sendTaskOp={sendTaskOp} />
            <div className="workspace-views">
              {VIEWS.map((v) => (
                <section
                  key={v.id}
                  hidden={activeView !== v.id}
                  className="workspace-view"
                  aria-labelledby={`workspace-${v.id}-heading`}
                >
                  <h1 id={`workspace-${v.id}-heading`} className="sr-only">
                    {v.label}
                  </h1>
                  {v.id === "chat" ? (
                    <ChatView
                      conversationId={conversationId}
                      connState={connState}
                      sendUserMsg={sendUserMsg}
                      sendConversationHistoryQuery={sendConversationHistoryQuery}
                      sendInterrupt={sendInterrupt}
                      sendMic={sendMic}
                      inputId={CHAT_INPUT_ID}
                    />
                  ) : v.id === "activity" ? (
                    <ActivityFeed sendUndo={sendUndo} />
                  ) : v.id === "tasks" ? (
                    <TasksView sendTaskOp={sendTaskOp} sendLanePin={sendLanePin} />
                  ) : v.id === "memory" ? (
                    <MemoryView
                      active={activeView === "memory"}
                      sendMemoryEdit={sendMemoryEdit}
                      sendMemoryQuery={sendMemoryQuery}
                    />
                  ) : v.id === "skills" ? (
                    <SkillsView sendSkillOp={sendSkillOp} />
                  ) : (
                    <SettingsView sendSettingsUpdate={sendSettingsUpdate} />
                  )}
                </section>
              ))}
              <ApprovalOverlay
                conversationId={conversationId}
                sendApprovalResponse={sendApprovalResponse}
                sendInterrupt={sendInterrupt}
              />
            </div>
          </main>
        </GlassPanel>
      </div>
    </div>
  );
}
