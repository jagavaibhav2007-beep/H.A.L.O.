# System Design: Coding-Agent Orchestration

Halo directs Codex/Claude to build, continue, refactor, debug — including improving Halo itself. Lane 1 via CLI.

## Responsibility
- Brief a coding agent, run it, monitor it, report back what changed. Not by clicking the desktop apps — via their **CLIs**.

## Mechanism
- The Brain spawns the coding-agent **CLI** as a subprocess in the target project dir, streams its output, and parses results.
- LangGraph models this as a long-running tool node with its own checkpoints (so a coding run is interruptible/resumable like any task).

```
"add feature X to project P"
  → Brain gathers context (status, relevant files) [Lane 1]
  → briefs coding-agent CLI in P
  → streams progress → narrates main events
  → coding agent edits files (Tier 2, inside project)
  → Brain summarizes diff → reports back
```

## Typical flow (from PRD)
1. "Check status of project P" → Brain reads repo/files, reports.
2. "Add this feature" → Brain briefs the coding agent, monitors, reports the diff.

## Self-improvement boundary
- Coding agent editing a **normal project** → Tier 2 (notify).
- Coding agent editing **Halo's own core code** → Tier 3 (ask), per [permissions](04-permissions.md). New *skills* are the exception (autonomous) — see [self-improvement](08-self-improvement.md).

## Failure handling
- Coding run errors/tests fail → Brain captures the output, reports honestly ("tests failed, here's why"), offers to retry with a refined brief. Never reports success it didn't verify.
- Writes a failure post-mortem to memory for next time.

## Cost note
- Orchestration reasoning uses the light model; the coding agent runs on its own model/subscription. See [techstack/07](../techstack/07-coding-orchestration.md).
