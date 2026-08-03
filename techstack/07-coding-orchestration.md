# Tech Stack: Coding-Agent Orchestration

Design: [systemdesign/07-coding-orchestration](../systemdesign/07-coding-orchestration.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| Agents | **Codex CLI**, **Claude CLI** | driven as subprocesses in the project dir |
| Spawn/stream | Python `asyncio` subprocess | stream stdout, parse results |
| As a task | Existing Brain `TaskRuntime` + `TaskContext` | Separate from interactive turn slots; bounded logs; cooperative terminate→kill |
| Durability | Intent/result task rows + reconcile-on-startup | Interrupted subprocesses become truthful terminal results; never auto-replay side effects |
| Repo context | git + file reads (Lane 1) | status/diff gathering |
| Diff summary | light model | summarize what changed |

## Cost note
- **Halo's orchestration reasoning = light model** (cheap): briefing, monitoring, summarizing.
- **The coding agent itself** runs on *its own* model and billing (Codex/Claude subscription or API) — not through OpenRouter. This keeps Halo's own LLM spend low; the heavy lifting is on the coding agent's plan.

## Boundary
- Editing normal projects = Tier 2; editing **Halo's own core code** = Tier 3 (ask). New skills are the autonomous exception ([08-self-improvement](../systemdesign/08-self-improvement.md)).
