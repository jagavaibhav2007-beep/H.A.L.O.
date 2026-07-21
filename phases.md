# H.A.L.O. — Build Phases

The build roadmap. This is the **order** we build in and the **definition of done** for each phase — not the implementation detail. When we start a phase, ask for a phase-specific implementation plan (e.g. "make the Phase 1 plan") and it gets written then, against the docs current at that point.

Source of truth for *what* each thing does stays in [Halo-PRD.md](Halo-PRD.md), [systemdesign/](systemdesign/00-overview.md), [techstack/](techstack/00-stack-summary.md), and [ui_ux/](ui_ux/00-design-language.md). This file only sequences them.

## Guiding rules for the whole build

1. **Real and testable early, then made to actually work.** Feel first (mocked), spine next (honest core), heavy systems last. Straight from PRD §13.
2. **Build the spine before the limbs.** The honest core is *chat + memory + local file control + permission/log*. Browser, coding-agent orchestration, voice, sandbox lane, and self-improvement layer on **after** that spine holds (PRD §13 note).
3. **One choke point, not scattered guards.** Permission gate, model router, and memory writes each land as a single module everything routes through (overview §Design principles). Build the choke point before the paths that use it.
4. **Local-first.** Only prompt text/audio + tool payloads leave the machine. Memory, logs, skills, keys stay on disk — true from Phase 2 onward.
5. **Contract-driven.** UI and Brain are built against the fixed WS envelope in [systemdesign/11-ipc-contract](systemdesign/11-ipc-contract.md). Mocked Brain and real Brain emit the *same* message shapes, so Phase 1 → Phase 2 is a swap, not a rewrite.

---

## Phase 0 — Skeleton & contract (the plumbing)

**Goal:** three processes exist and talk, authenticated, over the real message envelope. Nothing intelligent yet.

**Build:**
- Tauri (UI parent) spawns Brain + Voice as sidecar processes ([11-ipc](systemdesign/11-ipc-contract.md) §Process lifecycle).
- Brain binds a random loopback port, writes `{port, token}` to `%LOCALAPPDATA%\Halo\session.json`; UI + Voice read it.
- `{type:"hello", token}` auth handshake on every WS connection; wrong/missing token → drop.
- Shared IPC message-type definitions (the envelope + the inbound/outbound tables) as the single source both sides import.
- Sidecar supervision: Tauri watches exits, restarts with 1s/5s/30s backoff, then surfaces an error state.

**Depends on:** nothing.
**Done when:** UI sends a `user_msg` over authenticated WS; a stub Brain echoes a `token`+`done` back; killing Brain shows "reconnecting" and it restarts.
**Stack:** Tauri + React (UI), Python (Brain/Voice stubs), local WebSocket. See [techstack/10-ui](techstack/10-ui.md).

---

## Phase 1 — Front-end shell (the feel)

**Goal:** the full premium UI, every surface rendering against a **mocked** Brain that replays scripted IPC events. Establishes the look and motion before any real logic.

**Build (all mocked):**
- Floating companion window → expand into full workspace (Spotlight/Raycast-style), global hotkey summon, always-on-top.
- Core surfaces: **chat**, **activity feed**, **memory panel**, **task view**, **skills panel**, **lane indicator** ([ui_ux/](ui_ux/00-design-language.md), PRD §12).
- Design language applied for real: glass tokens, baby-blue/royal-blue, motion tokens, reduced-transparency + reduced-motion fallbacks, keyboard path ([ui_ux/00-design-language](ui_ux/00-design-language.md)).
- Mock Brain script drives token streaming, activity events, approval cards, task states, voice-state orb — all via the Phase-0 envelope.

**Depends on:** Phase 0 (the contract + a process to send mock frames).
**Done when:** every panel renders and animates from scripted events; the app *feels* done end-to-end with zero real backend; approval cards, undo affordances, and the lane indicator all display correctly.

---

## Phase 2 — Backend spine (the honest core)

**Goal:** replace the mock Brain with a real one for the safe, useful core. This is the first genuinely usable Halo.

