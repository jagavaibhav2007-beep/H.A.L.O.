// Phase 1 Step 7 — the orb's peek bubble: a small glass bubble for moments
// that deserve a glance, not the full workspace. Never approvals -- those
// get the amber ring + card (ui_ux/01-companion-orb.md "Peek bubble").
// D9 browser-fallback ONLY (rework): in the real Tauri app this content
// renders in its own dedicated window instead -- see PeekWindow.tsx and
// OrbRoot.tsx's Tauri branch. Detection (three-source most-recent-wins) is
// shared via usePeekSource; this component owns dismiss timing + hover-pin,
// which only make sense with an inline, hoverable element.

import { useCallback, useEffect, useRef, useState } from "react";
import { usePeekSource, PEEK_DISMISS_MS } from "./usePeekSource";
import "./OrbRoot.css";

export function PeekBubble() {
  const sourceText = usePeekSource();

  const [content, setContent] = useState<string | null>(null);
  const dismissTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pinnedRef = useRef(false);

  const show = useCallback((text: string) => {
    setContent(text);
    if (dismissTimer.current) clearTimeout(dismissTimer.current);
    if (pinnedRef.current) return; // hover pins -- don't (re)start the dismiss timer while pinned
    dismissTimer.current = setTimeout(() => setContent(null), PEEK_DISMISS_MS);
  }, []);

  useEffect(() => {
    if (sourceText != null) show(sourceText);
  }, [sourceText, show]);

  useEffect(
    () => () => {
      if (dismissTimer.current) clearTimeout(dismissTimer.current);
    },
    [],
  );

  if (!content) return null;

  return (
    <div
      className="peek-bubble glass"
      role="status"
      aria-live="polite"
      onMouseEnter={() => {
        pinnedRef.current = true;
        if (dismissTimer.current) clearTimeout(dismissTimer.current);
      }}
      onMouseLeave={() => {
        pinnedRef.current = false;
        dismissTimer.current = setTimeout(() => setContent(null), PEEK_DISMISS_MS);
      }}
    >
      {content}
    </div>
  );
}
