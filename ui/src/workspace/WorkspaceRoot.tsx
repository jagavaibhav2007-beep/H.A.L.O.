// Phase 1 Step 5 — the workspace window's content: plumbing only (show/hide
// animation anchored to the orb, Esc-collapses). The real shell (sidebar,
// panels) is Step 6 — this renders a placeholder.
// Spec: phase-1-plan.md Step 5, ui_ux/02-workspace.md.

import { useEffect, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { GlassPanel } from "../components/GlassPanel";
import "./WorkspaceRoot.css";

interface WorkspaceAnchor {
  x: number;
  y: number;
}

export function WorkspaceRoot() {
  const shellRef = useRef<HTMLDivElement>(null);
  const [anim, setAnim] = useState<"idle" | "enter" | "leave">("idle");
  const [origin, setOrigin] = useState("center");

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
      shell.addEventListener(
        "transitionend",
        () => {
          void getCurrentWindow().hide();
          setAnim("idle");
        },
        { once: true },
      );
      setAnim("leave");
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="workspace-root">
      <div ref={shellRef} className="workspace-shell" data-anim={anim} style={{ transformOrigin: origin }}>
        <GlassPanel elevation="panel" className="workspace-placeholder">
          <p>Workspace — the sidebar, status strip, and panels land in Step 6.</p>
        </GlassPanel>
      </div>
    </div>
  );
}
