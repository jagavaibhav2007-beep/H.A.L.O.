# System Design: Self-Improvement & Skills

Halo turns repeated work into tested, reusable skills, and learns from failures.

## Skills are files
- A skill = a markdown file (`skills/<name>.md`) with a description + steps/prompt + optional helper code. The Brain loads active skills into its tool/prompt space.

## Lifecycle
```
frequency detector (raw log) → "task kind T done N times"
  → generate skill.md (light model drafts from the successful runs)
  → SANDBOX EVAL (run skill on held-out/simulated inputs)
  → pass → activate + notify ("made a skill for T")
  → fail → discard (or shelve as draft)
active skill keeps failing in real use → auto-retire
```

### Thresholds (proposed defaults — tune against real usage)
- **Create trigger:** task-kind repeats **≥5× in a rolling 14 days**.
- **Go-live gate:** pass sandbox eval on **≥3 held-out/simulated inputs** (notify-only, never silent).
- **Auto-retire:** rolling success rate **<50% over ≥5 uses**, OR **3 consecutive failures** → auto-disable + notify (user can restore).

## Autonomy boundary (reconciles PRD)
- **Creating a new skill = autonomous** — it's additive, sandbox-tested, visible in the skills panel, killable. Notify, don't ask.
- **Editing Halo's own core code, or an existing relied-on skill = Tier 3 ask.**

## Eval-first (from agentic-engineering skill)
- Each candidate skill has a **capability eval** (does it do the task) and a **regression check** (does activating it break anything). No eval pass → no activation.

## Isolation (what "sandbox eval" actually means)
- Skill helper code **never runs in the Brain process**. Evals — and live execution of helper code — run in a **separate subprocess** with a restricted working dir and no access to the Brain's keystore or WS token; results come back over stdout. (This is process isolation, *not* the Lane-3 GUI VM — different "sandbox," and deliberately independent of it since Lane 3 is deferred.)
- Markdown-only skills (prompts/steps, no code) carry no execution risk and just load into the prompt space.
- A helper that needs powers beyond the restricted subprocess (network, arbitrary paths) loses the autonomy exemption → **Tier 3 ask**.

## Learning from failure
- On task failure the Brain writes a short **post-mortem belief** (`kind: failure_lesson`): what failed + the fix ("browser popup blocked submit → dismiss first").
- Before retrying a similar task, retrieval surfaces matching failure_lessons so the plan avoids the known trap.

## No junk accumulation
- Decay (memory) + retire-on-failure (skills) keep both sets lean. Skills panel shows usage + success rate; user can kill any.

## Inspectable
- **Skills panel**: list, trial-run, keep, or kill each skill; see which were auto-made vs user-made.

## Failure handling
- Skill generation/eval harness error → no activation, logged; Halo just keeps doing the task the manual way.
