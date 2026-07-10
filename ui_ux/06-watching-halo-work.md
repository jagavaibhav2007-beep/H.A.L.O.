# Halo UI/UX: Watching Halo Work

The supervision experience for the three lanes, browser runs, and coding agents. Sources: [systemdesign/05-computer-control](../systemdesign/05-computer-control.md), [06-browser](../systemdesign/06-browser.md), [07-coding-orchestration](../systemdesign/07-coding-orchestration.md).

## The lane chip (status strip, always visible during a task)
- 🟦 **Fast** — "working programmatically"; nothing moves on your screen.
- 🟨 **Takeover** — "driving your mouse"; the loudest state in the app.
- 🟪 **Sandbox** — "working in the box"; you keep your machine.
Halo announces the lane when a task starts ("I'll need to drive for this one") and you can pin a different lane from the chip's dropdown.

## Lane 2 — Takeover etiquette (the trust-critical moment)
- Before taking the cursor: a 3-second banner — "**Taking over in 3… — move the mouse to cancel**." You always get a veto.
- While driving: a slim top-edge banner persists — "Halo is driving · [ ⏸ Pause ] [ ⏹ Stop ]" — plus spoken/peek narration of main steps. Your physical mouse movement = instant pause (hardware-level trust: you always win the cursor).
- On finish: banner flips to "done — here's what I did" linking the activity entries.

## Lane 3 — Sandbox window
- The Tasks view hosts a **live stream tile** (~2fps, from `stream_frame` events) of the VM desktop. Click = enlarge; it's watchable, not interactive (a "take control" affordance can come later).
- Deferred out of MVP with the VM decision — the tile design ships, the lane doesn't.

## Browser runs
- Visible mode by default: the dedicated Halo Chrome window is real and watchable; the feed logs DOM-level actions.
- **Playbook transparency:** warm-path replays are labeled in the feed ("replayed 'expense report' routine — $0"), and every mutating step still throws its approval card ([05-permissions-trust](05-permissions-trust.md)). Cost savings never look like hidden behavior.

## Coding agent runs
- A task card per run: project name, elapsed, live tail of the agent's output (mono, collapsed to 6 lines, expandable), then the **diff summary** — files changed, +/- counts, one-paragraph plain-English summary.
- Failures are shown verbatim ("tests failed: 2") with Halo's read and a "retry with refined brief" action — never a false green.

## The Activity feed (the flight recorder)
- One virtualized timeline of everything, newest first: icon + sentence + tier chip + timestamp + lane. Undo button where an inverse exists; "not reversible" mark where it doesn't.
- Filters: tier, lane, task, undoable-only. Search over the raw log. This is the PRD's "running log I can review" made into a first-class view.
