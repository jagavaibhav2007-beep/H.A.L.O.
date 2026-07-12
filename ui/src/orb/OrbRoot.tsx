// Phase 1 Step 5/7 — the orb window's content: draggable/clickable glass
// circle (Step 5 plumbing, untouched) plus the 9-state visual language and
// peek bubble (Step 7). Spec: phase-1-plan.md Steps 5 & 7,
// ui_ux/01-companion-orb.md.

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow, PhysicalPosition, PhysicalSize } from "@tauri-apps/api/window";
import { AlertTriangle, MicOff } from "lucide-react";
import { Icon } from "../components/Icon";
import { useHaloConnection } from "../ipc/useHaloConnection";
import type { IpcMessage } from "../ipc/contract";
import {
  useHaloStore,
  selectOrbState,
  selectPendingApprovalCount,
  selectRunningTask,
  selectVoice,
} from "../state/store";
import { PeekBubble } from "./PeekBubble";
import { usePeekSource, PEEK_DISMISS_MS } from "./usePeekSource";
import "../styles/glass.css";
import "./OrbRoot.css";

// Below this pointer travel, a down+up is a click (expand); at or above it,
// it's a drag (move the window). Plan: "manual pointer-drag with a 4px
// movement threshold".
const DRAG_THRESHOLD_PX = 4;

// Resize happens ONLY from this small bottom-right corner square; the whole
// rest of the orb drags to move. Edge-based resize was the bug: on a ~64px
// window an 8px handle on every edge covers almost the entire surface, so
// grabbing the orb to move it started an OS resize instead and ballooned the
// window into a giant letterboxed rectangle.
const RESIZE_CORNER_PX = 16;

// Resize is clamped and square (the sphere stays a circle, never a letterboxed
// ellipse, and can't grow to fill the screen). Kept within tauri.conf's
// min/max so the OS never overrides these.
const MIN_ORB_PX = 48;
const MAX_ORB_PX = 128;

interface DragState {
  startScreenX: number;
  startScreenY: number;
  windowStartX: number;
  windowStartY: number;
  moved: boolean;
}

interface ResizeState {
  startScreenX: number;
  startScreenY: number;
  startSize: number;
}

// True only inside the bottom-right corner square — the sole resize zone.
// clientX/clientY are viewport-relative, so this is the window's own corner.
function inResizeCorner(e: React.PointerEvent): boolean {
  return (
    e.clientX > window.innerWidth - RESIZE_CORNER_PX && e.clientY > window.innerHeight - RESIZE_CORNER_PX
  );
}

