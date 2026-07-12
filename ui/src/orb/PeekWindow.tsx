// Phase 1 Step 7 (rework) — content of the dedicated `peek` Tauri window.
// Display-only: the orb owns detection/state (usePeekSource) and the Rust
// `show_peek` command positions this window and emits `peek-text`; this
// component just renders whatever text arrives. Mirrors WorkspaceRoot's
// `workspace-anchor` listen pattern.

import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import "../styles/glass.css";
import "./OrbRoot.css";

interface PeekTextPayload {
  text: string;
}

export function PeekWindow() {
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    const unlistenPromise = listen<PeekTextPayload>("peek-text", (event) => {
      setText(event.payload.text);
    });
    return () => {
      void unlistenPromise.then((f) => f());
    };
  }, []);

  if (!text) return null;

  return (
    <div className="peek-window">
      <div className="peek-bubble glass" role="status" aria-live="polite">
        {text}
      </div>
    </div>
  );
}
