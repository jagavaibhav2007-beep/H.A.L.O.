# System Design: Browser Automation

Drive the user's real, signed-in Chrome. A Lane-1 (programmatic) tool, not pixel-clicking.

## Responsibility
- Navigate, read pages, fill forms, and act on the web **as the user** (their logins), under the browser hard rule.

## Mechanism
- **Playwright connected over CDP** to a Chrome launched with the user's profile + a debug port.
- Uses a **dedicated Halo Chrome window** on that profile (not hijacking an already-open window) to avoid conflicts.
- **Known constraint:** Chrome 136+ blocks `--remote-debugging-port` on the *default* user-data-dir. Plan: a **dedicated Halo profile** (copy of the user's profile, or a fresh one the user signs into once) launched with the debug port. One-time login cost; sessions then persist in that profile.

```
Brain browser tool → Playwright(CDP) → Chrome(user profile) → page
   → read DOM/text back to Brain
```

## Browser hard rule (see permissions)
- read / navigate / hover / scroll → **Tier 1** (free).
- click that **submits a form, sends, buys, or posts** → **Tier 3** (confirm), always.
- Enforced in this tool's wrapper: actions are tagged `mutating:true` and the wrapper forces the gate before dispatching them.
- **Conservative-by-default classifier** (a JS `onclick` that POSTs is not detectable from markup alone): any click inside a `<form>`, on an element with submit/button semantics, or that triggers navigation/XHR → treated as mutating → gated. False positives (an extra confirmation) are acceptable; false negatives (an unconfirmed purchase) are not.

## Observability
- Optional visible window so the user can watch; DOM-level actions also logged as activity events.
- Screenshots captured on mutating steps for the audit log.

## Cost architecture: learn once, replay free
The naive loop (read page → LLM → click → read → LLM → …) is the most expensive pattern in agentic browsing. Halo uses a **three-rung ladder** per browser task — always try the cheapest rung first:

```
task arrives
  → 🟢 WARM: matching playbook exists? → validate (no LLM) → replay via Playwright → $0
  → 🟡 GROUND: playbook step broken? → light model re-grounds just that step (~200-token snapshot)
  → 🔴 COLD: no playbook / re-ground failed → full agent loop (heavy model) → SAVE new playbook
```

**Playbooks (the warm path)**
- First successful run of a task shape is recorded as a **playbook**: an ordered action list keyed by `hash(domain, task_template)`. Steps store **role + accessible name** (e.g. `button "Submit order"`), not brittle CSS/XPath.
- Replays execute directly through Playwright — **zero LLM calls**. Cache-hit runs drop from ~$0.05–$1.00 and 30–120s to ~$0 and 3–10s (browser-use's published numbers for this exact pattern).
- A playbook **is a skill** — it lives in the skills registry and inherits everything from [08-self-improvement](08-self-improvement.md): success-rate tracking, auto-retirement (<50% over ≥5 uses), the skills panel, notify-on-create.

**Self-healing (the grounding path)**
- Before replay: cheap non-LLM validation (element-exists / DOM-fingerprint check).
- One broken step ≠ cold restart: the **light model** re-grounds only that step against a fresh snapshot, playbook is patched in place. Full agent re-run only if that fails — capped at 1 retry, then surface to user.

**Page representation (every rung)**
- Default: **accessibility-tree snapshot**, interactive elements only (~200–400 tokens/page) — never raw DOM (thousands) or screenshots to a vision model (~3–5k tokens, ~10× dearer).
- Vision screenshots are the exception, only for canvas/no-ARIA UIs, and count as a heavy-model call.

**Permission gate is replay-proof**
- Playbook replay still passes every step through the mutating-click wrapper — a cached "submit/buy/send" step triggers the Tier-3 gate **on every run**. Caching optimizes cost, never bypasses consent.

## API/MCP-first
- If a task has an authenticated API/MCP (e.g. email), the Brain prefers that ([integrations](09-integrations-mcp.md)); browser automation is the fallback when no integration exists.

## Failure handling
- Selector/element missing → re-read page, retry once, then ask rather than guess.
- Login wall / captcha → stop and surface to user (never attempt to defeat it).
- Failure writes a post-mortem to memory ("popup blocked submit; dismiss first") per [self-improvement](08-self-improvement.md).
