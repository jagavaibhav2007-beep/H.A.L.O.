# Halo UI/UX: The Companion Capsule

Halo's face — a small glass capsule, on screen whenever Halo runs (user decision). 95% of the relationship happens here. Behavior source: [systemdesign/02-voice](../systemdesign/02-voice.md), [10-ui](../systemdesign/10-ui.md).

**Supersedes the orb-only design.** The original companion was a bare glass circle whose colour/glow encoded one state at a time. It was pretty and useless: you could not tell what Halo was doing or what needed you without opening the whole workspace. The companion is now a **capsule** — the orb survives as its centre, doing the one job it was good at (voice + narration), with status chips flanking it.

## Anatomy

A horizontal capsule, ~360×52px, fixed size, `border-radius = height/2` (a true pill, never a rounded rectangle). Frosted midnight glass over the desktop; always-on-top, never steals focus, dragged anywhere by its body, position remembered ([Decisions.md](../mem/Decisions.md): free placement, no edge-snap).

```
[ lane · task ]   ((orb))   [ approval · mic ]
  ambient          hero        needs you
```

- **Left cluster — ambient:** lane indicator (PRD §4 requires the active control lane always be visible) and running-task count with a mini progress ring.
- **Centre — the orb:** the hero. ~26px core, ~44px glow halo, dominant over the chips by size so the eye lands here first. Carries **voice state only** (idle/wake/listening/thinking/speaking/muted) and renders short narration text inline.
- **Right cluster — needs you:** pending-approval count (amber) and mic state.

**Everything visible at once — there is no priority ladder.** The old `approval > error > task > voice` selector existed only because a single circle could show one thing; chips have their own space, so every true signal is shown simultaneously. Anything urgent is a chip, not a colour change.

## State language

The orb core carries voice mood; every other signal is a chip beside it.

| Signal | Where | Visual |
|---|---|---|
| Idle | orb | faint breathing glow (~3.4s cycle) |
| Wake heard ("Halo") | orb | one ripple ring outward + soft chime |
| Listening | orb | rim lit, live transcript inline |
| Thinking | orb | inner gradient slowly swirls |
| Speaking | orb | glow pulses with amplitude; barge-in stops it instantly |
| Mic muted | orb + right chip | glow off, gray, slashed-mic glyph — mute is always visually loud |
| Task running | left chip | progress ring + count; hover reveals title/step |
| Active lane | left chip | icon + name (Fast / Takeover / Sandbox) |
| **Needs approval** | right chip | amber chip + count, the one thing allowed to break the blue |
| Error | right chip | red chip; never a modal from the capsule |

Every state also reads via icon/text, never colour alone (reduced-motion and colour-blind safe).

## Interactions

- **Click a chip** → opens the [workspace](02-workspace.md) deep-linked to that view (approval chip → the approval; task chip → tasks).
- **Click the capsule body** → expands into the workspace at the last view (250ms scale+fade from the capsule — spatial continuity).
- **Right-click** → quick menu: Mute mic · Pause all tasks · Open workspace · Quit.
- **Voice needs no interaction** — say "Halo…" from anywhere; the orb acknowledges while collapsed.
- Press-scale 0.97 on tappable chips; hover raises chip contrast.

## Floating approvals

When a request needs consent, the 360×52px capsule grows in place to 360×224px.
The expanded lower panel shows the plain-language summary, tool name, pending
count, and **Approve**, **Deny**, and **Review details** controls; it never
shows unredacted arguments. The oldest pending request is first. Resolving one
advances to the next without collapsing, then the capsule returns to 360×52px
only when none remain. **Review details** opens the full workspace, where the
user can inspect arguments and Edit before deciding.

Approve and Deny are unavailable while the Brain is disconnected, but Review
details remains available. Destructive requests use the same visible 700ms
hold-to-approve as the workspace card. The capsule stays non-focusing: an
approval must never steal the active app or approve by timeout. Its expanded
bounds stay within the current monitor work area, including mixed-DPI setups.

## Narration

Short narration renders **inline in the capsule**, beside the orb: live transcript while you speak, one-line narration of main task events, "made a new skill" notices. Auto-clears after 4s.

*This replaces the separate peek-bubble window.* The old design floated a second always-on-top window beside the orb because a 64px circle had nowhere to put text. The capsule has room, so the peek window, its two Rust commands, and its cross-window plumbing are gone. Approvals are still never narration — they are the amber chip.

## When the window is closed

The capsule never disappears while Halo runs; quitting is explicit (right-click → Quit). If the user is away and a Tier-3 gate fires, the capsule expands and stays amber with the approval controls waiting. Halo does not duplicate this with a Windows toast.
