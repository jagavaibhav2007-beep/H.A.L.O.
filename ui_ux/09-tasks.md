# Halo UI/UX: Tasks View

Everything in flight, with truthful progress and cancellation. Sources: [systemdesign/00-overview](../systemdesign/00-overview.md) (checkpoints), [11-ipc-contract](../systemdesign/11-ipc-contract.md) (`task_state`), and [12-task-runtime](../systemdesign/12-task-runtime.md).

## Layout
- Task cards, active first, then waiting-approval (amber), paused, stopped/failed history, and recent-done (last 24h, collapsed).
```
┌─────────────────────────────────────────────┐
│ ⏳ Reorganizing Downloads · 🟦 Fast          │
│ ▸ step 4/9 — moving PDFs                    │
│ [ ⏸ pause ] [ ⏹ stop ]                      │
└─────────────────────────────────────────────┘
```
- Waiting-approval cards embed their approval card directly. Paused cards show *why* ("you said stop", "waiting for approval", "Brain restarted — resumed safely").

## Working and stopping feedback

- While detached work is active, the workspace status strip is a polite live
  region with a spinner, `Running`/`Queued`/`Stopping`, the task title, and
  `step/total` progress. A subtle transform-only sheen on determinate task bars
  makes forward motion visible without causing layout shifts.
- Stop locks immediately and sends once. The same focusable button becomes
  `Stopping…` with `aria-disabled=true` until the Brain confirms a terminal
  snapshot, preserving keyboard focus while preventing duplicate requests.
- `Stopped` is neutral history, not a red failure. Its card preserves the last
  confirmed step and removes all actionable controls.
- `prefers-reduced-motion: reduce` disables the spinner and sheen animations;
  state words and numerical progress remain sufficient without motion or color.

## Resumability made visible
- Pause keeps its step position, and **Resume** picks up from the checkpoint. The card says so: "will continue from step 4." Stop is terminal and keeps the last confirmed position as history; it does not imply resumability.
- After a crash/restart, tasks reappear in paused state with a "resumed safely" note — recovery is a visible feature, not silent magic.

## Lane 3 stream tile
- Sandbox tasks host their live desktop stream inside the card ([06-watching-halo-work](06-watching-halo-work.md)).

## Empty state
"Nothing running. Ask me for something long — you can watch it work here, pause it, or walk away."
