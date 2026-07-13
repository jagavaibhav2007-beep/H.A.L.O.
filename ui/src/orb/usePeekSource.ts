// Phase 1 Step 7 (rework) — shared "what should the peek bubble show right
// now" detection, extracted so both the D9 browser-fallback inline bubble
// (PeekBubble.tsx) and the Tauri dedicated peek window's caller (OrbRoot.tsx)
// have one source of truth. Most-recent-wins across two sources: live
// transcript while listening/speaking and the newest narrate:true activity.
// Skill births already arrive as narrated activities, so they need no second
// snapshot-diff path. Dismiss
// timing is NOT this hook's job -- each consumer owns its own timer on top
// of the text this returns (inline bubble: hover-pin support; Tauri path:
// invoke("hide_peek")).

import { useEffect, useRef, useState } from "react";
import { useHaloStore, selectActivities, selectVoice } from "../state/store";

export const PEEK_DISMISS_MS = 4000;

export function usePeekSource(): string | null {
  const voice = useHaloStore(selectVoice);
  const activities = useHaloStore(selectActivities);

  const [text, setText] = useState<string | null>(null);
  const lastActivityIdRef = useRef<string | null>(null);

  // Live transcript while actively listening or speaking.
  useEffect(() => {
    if (!voice.transcript) return;
    if (voice.state !== "listening" && voice.state !== "speaking") return;
    setText(voice.transcript.text);
  }, [voice.transcript, voice.state]);

  // Newest narrate:true activity line.
  useEffect(() => {
    const narrated = [...activities].reverse().find((a) => a.narrate);
    if (!narrated || narrated.id === lastActivityIdRef.current) return;
    lastActivityIdRef.current = narrated.id;
    setText(narrated.text);
  }, [activities]);

  // setText bails with no re-render if a source repeats the exact same string
  // back-to-back. Narrated activities carry unique envelope ids, but repeating
  // identical copy does not need to reopen a dismissed peek.
  return text;
}
