# Halo — Product Requirements Document

**A local, human-like desktop AI companion**
Version 0.1 · Owner: Vaibhav · Date: 2026-07-09
Status: Concept locked, pre-implementation

> This PRD defines *what* Halo is and *how it behaves* — product, experience, capabilities, workflows, and limits. It deliberately does **not** choose a tech stack. Build order is phased (UI shell → backend → system design) but the technology for each phase is decided later.

---

## 1. Vision

Halo is a resident AI companion that lives on your laptop and acts as a **second brain and operating layer** for your computer. When your laptop is on and Halo is open, it can reason, remember, control your machine, drive your apps and browser, orchestrate coding agents, and improve itself over time. It should feel like a calm, capable, JARVIS-style presence — always there, learning your habits, getting better the more you use it.

Halo is not a chatbot with plugins bolted on. Its defining trait is the **learning loop**: it remembers what matters, notices repeated work and past failures, and turns them into reusable skills — so every task makes the next one faster.

---

## 2. What "local" means (and its honest limits)

**Local = Halo is a resident app on your machine, not a website.**

- Halo's brain (control loop), memory, skills, permission system, and activity log all live **on-device**. They run only while your laptop is on and Halo is open.
- **All your data stays on-device**, except the text/audio Halo sends to cloud **LLM and voice providers** in order to think and speak.
- "Offline when the laptop is off" means Halo is a program, not a cloud service — **not** that it works without internet. Heavy reasoning and realtime voice require cloud APIs and therefore a connection.

**Explicit limitation to state up front:** Halo is only as smart as the cloud models it calls, and it needs internet for its most capable modes. This is an accepted tradeoff in exchange for JARVIS-level intelligence rather than a weaker fully-offline model.

---

## 3. Core capabilities (feature stack)

1. **Chat** — a text interface for questions, planning, discussion, and answers.
2. **Realtime voice** — natural spoken conversation, cloud-powered.
3. **Local computer control** — inspect, create, edit, move, and organize files and apps.
4. **Browser automation** — drive Chrome using your existing signed-in profile.
5. **App automation** — operate installed desktop apps (editors, terminals, Claude/Codex apps, etc.).
6. **Coding-agent orchestration** — direct Codex/Claude to build, continue, refactor, debug, and improve projects — including improving Halo itself.
7. **Self-improvement** — learn from repeated tasks and failures; autonomously create tested, reusable skills.
8. **API & MCP integration** — prefer authenticated APIs/MCP servers; fall back to browser/GUI automation when none exists.
9. **Memory & context** — a curated, self-maintaining memory that powers deeper, context-aware reasoning.
10. **Premium UI/UX** — minimal, Apple-like, baby-blue/royal-blue glassmorphism.

---

## 4. How Halo touches the computer — the three control lanes

Halo has three ways to act. It picks the fast, safe one by default and only escalates when needed. **It always tells you which lane it used.**

### Lane 1 — Fast (default)
Invisible, programmatic execution: file operations, CLIs, APIs, MCP servers. **Codex and Claude orchestration runs here** — Halo talks to them through their CLI/API, not by clicking their windows. No cursor is taken; **you keep using your machine.** You observe through the live activity feed, not by watching a mouse.

### Lane 2 — Takeover (GUI on your real desktop)
For GUI-only tasks that need your **real signed-in apps/logins**. Halo drives your actual cursor and keyboard; **you wait and watch.** Used only when there's no faster path, or when you explicitly ask to watch it work on your real machine.

### Lane 3 — Sandbox (isolated desktop you can watch)
A separate virtual desktop/VM with its own cursor and keyboard. Halo works there while **you keep working on your real machine**; you click over to watch a live stream anytime. Requires a **one-time sign-in of your apps inside the sandbox** (a VM does not inherit your real logins).

### Selection rules
- **Default:** Lane 1 whenever a task has an API/CLI path.
- **Auto-escalate** to a GUI lane only when there is no other way in — and announce which lane.
- **Manual override:** you can pin a lane per task ("do this in the sandbox so I can watch," or "take over my screen, I'll wait").
- Halo does **not** ask which lane every time — it decides, states its choice, and you correct if you care.

**Known tradeoff (stated, not solved away):** concurrency-and-isolation (Lane 3) and real-signed-in-apps (Lane 2) cannot both be true for the same GUI action. You choose per task.

---

## 5. Permissions & trust

Every action falls into one of three tiers.

