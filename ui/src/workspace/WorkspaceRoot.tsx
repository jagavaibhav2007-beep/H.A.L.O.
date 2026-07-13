// Phase 1 Step 5/6 — the workspace window's content: Step 5's show/hide
// animation anchored to the orb (Esc-collapses) plus Step 6's shell —
// sidebar, status strip, view routing, deep-jump and Ctrl+K plumbing. Real
// panels land in Steps 8-14; every view mounts the same placeholder for now.
// Spec: phase-1-plan.md Steps 5-6, ui_ux/02-workspace.md.

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  isPermissionGranted,
  onAction,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import { GlassPanel } from "../components/GlassPanel";
import { Sidebar } from "./Sidebar";
import { StatusStrip } from "./StatusStrip";
import { ChatView } from "../chat/ChatView";
import { ActivityFeed } from "../activity/ActivityFeed";
import { TasksView } from "../tasks/TasksView";
import { MemoryView } from "../memory/MemoryView";
import { ApprovalOverlay } from "../approvals/ApprovalCard";
import { useHaloConnection } from "../ipc/useHaloConnection";
import type { IpcMessage } from "../ipc/contract";
import { useHaloStore, selectActiveView, selectApprovals, selectFocusTarget } from "../state/store";
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

// Chat is real as of Step 8; the other five panels land in Steps 9-14. Until
// then they mount this placeholder so switching views already exercises the
// mounted-hidden routing those panels rely on for scroll/state preservation.
function ViewPlaceholder({ name }: { name: string }) {
  return <div className="view-placeholder">{name}</div>;
}

export function WorkspaceRoot() {
  const shellRef = useRef<HTMLDivElement>(null);
  const [anim, setAnim] = useState<"idle" | "enter" | "leave">("idle");
  const [origin, setOrigin] = useState("center");

  const activeView = useHaloStore(selectActiveView);
  const setActiveView = useHaloStore((s) => s.setActiveView);
  const focusTarget = useHaloStore(selectFocusTarget);
  const clearFocusTarget = useHaloStore((s) => s.clearFocusTarget);

  // The workspace window owns the live connection; it feeds every inbound
  // frame straight into the event store (Step 4).
  const onMessage = useCallback((frame: IpcMessage) => {
    useHaloStore.getState().applyFrame(frame);
  }, []);
  const {
    connState,
    sidecarError,
    sendTaskOp,
    sendUserMsg,
    sendUndo,
    sendApprovalResponse,
    sendInterrupt,
    sendLanePin,
    sendMemoryEdit,
    conversationId,
  } = useHaloConnection(onMessage);

  // Forward the two Phase-0 signals into the store's connection slice,
  // kept distinct per the "never conflate WS state and sidecar health" rule.
  useEffect(() => {
    const event =
      connState === "connected"
        ? ({ type: "authenticated" } as const)
        : connState === "reconnecting"
          ? ({ type: "ws_closed" } as const)
          : ({ type: "ws_open" } as const);
    useHaloStore.getState().applyConnectionEvent(event);
  }, [connState]);

  useEffect(() => {
    if (!sidecarError) return;
    useHaloStore.getState().applyConnectionEvent({ type: "sidecar_state", process: "brain", state: "error" });
  }, [sidecarError]);

  // Away flow (Step 10): a pending approval that arrives while this window is
  // hidden fires a Windows toast; clicking it opens the workspace (Rust
  // `show_workspace`), where the card is already waiting as an overlay. Only
  // the workspace window toasts (it alone knows its own visibility) so the orb
  // window doesn't double-fire. onAction click delivery is best-effort on
  // Windows desktop; if it doesn't land the orb's amber badge is still the way
  // back — see mem/Gotchas.md.
  const approvals = useHaloStore(selectApprovals);
  const toastedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;
    void (async () => {
      const visible = await getCurrentWindow().isVisible();
      if (cancelled || visible) return;
      const fresh = Object.values(approvals).filter((a) => !toastedRef.current.has(a.approval_id));
      if (fresh.length === 0) return;
      if (!((await isPermissionGranted()) || (await requestPermission()) === "granted")) return;
      for (const a of fresh) {
        toastedRef.current.add(a.approval_id);
        sendNotification({ title: "Halo needs your OK", body: a.summary ?? `Approve ${a.tool}?` });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [approvals]);

  useEffect(() => {
    if (!isTauri()) return;
    const p = onAction(() => void invoke("show_workspace"));
    return () => void p.then((unlisten) => unlisten.unregister());
  }, []);

  // Deep-jump plumbing (Step 6): a focusTarget switches to the relevant view
  // then clears itself. ponytail: scroll-into-view for the specific
  // approval/task card lands once the real panels exist (Steps 9-10).
  useEffect(() => {
    if (!focusTarget) return;
    setActiveView("tasks");
    clearFocusTarget();
  }, [focusTarget, setActiveView, clearFocusTarget]);

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
          <div className="workspace-main">
            <StatusStrip sendTaskOp={sendTaskOp} />
            <div className="workspace-views">
              {VIEWS.map((v) => (
                <div key={v.id} hidden={activeView !== v.id} className="workspace-view">
                  {v.id === "chat" ? (
                    <ChatView
                      conversationId={conversationId}
                      connState={connState}
                      sendUserMsg={sendUserMsg}
                      inputId={CHAT_INPUT_ID}
                    />
                  ) : v.id === "activity" ? (
                    <ActivityFeed sendUndo={sendUndo} />
                  ) : v.id === "tasks" ? (
                    <TasksView sendTaskOp={sendTaskOp} sendLanePin={sendLanePin} />
                  ) : v.id === "memory" ? (
                    <MemoryView sendMemoryEdit={sendMemoryEdit} />
                  ) : (
                    <ViewPlaceholder name={v.label} />
                  )}
                </div>
              ))}
              <ApprovalOverlay
                conversationId={conversationId}
                sendApprovalResponse={sendApprovalResponse}
                sendInterrupt={sendInterrupt}
              />
            </div>
          </div>
        </GlassPanel>
      </div>
    </div>
  );
}
