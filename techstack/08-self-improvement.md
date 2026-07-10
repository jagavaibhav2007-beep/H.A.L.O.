# Tech Stack: Self-Improvement & Skills

Design: [systemdesign/08-self-improvement](../systemdesign/08-self-improvement.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| Skill format | markdown file + optional Python helper | `skills/<name>.md` |
| Frequency detector | SQL query over the raw activity log | counts task-kind occurrences |
| Skill drafting | light model | drafts from successful runs |
| Sandbox eval | run skill in isolated context on held-out/simulated inputs | capability + regression check |
| Registry | on-disk `skills/` dir loaded at Brain start + hot-reload | files, not a DB |
| Retirement | success-rate tracking in SQLite | auto-retire on repeated failure |

## Cost note
- **Detection = free** (local SQL).
- **Drafting + eval = a few light-model calls per new skill** — infrequent (only fires on a genuinely repeated task), so negligible ongoing cost.
- Once a skill exists, using it can *reduce* cost (fewer planning tokens for that task).

## Boundary
- New skill = autonomous (tested, reversible). Core-code / relied-on-skill edits = Tier 3.
