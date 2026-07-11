// Phase 1 Step 5 — the orb window's content: a minimal draggable/clickable
// glass circle. Full 9-state visual language is Step 7; this is plumbing.
// Spec: phase-1-plan.md Step 5, ui_ux/01-companion-orb.md "Anatomy".

import { useCallback, useRef } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { availableMonitors, getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import "../styles/glass.css";
import "./OrbRoot.css";

// Below this pointer travel, a down+up is a click (expand); at or above it,
// it's a drag (move the window). Plan: "manual pointer-drag with a 4px
// movement threshold".
const DRAG_THRESHOLD_PX = 4;

interface DragState {
  startScreenX: number;
  startScreenY: number;
  windowStartX: number;
  windowStartY: number;
  moved: boolean;
}

export function OrbRoot() {
  const dragRef = useRef<DragState | null>(null);

  const onPointerDown = useCallback(async (e: React.PointerEvent) => {
    if (e.button !== 0) return;
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
      return;
    }
    await snapToNearestEdge();
  }, []);

  const onContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    if (isTauri()) void invoke("show_orb_menu");
  }, []);

  // D9 browser fallback: no OS window to drag/snap/expand, just the visual.
  if (!isTauri()) {
    return <div className="orb glass" />;
  }

  return (
    <div
      className="orb glass"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onContextMenu={onContextMenu}
    />
  );
}

async function snapToNearestEdge() {
  const win = getCurrentWindow();
  const [pos, size, monitors] = await Promise.all([win.outerPosition(), win.outerSize(), availableMonitors()]);
  if (monitors.length === 0) return;

  let nearest = monitors[0];
  let nearestDist = Infinity;
  for (const m of monitors) {
    const cx = m.position.x + m.size.width / 2;
    const cy = m.position.y + m.size.height / 2;
    const dist = (pos.x - cx) ** 2 + (pos.y - cy) ** 2;
    if (dist < nearestDist) {
      nearestDist = dist;
      nearest = m;
    }
  }

  const { x: mx, y: my } = nearest.position;
  const { width: mw, height: mh } = nearest.size;
  const leftDist = pos.x - mx;
  const rightDist = mx + mw - (pos.x + size.width);
  const snappedX = leftDist <= rightDist ? mx : mx + mw - size.width;
  const clampedY = Math.min(Math.max(pos.y, my), my + mh - size.height);
  await win.setPosition(new PhysicalPosition(snappedX, clampedY));
}