| Tier | Behavior | Examples |
|------|----------|----------|
| **Tier 1 — Silent** | Just do it, log it | Read files, search, open apps, **draft** (not send), read-only commands, retrieve memory |
| **Tier 2 — Notify but proceed** | Do it, surface it | Create/edit/move files **inside project folders**, run a coding agent on a project, non-destructive browsing |
| **Tier 3 — Always ask** | Stop and request approval | Delete/overwrite anything; send email/messages; spend money or check out a cart; change account/system settings; install software; **edit Halo's own core code or an existing relied-on skill** |

### Browser hard rule
In the signed-in browser, Halo may **read and navigate freely**, but **any click that submits a form, sends, buys, or posts requires explicit confirmation.** No exceptions, even inside an approved task.

### Audit & undo
- A **reviewable running log** of everything Halo did ("here's everything I did today").
- **Undo** wherever technically possible.

### Away behavior
- If a Tier-3 approval is needed while you're away, Halo **pauses silently and waits** — it does not guess or skip ahead.
- It fires a **native desktop notification** (Windows toast — free, on-device, no cloud service) so you know it's waiting.
- No phone/push notifications in scope (would require a paid service).

---

## 6. Memory

Halo's memory is **curated, short, and high-signal** — only details that matter, not a transcript of everything.

- **Capture:** curated, not exhaustive. Halo keeps concise, important facts (preferences, active projects, workflows, key decisions, failure lessons), not every message or click.
- **Auto-correction:** Halo **autonomously updates stale or wrong beliefs** as it learns new information (e.g. a shipped project stops being "in progress").
- **Decay:** infrequently-used memories **fade over time** so Halo isn't drowning in old context. Frequently-relevant memories persist.
- **Inspectable & editable:** a **memory panel** lets you see exactly what Halo believes about you and your work, and delete or correct any entry. Because everything is visible and restorable, autonomous correction is safe rather than destructive.
- **Deeper thinking mode:** for harder tasks, Halo reasons *using* relevant past context (projects, preferences, prior failures) instead of treating the task as brand-new.

---

## 7. Self-improvement & skills

Halo gets better by turning repeated work into reusable skills.

- **Autonomous skill creation:** when Halo notices you do a specific kind of task **frequently**, it creates a reusable skill for it on its own — no prompting required. It **notifies** you when it does ("made a new skill for X").
- **Additive & reversible:** a *new* skill is low-risk — it's new, tested, visible in your skills panel, and killable. This is why it can be autonomous even though self-modification is otherwise Tier 3.
- **Boundary (Tier 3 stays Tier 3):** editing Halo's **own core code**, or modifying an **existing skill you already rely on**, still requires your approval. New skills = autonomous; touching its guts = ask.
- **Testing gate:** every self-made skill is **sandbox-tested / trial-run before going live**, and **quietly retired if it keeps failing** — so a bad skill can't lock in a repeated mistake.
- **Learning from failure:** when a task fails, Halo writes a short **post-mortem to memory** (e.g. "browser task failed because a login popup blocked it; dismiss it first next time") and **pulls those lessons up before retrying similar tasks.**
- **No junk accumulation:** decay + retirement-on-failure keep the skill/memory set lean.

---

## 8. Coding-agent orchestration

Halo can direct Codex/Claude to build features, continue projects, refactor, debug, and improve apps — **including improving Halo itself** (subject to the Tier-3 rule on its own core code).

- Runs in **Lane 1 (Fast)** via CLI/API — reliable and quick — not by clicking the desktop app windows.
- Typical flow: you ask Halo to check a project's status → it reports → you say "add this feature" → Halo briefs the coding agent, monitors it, and reports back what changed.
- Edits land as Tier-2 (notify) inside project folders; anything destructive or self-modifying escalates to Tier-3.

---

## 9. APIs & MCP

- Halo **prefers authenticated APIs and MCP servers** when they exist (e.g. checking email via an email API/MCP).
- When no integration exists, it **falls back to browser/GUI automation** — still bound by the browser hard rule (submit/send/buy/post = confirm).

---

## 10. Voice

- **Wake word / name: "Halo."** Distinctive enough to avoid constant false triggers (unlike a common word). "Halo" is also its identity across the UI and its system prompt.
- **Realtime, natural** spoken conversation (cloud-powered).
- **Narration:** while working, Halo narrates **main events only** ("opening the project now…", "coding agent finished, 3 files changed") — **not** every click or keystroke.
- **Interrupt & redirect:** you can say **"stop"** at any time → Halo halts immediately, asks **"what should I do differently?"**, takes your input, and **resumes from where it left off** (requires task checkpointing — see §11).