**Build, in dependency order:**
1. **LangGraph control loop** — `perceive → route → plan → [gate] → execute → checkpoint → narrate → loop`, checkpointed to SQLite, resumable/interruptible ([overview](systemdesign/00-overview.md), [01-chat](systemdesign/01-chat.md)).
2. **Model router** — light-by-default, escalate-on-gap, via OpenRouter ([techstack/00](techstack/00-stack-summary.md)).
3. **Permission gate** — the single choke point; Tier-3 → `interrupt()` → `approval_request` → `approval_response` ([04-permissions](systemdesign/04-permissions.md)).
4. **Memory** — 3 tiers (session / curated beliefs / raw log), SQLite + sqlite-vec, local embeddings, decay + auto-correct; wired to the memory panel ([03-memory](systemdesign/03-memory.md)).
5. **Activity log + undo** — every action logged, reviewable, undoable where possible; `undoable:false` shown honestly.
6. **Local computer control (Lane 1 only)** — file read/create/edit/move/organize, read-only commands, under the gate's tiers.
7. **Secrets** — OS keystore via `keyring`, no plaintext keys.

**Depends on:** Phase 1 (surfaces to bind to), Phase 0 (transport). Gate before the tools that trip it; memory store before the panel is live.
**Done when:** real chat with streamed replies; memory that persists, self-corrects, and is editable in the panel; Tier-1/2/3 actions behave per the tiers with real approval round-trips; local file ops work; activity log + undo real; everything but LLM calls stays on-device.

---

## Phase 3 — Heavy systems (make it JARVIS)

**Goal:** the ambitious capabilities, layered on the proven spine. Each is independently shippable — sub-order by value, ship one at a time.

**Build (suggested order — each its own mini-phase):**
- **3a — Coding-agent orchestration.** Codex/Claude CLIs as subprocesses, Lane 1, cooperative cancellation ([07-coding-orchestration](systemdesign/07-coding-orchestration.md)). Highest-value, all Lane 1, no GUI brittleness.
- **3b — Browser automation.** Playwright over CDP → real Chrome profile; learn-once-replay-free playbooks; browser hard rule (submit/send/buy/post = confirm) ([06-browser](systemdesign/06-browser.md)).
- **3c — Voice.** Pipecat pipeline, openWakeWord, Whisper STT, Deepgram TTS; barge-in, narration of main events, "stop" → redirect → resume ([02-voice](systemdesign/02-voice.md)).
- **3d — GUI automation (Lanes 2 & 3).** Windows UI Automation → vision fallback; Lane 2 takeover, Lane 3 sandbox; live desktop stream ([05-computer-control](systemdesign/05-computer-control.md)). **Lane 3 sandbox deferred out of MVP** (PRD §15 — needs Win Pro or VirtualBox).
- **3e — Self-improvement / skill loop.** Frequency-detect → generate → sandbox-eval → activate/retire; learn-from-failure post-mortems; skills panel trial/keep/kill ([08-self-improvement](systemdesign/08-self-improvement.md)).
- **APIs & MCP integration** threads through 3a–3b (prefer authenticated API/MCP, fall back to browser/GUI) ([09-integrations-mcp](systemdesign/09-integrations-mcp.md)).

**Depends on:** Phase 2 (control loop, gate, memory, log all required by every item here).
**Done when:** each sub-phase's feature works under the same gate + log + checkpoint guarantees the spine established.

---

## Explicitly deferred (not in early build)

- **Lane 3 sandbox** — until Win 11 Pro upgrade or a VirtualBox setup (PRD §15).
- **Phone/push notifications** — out of scope; native Windows toast only (PRD §5).
- **Decay/retirement threshold tuning** — ship the proposed defaults, tune with real usage (PRD §15).

## How to use this file

- **Phase 0 is done** (skeleton + IPC contract, hardened with hello_ack/queueing) — see [phase-0-plan.md](phase-0-plan.md). **Phase 1 is done** — see [phase-1-plan.md](phase-1-plan.md). **Phase 2 is done** (2026-07-21, Steps 1–10; automated gate green, native checklist in VERIFY.md still pending a human run with a real key) — see [phase-2-plan.md](phase-2-plan.md). **Phase 3 is next.**
- To start a phase: "let's start Phase N" → a `phase-N-plan.md` gets written with concrete tasks, file layout, and interfaces, grounded in the then-current docs.
- If a phase reveals a design gap, fix the relevant `systemdesign/`/`techstack/`/`ui_ux/` doc first, then continue — this roadmap follows the docs, not the other way around.
