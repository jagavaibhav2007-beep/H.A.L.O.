# Halo UI/UX: Skills Panel

Where Halo's self-taught abilities live — visible, trialable, killable. Source: [systemdesign/08-self-improvement](../systemdesign/08-self-improvement.md); browser playbooks live here too ([06-browser design](../systemdesign/06-browser.md)).

## Layout
- Skill cards, two groups: **Auto-learned** ✨ and **User-made** 🛠, plus a **Playbooks** filter (browser routines are skills).
- Each card:
```
┌───────────────────────────────────────────┐
│ ✨ Weekly expense report                   │
│ used 12× · 92% success · learned 3 Jul    │
│ [ ▶ trial run ]  [ pause ]  [ delete ]    │
└───────────────────────────────────────────┘
```
- Success rate as a small bar, red-tinted under 60% (the auto-retire threshold approaching is visible before it fires).

## Lifecycle moments in the UI
- **Born:** peek bubble + feed entry — "I made a skill for X (passed 3 test runs) — view / undo." Notify-only, per the autonomy boundary; *undo right there* keeps autonomy comfortable.
- **Trial run:** executes the skill in its sandbox against a sample input, results shown in a drawer — try before trusting.
- **Auto-retired:** card grays out with the reason ("failed 3× in a row") + restore button. Nothing vanishes silently.
- **Tier-3 edits:** attempts to modify a relied-on skill or core code surface as a standard approval card ([05-permissions-trust](05-permissions-trust.md)).

## Empty state
"No skills yet — I create them when I notice you repeating a task. Do something a few times and watch this space."