---

## 11. Task supervision

- **Interruptible:** voice or typed "stop" halts a running task immediately.
- **Redirectable:** after stopping, Halo asks how to adjust, then continues.
- **Resumable / checkpointed:** long tasks track their own progress so Halo can pick up mid-task after an interruption or a pause-for-approval. *(Required capability, not free — called out for the build.)*
- **Away/paused tasks:** on a Tier-3 gate while you're away, Halo pauses, notifies, and holds state until you return.

---

## 12. UI / UX direction

- **Form factor:** a **floating companion window** that's always present and glanceable, and **expands into a full desktop-app workspace** (chat, live activity log, memory panel, task view, skills panel) when opened/enlarged. Spotlight/Raycast-style expansion.
- **Aesthetic:** premium, minimal, modern, **Apple-like**. Soft, refreshing **baby-blue gradients**, **royal-blue accents**, **frosted glassmorphism**, smooth interactions, clean layout. Never chunky, cluttered, or generic.
- **Core surfaces:**
  - **Chat** — primary conversation.
  - **Activity feed** — the live "what Halo is doing" stream (main events), and the reviewable historical log with undo.
  - **Memory panel** — inspect/edit what Halo believes.
  - **Task view** — running/paused tasks, their state, and approval prompts.
  - **Skills panel** — see, trial, keep, or kill self-made skills.
  - **Lane indicator** — always shows which control lane is active.

---

## 13. Phased scope

Build in phases so the product is real and testable early, then made to actually work behind the glass.

- **Phase 1 — Front-end shell.** The premium UI/UX: floating→expandable window, chat, activity feed, memory/task/skills panels, lane indicator — with mocked behavior. Establishes the feel.
- **Phase 2 — Backend wiring.** Connect chat + memory + permission tiers + the activity log/undo to real logic. The safe, useful spine.
- **Phase 3 — Heavy system design.** The control loop, the three control lanes, browser/app automation, coding-agent orchestration, voice, and the self-improvement/learning loop.

*(Feature-priority note for Phase 2/3: the honest core is **chat + memory + local file/computer control + permission/log system**; browser automation, coding-agent orchestration, voice, sandbox lane, and self-improvement layer on after that spine holds.)*

---

## 14. Limitations & accepted tradeoffs (stated plainly)

1. **Not truly offline.** Best reasoning and realtime voice need cloud APIs and internet. "Local" = resident app + on-device data, not air-gapped.
2. **Data leaves for the brain.** Text/audio sent to LLM/voice providers is not on-device; everything else is.
3. **Cursor takeover is real.** Lane 2 uses your actual mouse/keyboard — you're blocked while it runs. This is honest, not hidden.
4. **Sandbox vs. logins can't both win.** Lane 3 gives concurrency but needs its own app sign-ins; Lane 2 has your logins but takes your screen.
5. **GUI automation is brittle.** It's the fallback, not the default — used only when no API/CLI/MCP path exists.
6. **Cost & dependency.** Cloud LLM/voice usage has ongoing cost and depends on provider availability.
7. **Self-improvement is bounded.** New skills are autonomous but tested and reversible; core-code changes always require approval.

---

## 15. Open items (to decide before/within build)

- **Summoning:** confirmed a floating, enlargeable window; opened via a **global hotkey**.
- **Cloud LLM/voice providers:** resolved — OpenRouter (Gemma-4-26b light / DeepSeek-v4-pro heavy), Whisper-large-v3-turbo STT, Deepgram Aura-1 TTS; on-device openWakeWord handles the always-on wake word.
- **Sandbox/VM for Lane 3:** proposed — this machine is Win 11 Home (no Hyper-V/Windows Sandbox), so **Windows Sandbox if upgraded to Pro, else VirtualBox**; Lane 3 deferred out of MVP. See [systemdesign/05-computer-control](systemdesign/05-computer-control.md).
- **Decay/retirement thresholds:** proposed defaults set (memory: start 0.6, +0.2 on use, ×0.5/30d, archive <0.2; skills: create ≥5×/14d, retire <50% over ≥5 uses). Tune during build. See [03-memory](systemdesign/03-memory.md) / [08-self-improvement](systemdesign/08-self-improvement.md).
