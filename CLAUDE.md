# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is the **design/planning repo for Halo** — a local, resident desktop AI companion (Tauri + React UI, Python/LangGraph brain, Python/Pipecat voice worker). There is **no source code here yet**: it is pre-implementation. The repo is three layers of markdown that must stay in sync, plus a top-level PRD:

- **[Halo-PRD.md](Halo-PRD.md)** — product spec: *what* Halo is and *how it behaves* (capabilities, control lanes, permissions, memory, self-improvement). Deliberately stack-agnostic.
- **[systemdesign/](systemdesign/00-overview.md)** — one doc per feature describing the *architecture* that implements the PRD (process model, control loop, cross-cutting systems). Start at `00-overview.md`.
- **[techstack/](techstack/00-stack-summary.md)** — the concrete technology choice per feature, layered on top of `00-stack-summary.md` (the global stack table).
- **[ui_ux/](ui_ux/00-design-language.md)** — the visual/interaction spec (design tokens, motion, copy voice) that every screen inherits from `00-design-language.md`.

Each folder numbers files by feature (`01-chat`, `02-voice`, `03-memory`, `04-permissions`, `05-computer-control`, `06-browser`, …) — the same number in `systemdesign/`, `techstack/`, and `ui_ux/` covers the same feature from architecture, technology, and UI angles respectively. When changing one, check whether the matching file in the other two folders needs to move with it.

## Working in this repo

- This is a **docs-only repo** — there is no build, lint, or test command because there is no code. If/when implementation starts, this file should be updated with real commands.
- Treat the PRD as the source of truth for *behavior*; `systemdesign/` and `techstack/` must not contradict it. If a design decision changes a PRD claim, update the PRD too.
- Keep tech choices out of `Halo-PRD.md` and the PRD's stack-agnostic framing out of `techstack/` — the split is intentional (see the PRD's own header note).
- Before editing UI-facing docs, check `ui_ux/00-design-language.md` for existing tokens (color, spacing, motion, type scale) instead of inventing new ones — same reuse rule as code: don't reintroduce something that already has a name here.

## Repo conventions (this is a public open-source repo)

- **Never commit or hardcode secrets** (API keys, tokens, credentials) in any file, including examples in the design docs — use placeholders like `<YOUR_API_KEY>` or reference an env var / OS keystore by name instead.
- Before staging or pushing, re-check `git status`/`git diff` for anything that looks like a real key, token, or personal path/credential — this repo is public, so anything committed is permanently visible in history.
- Commit messages and PR descriptions should stand on their own for outside contributors: explain *why*, avoid internal shorthand, and don't assume the reader has this conversation's context.
- Prefer small, reviewable commits over large mixed ones — an open-source repo's history is documentation too.

## Picking a skill/plugin

Before doing non-trivial work, check whether an available skill or agent already fits the task (e.g. planning → `ecc:plan`; docs sync → `ecc:update-docs`; UI/UX work → `ui-ux-pro-max`) rather than solving it from scratch — this workspace has skills disabled granularly in `.claude/settings.local.json`, so check there before assuming one is unavailable.
