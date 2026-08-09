# Halo UI/UX: Permissions & Trust

The consent surface — where autonomy meets your veto. Rules source: [systemdesign/04-permissions](../systemdesign/04-permissions.md).

## How the tiers feel
| Tier | You experience |
|---|---|
| 1 Silent | nothing — it's in the Activity feed if you ever look |
| 2 Notify | a quiet peek-bubble line / feed entry; work never pauses |
| 3 Ask | amber: orb ring, sidebar badge, and **the approval card** |

## The approval card
Anchored bottom-center of the current view (or inline in chat when the task lives there):
```
┌──────────────────────────────────────┐
│ ⚠ Send email to raj@company.com      │  ← one plain sentence, no jargon
│   "Q3 report attached — see notes…"  │  ← the payload, truncated, expandable
│   ▸ details (tool, full args)        │  ← redacted args, expandable
│                                      │
│  [ Approve ]   [ Edit ]   [ Deny ]   │
└──────────────────────────────────────┘
```
- **Approve** = primary blue. **Deny** = ghost, never red (denying is safe, not destructive). **Edit** opens the args inline for correction before approving.
- **Money / irreversible-external** variants: `--destructive` red border, the sentence states the amount/consequence in bold, **voice approval disabled** — click only (user decision). A 700ms hold-to-approve on the button prevents reflex clicks.
- Card waits forever; the task stays checkpointed. Saying "stop" cancels it as an implicit deny (per [IPC rules](../systemdesign/11-ipc-contract.md)) and the card vanishes.

## Floating companion approval

The companion expands from 360×52px to 360×224px for the oldest pending
request. It presents only the summary, tool, count, and Approve, Deny, and
**Review details** controls: arguments remain in the workspace. Review details
opens that card for full arguments and Edit. Each resolution advances to the
next pending request without collapsing; the final resolution restores the
compact pill.

Approve and Deny are disabled while disconnected, while Review details still
opens the workspace. Destructive companion approvals use the same 700ms hold
as the card. Requests wait for an explicit response forever: no timeout
approval and no focus theft.

## When you're away
Task pauses silently → Windows toast: "Halo is waiting for your OK." Clicking it opens the workspace focused on the card. The orb stays amber the whole time — walking past your desk tells you something's waiting.

## Undo & the audit trail
- Every action lands in the **Activity feed** with its tier chip. Undoable ones show **Undo** for as long as the inverse exists; irreversible ones are marked "not reversible" *before* you rely on them (`undoable:false` from the IPC contract — the UI never implies false safety).
- The browser hard rule surfaces plainly: any submit/send/buy/post click shows its approval card **every time**, even inside an approved task, even on a cached playbook replay.

## Trust design principles
1. Consent UI is calm, not alarming — amber attention, red only for genuinely destructive/money.
2. The safe choice (Deny) is always one obvious click and never punished with nagging.
3. Nothing consequential is ever announced only by sound, and nothing is ever approved by timeout.
