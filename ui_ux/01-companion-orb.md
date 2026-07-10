# Halo UI/UX: The Companion Orb

Halo's face — a small glass orb, on screen whenever Halo runs (user decision). 95% of the relationship happens here. Behavior source: [systemdesign/02-voice](../systemdesign/02-voice.md), [10-ui](../systemdesign/10-ui.md).

## Anatomy
- ~56px glass sphere, baby-blue inner gradient, soft shadow. Draggable anywhere; **snaps to screen edges**, position remembered. Always-on-top but never steals focus.
- A thin **status ring** around the orb carries most state; the core glow carries mood.

## State language (the orb IS the voice UI)
| State | Visual | Also |
|---|---|---|
| Idle | faint breathing glow (4s cycle) | — |
| Wake heard ("Halo") | one ripple ring outward | soft chime |
| Listening | rim lit `--accent-soft`, ring reacts subtly to your voice level | live transcript in peek bubble |
| Thinking | inner gradient slowly swirls | — |
| Speaking | glow pulses gently with speech amplitude | barge-in: pulse stops instantly → listening |
| Task running | thin progress arc on the ring | hover shows "what I'm doing" tooltip |
| **Needs approval** | ring turns `--tier-3` amber, 2 gentle pulses then steady | badge with count; click jumps to card |
| Error | one brief red ring flash, then persistent small badge | never a modal from the orb |
| Mic muted | glow off, gray, slashed-mic glyph | mute is always visually loud |

One state at a time; approval > error > task > voice in priority. All states also exist as color/opacity only (reduced motion).

## Interactions
- **Click / global hotkey** → expands into the [workspace](02-workspace.md) (250ms scale+fade from the orb — spatial continuity).
- **Right-click** → quick menu: Mute mic · Pause all tasks · Open workspace · Quit.
- **Hover** → peek bubble: current status sentence + last activity line.
- **Voice needs no interaction** — say "Halo…" from anywhere; the orb acknowledges even while collapsed.

## Peek bubble
A small glass bubble that slides from the orb (200ms) for moments that deserve a glance but not the full window: live transcript while you speak, one-line narration of main task events, "made a new skill" notices. Auto-dismisses in 4s; hover pins it. Never used for approvals — those get the amber ring + card.

## When the window is closed
The orb never disappears while Halo runs; quitting is explicit (right-click → Quit). If the user is away and a Tier-3 gate fires: orb goes amber **and** a Windows toast fires ([systemdesign/04-permissions](../systemdesign/04-permissions.md)). Clicking the toast opens the workspace focused on the approval card.
