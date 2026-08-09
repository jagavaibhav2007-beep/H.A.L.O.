# Tech Stack: Permissions & Trust

Design: [systemdesign/04-permissions](../systemdesign/04-permissions.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| Enforcement | LangGraph node wrapping the Tool Executor | single `classify()` choke point |
| Pause/resume | LangGraph **`interrupt()`** + checkpointer | Tier-3 suspends, resumes on approval |
| Approval UI | React approval card over WebSocket | Approve / Deny / Edit |
| Away notify | Always-on-top floating companion | expands with Approve / Deny / Review and stays pending |
| Audit log | SQLite `action` table | all tiers logged |
| Undo | inverse-op tokens per action | file move-back, delete-created, etc. |

## Cost note
- **Zero recurring cost** — all local. Classification is rule-based (no LLM call) for known tools; unknown tools default to Tier 3.
- Approval notification stays local in the companion; no paid push service or OS toast is used.
