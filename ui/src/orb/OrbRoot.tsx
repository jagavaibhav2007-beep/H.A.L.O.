// Phase 1 Step 5 — the orb window's content: a minimal draggable/clickable
// glass circle. Full 9-state visual language is Step 7; this is plumbing.
// Spec: phase-1-plan.md Step 5, ui_ux/01-companion-orb.md "Anatomy".

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import "../styles/glass.css";
import "./OrbRoot.css";

// @tauri-apps/api/window declares this type but doesn't export it, so it's
// mirrored here to match startResizeDragging's accepted values exactly.
type ResizeDirection = "East" | "North" | "NorthEast" | "NorthWest" | "South" | "SouthEast" | "SouthWest" | "West";

// Below this pointer travel, a down+up is a click (expand); at or above it,
// it's a drag (move the window). Plan: "manual pointer-drag with a 4px
// movement threshold".
const DRAG_THRESHOLD_PX = 4;

// Pointer-downs within this many px of a window edge start a native OS
// resize instead of a move-drag or click — the orb window is borderless
// (decorations:false), so there's no OS chrome to grab for resizing.
const RESIZE_HANDLE_PX = 8;

interface DragState {
  startScreenX: number;
  startScreenY: number;
  windowStartX: number;
  windowStartY: number;
  moved: boolean;
}

// clientX/clientY are already viewport-relative, so this is checked against
// the window's own size, not the (possibly smaller, letterboxed) visible
// circle — every edge stays a reachable resize handle regardless of aspect.
function resizeDirectionAt(e: React.PointerEvent): ResizeDirection | null {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const nearLeft = e.clientX < RESIZE_HANDLE_PX;
  const nearRight = e.clientX > w - RESIZE_HANDLE_PX;
  const nearTop = e.clientY < RESIZE_HANDLE_PX;
  const nearBottom = e.clientY > h - RESIZE_HANDLE_PX;

  if (nearLeft && nearTop) return "NorthWest";
  if (nearRight && nearTop) return "NorthEast";
  if (nearLeft && nearBottom) return "SouthWest";
  if (nearRight && nearBottom) return "SouthEast";
  if (nearLeft) return "West";
  if (nearRight) return "East";
  if (nearTop) return "North";
  if (nearBottom) return "South";
  return null;
}

export function OrbRoot() {
  const dragRef = useRef<DragState | null>(null);
  // The glass sphere always fills the smaller window dimension, so a
  // non-square resize (e.g. dragging one edge, not a corner) never stretches
  // it into an ellipse — the excess space on the longer axis stays
  // transparent. Recomputed live via ResizeObserver as the OS resizes the
  // window (a plain resize event isn't enough: this must track the actual
  // content box, and works identically in the D9 browser-fallback tab).
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

  const onPointerDown = useCallback(async (e: React.PointerEvent) => {
    if (e.button !== 0) return;

    const direction = resizeDirectionAt(e);
    if (direction) {
      // Native OS-driven resize — the window manager owns the drag from
      // here; don't also start our own move-drag tracking.
      await getCurrentWindow().startResizeDragging(direction);
      return;
    }

    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
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
    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.screenX - drag.startScreenX;
    const dy = e.screenY - drag.startScreenY;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
    drag.moved = true;
    void getCurrentWindow().setPosition(new PhysicalPosition(drag.windowStartX + dx, drag.windowStartY + dy));
  }, []);

  const onPointerUp = useCallback(async (e: React.PointerEvent) => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    e.currentTarget.releasePointerCapture(e.pointerId);

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

  // D9 browser fallback: no OS window to drag/resize/expand, just the visual
  // (still circle-locked via the same ResizeObserver, for eyeball parity).
  if (!isTauri()) {
    return (
      <div className="orb-hit-area">
        <div className="orb glass" style={{ width: orbSize, height: orbSize }} />
      </div>
    );
  }

  return (
    <div
      className="orb-hit-area"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onContextMenu={onContextMenu}
    >
      <div className="orb glass" style={{ width: orbSize, height: orbSize }} />
    </div>
  );
}
