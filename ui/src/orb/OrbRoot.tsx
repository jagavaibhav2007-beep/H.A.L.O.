// The companion capsule — Halo's face, always on top. Spec:
// ui_ux/01-companion-orb.md "Anatomy"/"State language"/"Interactions".
// Layout: [lane · task]  ((orb))  [approval · mic] — all simultaneous, no
// priority ladder (the old one-state-at-a-time orb selector crammed 4
// signals into one circle; chips have their own space now, so every true
// signal shows at once).

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow, LogicalPosition } from "@tauri-apps/api/window";
import { AlertTriangle, Mic, MicOff } from "lucide-react";
import { Icon } from "../components/Icon";
import { useStoreConnection } from "../state/useStoreConnection";
import {
  useHaloStore,
  selectApprovals,
  selectBrainStatus,
  selectPendingApprovalCount,
  selectTasks,
  selectVoice,
  selectCapabilities,
} from "../state/store";
import { LANE_ICON, LANE_LABEL, formatTaskProgress } from "../lib/lanes";
import { usePeekSource, PEEK_DISMISS_MS } from "./usePeekSource";
import { FloatingApproval } from "./FloatingApproval";
import { useApprovalWindow } from "./useApprovalWindow";
import "../styles/glass.css";
import "./OrbRoot.css";

// Below this pointer travel, a down+up is a click (expand); at or above it,
// it's a drag (move the window). Plan: "manual pointer-drag with a 4px
// movement threshold".
const DRAG_THRESHOLD_PX = 4;

interface DragState {
  startScreenX: number;
  startScreenY: number;
  origin: Promise<{ x: number; y: number } | null>;
  latestDx: number;
  latestDy: number;
  moved: boolean;
}

