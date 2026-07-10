# Halo UI/UX: Memory Panel

The inspectable second brain — beliefs you can read, correct, and delete. Source: [systemdesign/03-memory](../systemdesign/03-memory.md).

## Layout
- Belief cards grouped by kind (Preferences · Projects · Workflows · Decisions · Lessons), searchable, sorted by salience within group.
- Each card:
```
┌────────────────────────────────────────────┐
│ "Prefers TypeScript over Python for tools" │
│ 🗣 you said · used 3d ago · salience ▓▓▓░  │
│                     [ edit ] [ delete ]    │
└────────────────────────────────────────────┘
```
- **Provenance chip is the star:** 🗣 *you said* vs ✨ *Halo inferred* — visually distinct, because the system treats them differently (inferences can never overwrite your statements — the provenance rule made visible).

## Interactions
- **Edit** inline → saves as a new user-stated belief superseding the old (the strongest provenance).
- **Delete** → soft; an "undo" toast for 5s, then archived — recoverable from the archived filter **indefinitely** (nothing hard-deletes itself, per the memory design; permanent removal is a separate explicit action inside the archive).
- **Superseded history:** cards with a past show a small ⌄ — expanding reveals the chain ("used to think X → corrected 12 Jun"). Any old version restorable in one click.
- **Archived view** (filter): decayed beliefs (salience < 0.2) rest here, restorable — decay is visible, never mysterious disappearance.

## Moments of delight
- When Halo auto-corrects a belief mid-conversation, a quiet peek line: "updated what I remember — you switched to pnpm." Click = jump to the card.
- Empty state: "I don't remember anything yet — I only keep what matters, and you'll always see it here."

## Trust rule
Nothing in memory is hidden, and nothing hard-deletes without you. The panel is the proof behind "autonomous correction is safe."
