# Tech Stack: Browser Automation

Design: [systemdesign/06-browser](../systemdesign/06-browser.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| Driver | **Playwright (Python)** | mature, reliable |
| Connection | **CDP** (`connect_over_cdp`) | attaches to real Chrome |
| Profile | **dedicated Halo profile** (user signs in once) + `--remote-debugging-port` | Chrome 136+ blocks the debug port on the *default* profile dir |
| Window | dedicated Halo window on that profile | avoids clobbering open tabs |
| Page reads | Playwright DOM / `inner_text` | Tier 1 |
| Mutating acts | wrapper tags `mutating:true` → forces Tier-3 gate | submit/send/buy/post |
| Screenshots | Playwright capture on mutating steps | audit trail |

## Cost stack (learn-once / replay-free — see design doc)
| Rung | Mechanism | LLM cost |
|---|---|---|
| 🟢 Warm | own playbook layer: JSON action list (role + accessible name), replayed via raw Playwright | **$0** |
| 🟡 Ground | light model (`gemma-4-26b-a4b-it`) re-grounds one broken step from an a11y snapshot | ~cents |
| 🔴 Cold | **browser-use** (MIT, Python, ~81k★) as the first-encounter agent loop; heavy model | first run only |

- Page representation: **accessibility-tree snapshot, interactive-elements-only** (~200–400 tokens) — never screenshots (~3–5k tokens) unless the UI has no ARIA semantics.
- Playbook store: rows in the skills registry (SQLite + `skills/` dir) — reuses 08's success tracking and retirement, no new infrastructure.
- Validation before replay: element-exists / DOM-fingerprint check — non-LLM, free.

### Licensing (why these and not others)
- **browser-use: MIT** ✅ — safe to embed; also ships its own cache-script feature we can lean on for the cold→warm handoff.
- Skyvern / workflow-use (**AGPL**) and Notte (**SSPL**) — good ideas, wrong licenses to embed; their techniques (route memorization, hybrid script+LLM) are copied conceptually, not as dependencies.
- Stagehand (MIT but TypeScript) — its action-cache design (selector + DOM-hash validation, ~48h validity) informed the playbook validation scheme; not a runtime dependency.

## Cost note
- **DOM reads/navigation = free/local** (no LLM needed to read a page's text).
- Repeat tasks (the common case for a daily assistant) run at **$0 LLM cost** on the warm path; only novel task shapes pay the agent-loop price, once.

## Note
- Prefer an **API/MCP** for a task when one exists ([09-integrations-mcp](09-integrations-mcp.md)); browser is the fallback.