export function OrbRoot() {
  const dragRef = useRef<DragState | null>(null);

  // The orb window owns its own live connection + store instance, wired the
  // same way the workspace is (each webview boots its own -- see store.ts).
  const { connState, sendApprovalResponse } = useStoreConnection();

  // Narration renders inline beside the orb -- the capsule has room, unlike
  // the old 64px circle (ui_ux/01-companion-orb.md "Narration"). A local
  // timer clears it PEEK_DISMISS_MS after the source text last changed;
  // usePeekSource itself doesn't own dismiss timing.
  const sourceText = usePeekSource();
  const [narration, setNarration] = useState<string | null>(null);
  const dismissTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (sourceText == null) return;
    setNarration(sourceText);
    if (dismissTimer.current) clearTimeout(dismissTimer.current);
    dismissTimer.current = setTimeout(() => setNarration(null), PEEK_DISMISS_MS);
  }, [sourceText]);
  useEffect(
    () => () => {
      if (dismissTimer.current) clearTimeout(dismissTimer.current);
    },
    [],
  );

  const approvals = useHaloStore(selectApprovals);
  const approvalCount = useHaloStore(selectPendingApprovalCount);
  const activeApproval = Object.values(approvals)[0];
  useApprovalWindow(Boolean(activeApproval));
  const brainStatus = useHaloStore(selectBrainStatus);
  const tasks = useHaloStore(selectTasks);
  const voice = useHaloStore(selectVoice);
  const capabilities = useHaloStore(selectCapabilities);

  const runningTasks = Object.values(tasks).filter((t) => t.state === "running");
  const primaryTask = runningTasks[0];
  const lane = primaryTask?.lane ?? 1;
  const LaneGlyph = LANE_ICON[lane];
  const hasProgress = primaryTask?.step != null && primaryTask?.steps_total != null;
  const taskProgress = hasProgress ? primaryTask!.step! / primaryTask!.steps_total! : 0.15;
  const taskTitle = primaryTask
    ? [primaryTask.title ?? "Working", formatTaskProgress(primaryTask)].filter(Boolean).join(" · ")
    : undefined;

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    // Capture on this element so move/up keep firing even if the pointer
    // leaves the tiny window mid-gesture.
    e.currentTarget.setPointerCapture(e.pointerId);

    // Everything below works in LOGICAL pixels, to match the pointer's
    // e.screenX/screenY (which are CSS/logical). outerPosition() returns
    // PHYSICAL px, so convert via the scale factor -- mixing the two broke
    // drag (moved at 1/scale speed) on a 2x-DPI display. See Bugs.md "DPI
    // mismatch".
    const win = getCurrentWindow();
    const origin = (async () => {
      try {
        const scale = await win.scaleFactor();
        const pos = (await win.outerPosition()).toLogical(scale);
        return { x: pos.x, y: pos.y };
      } catch {
        return null;
      }
    })();
    dragRef.current = {
      startScreenX: e.screenX,
      startScreenY: e.screenY,
      origin,
      latestDx: 0,
      latestDy: 0,
      moved: false,
    };
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.screenX - drag.startScreenX;
    const dy = e.screenY - drag.startScreenY;
    drag.latestDx = dx;
    drag.latestDy = dy;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
    drag.moved = true;
    void drag.origin.then((origin) => {
      if (!origin || dragRef.current !== drag || !drag.moved) return;
      void getCurrentWindow().setPosition(
        new LogicalPosition(origin.x + drag.latestDx, origin.y + drag.latestDy),
      );
    });
  }, []);

  const onPointerUp = useCallback(async (e: React.PointerEvent) => {
    e.currentTarget.releasePointerCapture(e.pointerId);
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;

    if (!drag.moved) {
      const origin = await drag.origin;
      if (!origin) return;
      // A click, not a drag — expand the workspace, anchored at the orb.
      // D3: show -> focus goes to the workspace, never the orb (the Rust
      // command does the focusing; this window never calls setFocus).
      await invoke("toggle_workspace", { orbX: origin.x, orbY: origin.y });
    } else {
      // A fast drag can finish before the asynchronous DPI/position lookup.
      // Commit the final delta here as well so releasing early never turns the
      // gesture into a no-op.
      const origin = await drag.origin;
      if (!origin) return;
      await getCurrentWindow().setPosition(
        new LogicalPosition(origin.x + drag.latestDx, origin.y + drag.latestDy),
      );
    }
    // A drag ends wherever the user released it -- free placement, no
    // edge-snapping. (window-state plugin persists the final position.)
  }, []);

  const onPointerCancel = useCallback((e: React.PointerEvent) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    dragRef.current = null;
  }, []);

  const onContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    if (isTauri()) void invoke("show_orb_menu");
  }, []);

  // Chips are buttons: clicking approval/task opens the workspace, same as
  // the capsule body. Deep-linking to a specific view needs a cross-window
  // nav channel that doesn't exist (the old focusTarget was deleted as dead
  // code) -- ponytail: deep-link to the specific view once the workspace has
  // a cross-window nav channel. stopPropagation on pointerdown so the click
  // doesn't also register as the body's drag/click gesture.
  const stopChipPointer = useCallback((e: React.PointerEvent) => e.stopPropagation(), []);
  const stopApprovalPointer = useCallback((e: React.PointerEvent) => e.stopPropagation(), []);
  const onChipClick = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!isTauri()) return;
    const win = getCurrentWindow();
    const scale = await win.scaleFactor();
    const pos = (await win.outerPosition()).toLogical(scale);
    await invoke("toggle_workspace", { orbX: pos.x, orbY: pos.y });
  }, []);
  const onReview = useCallback(() => {
    if (isTauri()) void invoke("show_workspace");
  }, []);

  const capsule = (
    // Not .glass: the capsule paints its own fixed midnight surface — native
    // WebView2 can't backdrop-blur the desktop behind the window, and the
    // capsule must look identical in light/dark theme.
    <div className="capsule">
      <div className="capsule-glow" aria-hidden="true" />
      <div
        className="capsule-status"
        onPointerDown={isTauri() ? onPointerDown : undefined}
        onPointerMove={isTauri() ? onPointerMove : undefined}
        onPointerUp={isTauri() ? onPointerUp : undefined}
        onPointerCancel={isTauri() ? onPointerCancel : undefined}
        onContextMenu={isTauri() ? onContextMenu : undefined}
      >
      {/* Narration is the live transcript ("you always see what it heard",
          ui_ux/04-voice.md) — but this window never takes focus, so a screen
          reader only ever reaches it through a live region. The region is
          mounted unconditionally and empty: a live region that appears
          already-populated is frequently not announced at all. The visible
          span below is aria-hidden so it isn't read twice. */}
      <span className="halo-sr-only" role="status" aria-live="polite" aria-atomic="true">
        {narration ?? ""}
      </span>

      <div className="capsule-left">
        <span className="chip lane-chip" role="status" aria-label={`Active lane: ${LANE_LABEL[lane]}`}>
          <Icon icon={LaneGlyph} size={16} />
          <span>{LANE_LABEL[lane]}</span>
        </span>
        {runningTasks.length > 0 && (
          <button
            type="button"
            className="chip task-chip"
            onPointerDown={stopChipPointer}
            onClick={onChipClick}
            title={taskTitle}
            // The title attribute is hover-only; keyboard and screen-reader
            // users get the same detail through the accessible name.
            aria-label={[
              `${runningTasks.length} task${runningTasks.length > 1 ? "s" : ""} running`,
              taskTitle,
            ]
              .filter(Boolean)
              .join(": ")}
          >
            <TaskRing progress={taskProgress} indeterminate={!hasProgress} />
            <span>{runningTasks.length}</span>
          </button>
        )}
      </div>

      <div className="capsule-center" data-voice-state={voice.state}>
        <div className="orb-wrap">
          <div className="orb-halo" aria-hidden="true" />
          <div className="orb-core" aria-hidden="true" />
        </div>
        {/* Truncates to whatever the chip clusters leave (OrbRoot.css
            .narration); title carries the full line on hover. */}
        {narration && (
          <span className="narration" title={narration} aria-hidden="true">
            {narration}
          </span>
        )}
      </div>

      <div className="capsule-right">
        {brainStatus === "error" && (
          <span className="chip error-chip" role="status" aria-label="Brain failed to start">
            <Icon icon={AlertTriangle} size={16} />
          </span>
        )}
        {approvalCount > 0 && (
          <button
            type="button"
            className="chip approval-chip"
            onPointerDown={stopChipPointer}
            onClick={onChipClick}
            aria-label={`${approvalCount} pending approval${approvalCount > 1 ? "s" : ""}`}
          >
            {approvalCount}
          </button>
        )}
        <span
          className="mic-indicator"
          role="status"
          aria-label={
            capabilities.voiceInput === true
              ? voice.state === "muted"
                ? "Microphone muted"
                : "Microphone live"
              : capabilities.voiceInput === false
                ? "Voice input unavailable"
                : "Checking voice availability"
          }
        >
          <Icon icon={capabilities.voiceInput === true && voice.state !== "muted" ? Mic : MicOff} size={16} />
        </span>
      </div>
      </div>
      {activeApproval && (
        <div
          className="approval-panel"
          onPointerDown={stopApprovalPointer}
          onPointerMove={stopApprovalPointer}
          onPointerUp={stopApprovalPointer}
          onPointerCancel={stopApprovalPointer}
        >
          <FloatingApproval
            approval={activeApproval}
            count={approvalCount}
            connected={connState === "connected"}
            sendApprovalResponse={sendApprovalResponse}
            onReview={onReview}
          />
        </div>
      )}
    </div>
  );

  return <div className="capsule-hit-area">{capsule}</div>;
}

// Small SVG progress ring for the task chip. Indeterminate spin is killed by
// prefers-reduced-motion globally (glass.css forces ~0 animation duration).
function TaskRing({ progress, indeterminate }: { progress: number; indeterminate: boolean }) {
  const r = 5;
  const c = 2 * Math.PI * r;
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" className={indeterminate ? "task-ring task-ring-spin" : "task-ring"}>
      {/* Capsule-local palette, not the theme tokens — see .capsule in OrbRoot.css. */}
      <circle cx="7" cy="7" r={r} fill="none" stroke="var(--cap-border)" strokeWidth="2" />
      <circle
        cx="7"
        cy="7"
        r={r}
        fill="none"
        stroke="var(--cap-primary)"
        strokeWidth="2"
        strokeDasharray={c}
        strokeDashoffset={c * (1 - progress)}
        strokeLinecap="round"
        transform="rotate(-90 7 7)"
      />
    </svg>
  );
}