export function OrbRoot() {
  const dragRef = useRef<DragState | null>(null);
  const resizeRef = useRef<ResizeState | null>(null);
  // The glass sphere fills the window (resize is square-locked below, so the
  // window stays square and the sphere stays a circle). Recomputed live via
  // ResizeObserver as the window resizes — a plain resize event isn't enough:
  // this tracks the actual content box, and works identically in the D9
  // browser-fallback tab.
  const [orbSize, setOrbSize] = useState(64);

  useEffect(() => {
    const root = document.documentElement;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setOrbSize(Math.floor(Math.min(width, height)));
    });
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  // The orb window has no store of its own to read (each webview boots its
  // own instance) -- it owns its own live connection, mirroring
  // WorkspaceRoot's wiring verbatim so the orb's state visuals (below) react
  // to the same frames the workspace does.
  const onMessage = useCallback((frame: IpcMessage) => {
    useHaloStore.getState().applyFrame(frame);
  }, []);
  const { connState, sidecarError } = useHaloConnection(onMessage);

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

  // Dedicated peek window (rework -- see PeekWindow.tsx): the inline bubble
  // is invisible in the real app (clipped by the 64x64 window rect), so the
  // Tauri branch drives a separate always-on-top window instead of rendering
  // <PeekBubble/>. show_peek repositions+shows it and emits the text; a local
  // 4s timer (reset on every fresh source text) calls hide_peek.
  // ponytail: hover-pin (pause dismiss on hover) would need cross-window
  // pointer-event plumbing to the peek webview -- dropped here, kept in the
  // browser-fallback inline bubble where it's free.
  const peekText = usePeekSource();
  const peekHideTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (!isTauri() || peekText == null) return;
    let cancelled = false;
    void (async () => {
      const win = getCurrentWindow();
      const [pos, size] = await Promise.all([win.outerPosition(), win.outerSize()]);
      if (cancelled) return;
      await invoke("show_peek", { text: peekText, orbX: pos.x, orbY: pos.y, orbW: size.width, orbH: size.height });
    })();
    if (peekHideTimer.current) clearTimeout(peekHideTimer.current);
    peekHideTimer.current = setTimeout(() => void invoke("hide_peek"), PEEK_DISMISS_MS);
    return () => {
      cancelled = true;
    };
  }, [peekText]);

  useEffect(
    () => () => {
      if (peekHideTimer.current) clearTimeout(peekHideTimer.current);
    },
    [],
  );

  const orbState = useHaloStore(selectOrbState);
  const approvalCount = useHaloStore(selectPendingApprovalCount);
  const runningTask = useHaloStore(selectRunningTask);
  const voice = useHaloStore(selectVoice);

  // Task progress arc: a CSS custom property read by OrbRoot.css's
  // conic-gradient ring (`--task-progress`, 0..1). No step/steps_total yet ->
  // a small fixed arc reads as "something's running" without claiming a
  // (nonexistent) percentage -- see OrbRoot.css's [data-indeterminate] rule
  // for the motion-gated spin that says so more clearly when allowed.
  const hasProgress = runningTask?.step != null && runningTask?.steps_total != null;
  const taskProgress = hasProgress ? runningTask!.step! / runningTask!.steps_total! : 0.15;
  const taskTooltip = runningTask
    ? [runningTask.title ?? "Working", hasProgress ? `step ${runningTask.step}/${runningTask.steps_total}` : null, runningTask.step_label]
        .filter(Boolean)
        .join(" — ")
    : undefined;

  const onPointerDown = useCallback(async (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    // Capture on this element so move/up keep firing even if the pointer
    // leaves the tiny window mid-gesture (both drag and resize need it).
    e.currentTarget.setPointerCapture(e.pointerId);

    if (inResizeCorner(e)) {
      const size = await getCurrentWindow().outerSize();
      resizeRef.current = {
        startScreenX: e.screenX,
        startScreenY: e.screenY,
        startSize: Math.min(size.width, size.height),
      };
      void invoke("hide_peek"); // don't let a stale peek float beside a resizing orb
      return;
    }

    const pos = await getCurrentWindow().outerPosition();
    dragRef.current = {
      startScreenX: e.screenX,
      startScreenY: e.screenY,
      windowStartX: pos.x,
      windowStartY: pos.y,
      moved: false,
    };
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const resize = resizeRef.current;
    if (resize) {
      // Square-locked: one delta (whichever axis moved more) drives both
      // dimensions, clamped, so the window can never letterbox or balloon.
      const delta = Math.max(e.screenX - resize.startScreenX, e.screenY - resize.startScreenY);
      const next = Math.round(Math.min(MAX_ORB_PX, Math.max(MIN_ORB_PX, resize.startSize + delta)));
      void getCurrentWindow().setSize(new PhysicalSize(next, next));
      return;
    }

    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.screenX - drag.startScreenX;
    const dy = e.screenY - drag.startScreenY;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
    if (!drag.moved) void invoke("hide_peek"); // drag just started -- don't let a stale peek follow the window
    drag.moved = true;
    void getCurrentWindow().setPosition(new PhysicalPosition(drag.windowStartX + dx, drag.windowStartY + dy));
  }, []);

  const onPointerUp = useCallback(async (e: React.PointerEvent) => {
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (resizeRef.current) {
      resizeRef.current = null;
      return; // a resize is never a click
    }

    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;

    if (!drag.moved) {
      // A click, not a drag — expand the workspace, anchored at the orb.
      // D3: show -> focus goes to the workspace, never the orb (the Rust
      // command does the focusing; this window never calls setFocus).
      await invoke("toggle_workspace", { orbX: drag.windowStartX, orbY: drag.windowStartY });
    }
    // A drag ends wherever the user released it -- free placement, no
    // edge-snapping. (window-state plugin persists the final position.)
  }, []);

  const onContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    if (isTauri()) void invoke("show_orb_menu");
  }, []);

  // Nine-state visuals (ui_ux/01-companion-orb.md "State language") plus the
  // always-on mute overlay (ruling: mute is never hidden by a higher-priority
  // state, layered on top of whatever deriveOrbState returns rather than
  // replacing it). Shared between the Tauri and D9 browser-fallback branches
  // below -- the only difference between them is the pointer/drag handlers.
  const orbVisual = (
    <div className="orb-stack">
      <div
        className="orb glass"
        data-orb-state={orbState}
        data-indeterminate={orbState === "task" && !hasProgress ? "true" : undefined}
        style={{ width: orbSize, height: orbSize, "--task-progress": `${taskProgress}` } as React.CSSProperties}
        title={orbState === "task" ? taskTooltip : undefined}
      />
      {approvalCount > 0 && (
        <span
          className="orb-badge orb-badge-approval"
          role="status"
          aria-label={`${approvalCount} pending approval${approvalCount > 1 ? "s" : ""}`}
        >
          {approvalCount}
        </span>
      )}
      {orbState === "error" && (
        <span className="orb-badge orb-badge-error" role="status" aria-label="Brain connection error">
          <Icon icon={AlertTriangle} size={16} />
        </span>
      )}
      {voice.state === "muted" && (
        <span className="orb-mute-badge" role="status" aria-label="Microphone muted">
          <Icon icon={MicOff} size={16} />
        </span>
      )}
      {/* Tauri: the dedicated peek window (above) handles this instead --
          rendering it here too would double-show the same text inline,
          invisibly, inside the clipped 64x64 window rect. */}
      {!isTauri() && <PeekBubble />}
      {/* Bottom-right resize grip (Tauri only) — the sole resize zone; the
          pointer handlers detect the corner by coordinate, this is the hint. */}
      {isTauri() && <span className="orb-resize-handle" aria-hidden="true" />}
    </div>
  );

  // D9 browser fallback: no OS window to drag/resize/expand, just the visual
  // (still circle-locked via the same ResizeObserver, for eyeball parity).
  // Note: without a Tauri connection there's no session.json to read, so
  // useHaloConnection retries read_session forever and the store never gets
  // frames -- the orb just shows idle here, same as before any connection.
  if (!isTauri()) {
    return <div className="orb-hit-area">{orbVisual}</div>;
  }

  return (
    <div
      className="orb-hit-area"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onContextMenu={onContextMenu}
    >
      {orbVisual}
    </div>
  );
}
