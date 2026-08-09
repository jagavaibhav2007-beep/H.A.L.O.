# System Design: Coding-Agent Orchestration

Halo directs Codex/Claude to build, continue, refactor, debug — including improving Halo itself. Lane 1 via CLI.

## Responsibility
- Brief a coding agent, run it, monitor it, report back what changed. Not by clicking the desktop apps — via their **CLIs**.

## Mechanism
- The Brain spawns the coding-agent **CLI** as a subprocess in the target project dir, streams its output, and parses results.
- The shared subprocess boundary is now implemented by `command_run` /
  `script_run`: normalized argv, executable-identity binding, deterministic
  tier policy, Job Object containment, bounded redacted output, and artifact
  verification. Codex/Claude adapters must compose this executor rather than
  adding another subprocess path.
- The permission gate classifies the request before side effects, then submits a task-shaped tool to the implemented [TaskRuntime](12-task-runtime.md). The interactive LangGraph turn returns after reporting the task start; it never owns the subprocess lifetime.
- `TaskContext.log()` carries coalesced output to the bounded `task_log` tail. Cooperative stop terminates the subprocess and escalates to kill within the runtime's halt budget.
- Restart reconciliation reports an interrupted run truthfully and never blindly replays it. A future agent adapter may resume only when that CLI exposes a durable, explicitly verified resume token.

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
- A missing CLI, non-zero exit, cancellation, or Brain restart produces a durable terminal task result; it is not represented as a successful conversation checkpoint.
- Writes a failure post-mortem to memory for next time.

## Cost note
- Orchestration reasoning uses the light model; the coding agent runs on its own model/subscription. See [techstack/07](../techstack/07-coding-orchestration.md).
