// Phase 1 Step 7 (rework) — shared "what should the peek bubble show right
// now" detection, extracted so both the D9 browser-fallback inline bubble
// (PeekBubble.tsx) and the Tauri dedicated peek window's caller (OrbRoot.tsx)
// have one source of truth. Most-recent-wins across three sources: live
// transcript while listening/speaking, the newest narrate:true activity, and
// skill-birth notices (ui_ux/01-companion-orb.md "Peek bubble"). Dismiss
// timing is NOT this hook's job -- each consumer owns its own timer on top
// of the text this returns (inline bubble: hover-pin support; Tauri path:
// invoke("hide_peek")).

import { useEffect, useRef, useState } from "react";
import { useHaloStore, selectActivities, selectSkills, selectVoice } from "../state/store";

export const PEEK_DISMISS_MS = 4000;

export function usePeekSource(): string | null {
  const voice = useHaloStore(selectVoice);
  const activities = useHaloStore(selectActivities);
  const skills = useHaloStore(selectSkills);

  const [text, setText] = useState<string | null>(null);
  const lastActivityIdRef = useRef<string | null>(null);
  const seenSkillsRef = useRef<Set<string>>(new Set());
  const skillsSeeded = useRef(false);

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

  // ponytail: no "skill born" event exists on the wire (skill_state is
  // upsert-only) -- detect a birth by diffing newly-appearing active
  // skill_names against a running seen-set. The first snapshot after
  // connect is the existing roster, not new births, so it seeds silently.
  useEffect(() => {
    const names = Object.keys(skills);
    if (!skillsSeeded.current) {
      skillsSeeded.current = true;
      for (const name of names) seenSkillsRef.current.add(name);
      return;
    }
    for (const name of names) {
      if (seenSkillsRef.current.has(name)) continue;
      seenSkillsRef.current.add(name);
      if (skills[name].status === "active") setText(`just learned a new skill: ${name}`);
    }
  }, [skills]);

  // ponytail: setText bails with no re-render if a source repeats the exact
  // same string back-to-back (React state equality) -- consumers' dismiss
  // timers won't reset on that exact repeat. Fine here: all three sources
  // produce distinct text per real event in practice.
  return text;
}
