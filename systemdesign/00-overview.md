# Halo — System Design: Overview

Companion to [Halo-PRD.md](../Halo-PRD.md). This folder holds one design doc per feature; this file is the backbone they all plug into. Tech choices live in [`../techstack/`](../techstack/00-stack-summary.md).

## Process model

Halo runs as **three resident processes** on the laptop plus local stores. Nothing is a server you dial into; it's an app.

```
┌────────────────────────── LAPTOP ──────────────────────────┐
│                                                             │
│  UI PROCESS ◄──WebSocket──► BRAIN PROCESS ◄──WS──► VOICE     │
│  Tauri+React               Python·LangGraph        WORKER    │
│  (glass UI, panels,        (control loop,          Python·   │
│   approval prompts)         router, gate,          Pipecat   │
│                             tools, memory)         +openWW    │
│                                   │                          │
│              ┌────────────────────┼───────────────────┐      │
│              ▼                    ▼                    ▼      │
│   SQLite + sqlite-vec      skills/*.md         OS keystore    │
│   (memory, tasks,          (self-made          (API keys)    │
│    activity log)            skills)                          │
└─────────────────────────────────────────────────────────────┘
       │ cloud egress (only these leave the machine)
       ▼
  OpenRouter (LLM + Whisper STT) · Deepgram (Aura TTS) · MCP servers · Codex/Claude CLIs
```

| Process | Responsibility | Talks to |
|---|---|---|
| **UI** | Render floating→expandable window and all panels; show approvals, activity feed, memory/task/skill views | Brain (WebSocket) |
| **Brain** | The agent. LangGraph control loop, model routing, permission gate, tool execution, memory, skills, task state | UI, Voice, OpenRouter, tools, MCP, CLIs |
| **Voice** | Wake word → capture → STT → hand text to Brain → speak reply via TTS; barge-in/interruption | Brain (WebSocket), OpenRouter STT, Deepgram TTS |

Processes communicate over **local WebSocket** (loopback only). If Brain dies, UI shows "reconnecting"; Voice buffers the last utterance.

## The control loop (LangGraph)

Every task is a run through one graph:

```
perceive → route model → plan → [permission gate] → execute tool → checkpoint → narrate → loop → done
```

- **Checkpoint after every node**, persisted to SQLite. This is what makes tasks **resumable** and **interruptible** ("stop → what should I do differently? → resume from here").
- **`interrupt()`** is fired by the permission gate on Tier-3 actions and by an explicit user "stop." The graph suspends, state is saved, and it resumes on approval/redirect.

## Cross-cutting systems (each has its own doc)

| System | Doc | One-liner |
|---|---|---|
| Permission gate | [04-permissions](04-permissions.md) | Single choke point; Tier 3 → `interrupt()` |
| Memory | [03-memory](03-memory.md) | 3 tiers: session / curated beliefs / raw log; decay + auto-correct |
| Model router | see techstack | Light model default, escalate to heavy on reasoning gaps |
| Skill lifecycle | [08-self-improvement](08-self-improvement.md) | Frequency → generate → sandbox-eval → activate/retire |
| Control lanes | [05-computer-control](05-computer-control.md) | Fast / Takeover / Sandbox, chosen per task |
| IPC contract & lifecycle | [11-ipc-contract](11-ipc-contract.md) | canonical WS schema, process launch/auth, concurrency, cancellation — **the Phase-1 build target** |

## Design principles

1. **One enforcement point, not scattered guards.** Permissions, model routing, and memory writes each have a single module every path routes through.
2. **Local-first data.** Only prompt text/audio and tool payloads leave the machine (to OpenRouter/Deepgram/MCP). Memory, logs, skills, keys stay on disk.
3. **Fast path by default.** Programmatic tools before GUI automation; light model before heavy; escalate only on a clear gap.
4. **Everything is inspectable.** Activity log, memory, and skills are all files/rows the user can view and undo/edit.

## Build phases (from PRD §13)

1. **UI shell** — the three-process skeleton with mocked Brain responses; all panels render.
2. **Backend spine** — real Brain: chat + memory + permission gate + activity log.
3. **Heavy systems** — voice, browser, GUI lanes, coding orchestration, self-improvement.
