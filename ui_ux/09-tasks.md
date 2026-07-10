# Halo UI/UX: Tasks View

Everything in flight, pausable and resumable. Sources: [systemdesign/00-overview](../systemdesign/00-overview.md) (checkpoints), [11-ipc-contract](../systemdesign/11-ipc-contract.md) (`task_state`).

## Layout
- Task cards, running first, then waiting-approval (amber), paused, recent-done (last 24h, collapsed).
```
┌─────────────────────────────────────────────┐
│ ⏳ Reorganizing Downloads · 🟦 Fast          │
│ ▸ step 4/9 — moving PDFs                    │
│ [ ⏸ pause ] [ ⏹ stop ]                      │
└─────────────────────────────────────────────┘
```
- Waiting-approval cards embed their approval card directly. Paused cards show *why* ("you said stop", "waiting for approval", "Brain restarted — resumed safely").

## Resumability made visible
- Pause/stop never loses work — the card keeps its step position, and **Resume** picks up from the checkpoint. The card says so: "will continue from step 4." This is the checkpointer's promise, surfaced as UX.
- After a crash/restart, tasks reappear in paused state with a "resumed safely" note — recovery is a visible feature, not silent magic.

## Lane 3 stream tile
- Sandbox tasks host their live desktop stream inside the card ([06-watching-halo-work](06-watching-halo-work.md)).

## Empty state
"Nothing running. Ask me for something long — you can watch it work here, pause it, or walk away."
