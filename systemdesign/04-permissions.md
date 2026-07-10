# System Design: Permissions & Trust

One choke point every tool call passes through. Maps PRD tiers onto a LangGraph interrupt.

## The gate
Every tool invocation is classified **before execution**:

| Tier | Action | Behavior |
|---|---|---|
| 1 Silent | read, search, open app, draft, read-only cmd, memory read | run, log |
| 2 Notify | create/edit/move in project folders, run coding agent, non-destructive browse | run, surface event |
| 3 Ask | delete/overwrite, send msg/email, spend money/checkout, change account/system settings, install software, edit Halo's own core code / a relied-on skill | **`interrupt()` → approval** |

Classification is a single function `classify(tool, args) -> tier`, unit-tested. No per-tool scattered checks.

## Tier-3 flow
```
tool call → classify → Tier 3
   → LangGraph interrupt() (state checkpointed)
   → if user present: UI approval card (Approve / Deny / Edit)
   → if away: pause silently + native desktop notification
   → on approve: resume graph from checkpoint
   → on deny: graph takes the "denied" branch, continues what it can
```

## Voice approval (user decision)
- Tier-3 requests may be approved/denied **by voice** ("approve" / "deny") — **except money or irreversible-external actions** (spend/checkout, account changes): those require a physical click on the approval card, always. Voice announces them but cannot confirm them. UX: [ui_ux/05-permissions-trust](../ui_ux/05-permissions-trust.md).

## Browser hard rule (overrides tiers)
In the signed-in browser: read/navigate = Tier 1. **Any click that submits, sends, buys, or posts = Tier 3**, always, even inside an approved task. Enforced in the browser tool wrapper, not left to classification.

## Audit & undo
- Every action (all tiers) writes to the raw activity log: `action(id, tool, args_redacted, tier, result, undo_token, ts)`.
- `undo_token` records the inverse where possible (moved file → move back; created file → delete). Undo surfaced in the activity feed.

## Away detection
- No **user-generated** input for 5 minutes (proposed default, tunable) → "away." Tier-3 gates then pause + notify instead of blocking silently.
- Input injected by Halo itself (Lane-2 takeover is driving the mouse/keyboard) **doesn't count as user presence** — detection uses last *physical* input (`GetLastInputInfo`) minus Halo-injected events, so a takeover session doesn't mask the user actually being away.

## Failure handling
- Unknown/unclassifiable tool → **default to Tier 3** (fail safe, ask).
- Notification system unavailable → still pause; show the pending approval on next UI focus.
