# Halo — Tech Stack Summary

The global stack. Per-feature files list only what's *specific* to that feature on top of this. Design lives in [`../systemdesign/`](../systemdesign/00-overview.md).

## Backbone
| Concern | Choice | Why (cheap / efficient / quality) |
|---|---|---|
| **App shell** | **Tauri** (Rust core + web UI) | ~10× lighter RAM than Electron; native tray + notifications; fits an always-resident app |
| **UI** | **React** + CSS (glassmorphism) | biggest component ecosystem for the premium look |
| **Brain** | **Python + LangGraph** | provider-agnostic agent loop; **checkpointer** = resumable tasks; **`interrupt()`** = Tier-3 gate for free |
| **Model access** | **OpenRouter** (one API key, many models) | provider-agnostic routing; per-task cost control |
| **Voice worker** | **Python + Pipecat** (local audio transport) | realtime STT→LLM→TTS pipeline, no media server, no Docker; barge-in built in |
| **Wake word** | **openWakeWord** | on-device, free, open; keeps audio local until "Halo" |
| **Memory store** | **SQLite + sqlite-vec** | on-device, free, fast vector search |
| **Embeddings** | **local** (fastembed: `bge-small-en` / MiniLM) | free, private, no API per retrieval |
| **Browser** | **Playwright** over **CDP** → real Chrome profile | uses your logins; scriptable; reliable |
| **GUI automation** | **Windows UI Automation** (`pywinauto`/`uiautomation`) → **vision + `pyautogui`** fallback | element-based first, pixels only when forced |
| **Coding agents** | **Codex / Claude CLIs** as subprocesses | reliable Lane-1 orchestration, no cursor |
| **Secrets** | OS keystore (Windows Credential Manager via `keyring`) | no plaintext keys |
| **IPC** | local WebSocket (loopback) | simple, language-agnostic between the 3 processes |

## Models (OpenRouter) — verified 2026-07
| Role | Model ID | Status |
|---|---|---|
| **Heavy** (reasoning, code, planning) | `deepseek/deepseek-v4-pro` | ✅ confirmed on OpenRouter |
| **Light** (classify, narrate, memory extract) | `google/gemma-4-26b-a4b-it` | ✅ confirmed live on OpenRouter — **paid variant** ($0.06/M in, $0.33/M out), not the rate-limited `:free` |
| **STT** | `openai/whisper-large-v3-turbo` (OpenRouter audio endpoint) | ✅ confirmed live |
| **TTS** | Deepgram **Aura-1** (Deepgram API, not OpenRouter) | as chosen |

## Cost strategy (from agentic-engineering skill)
- **Default light, escalate on gap.** Most calls (routing, narration, memory extraction, simple chat) → light model. Escalate to heavy only when the task shows real reasoning/coding depth.
- **Local where free:** wake word, embeddings, memory search, file ops, GUI, browser DOM reads — no API cost.
- **Browser: learn once, replay free.** First encounter of a web task runs the agent loop (browser-use, MIT); the successful path is saved as a **playbook** (a skill) and replayed via raw Playwright at **$0 LLM cost** thereafter, with light-model self-healing for single broken steps. A11y-tree snapshots (~200–400 tokens), never screenshots, as the default page view. See [06-browser](06-browser.md).
- **Cloud only for:** LLM reasoning (OpenRouter), STT (OpenRouter), TTS (Deepgram). These are the only recurring bills.

## What leaves the machine
Prompt text + tool payloads → OpenRouter; audio after wake → STT; reply text → Deepgram TTS; MCP/API calls to their services. **Everything else (memory, logs, skills, keys) stays local.**
