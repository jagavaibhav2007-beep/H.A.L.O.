# System Design: API & MCP Integrations

Prefer authenticated APIs/MCP servers; fall back to browser/GUI automation when none exists.

## Responsibility
- Give Halo real tool access to external services (email, calendar, notes, etc.) through the cleanest available channel.

## Preference order (per task)
```
1. Authenticated API / MCP server exists?  → use it (Lane 1, structured, reliable)
2. else → browser automation (signed-in Chrome, hard rule applies)
3. else → GUI automation (Lane 2/3)
```

## MCP integration
- The Brain runs MCP servers as tool providers; their tools appear in the Tool Executor alongside native tools and pass through the **same permission gate** (e.g. an email "send" tool is Tier 3).
- Example: "check my email" → email MCP `search`/`get` = Tier 1; `send` = Tier 3.

## Credentials
- API keys / OAuth tokens stored in the **OS keystore**, never in plaintext config or memory. Redacted in the activity log.

## Adding integrations
- New MCP servers/APIs can be registered (config). This is capability-expansion, so registering one that grants send/spend powers is surfaced to the user, not silent.

## Failure handling
- API/MCP error or auth expiry → report clearly, offer re-auth; fall back to browser only if the user approves for that task.
- Never fabricate a result when an integration call fails.

## Cost note
- Most integration calls are non-LLM (direct API); LLM cost is only the surrounding reasoning. See [techstack/09](../techstack/09-integrations-mcp.md).
