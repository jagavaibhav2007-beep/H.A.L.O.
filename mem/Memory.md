# Memory
_Current project state, active goals, and key context._

- H.A.L.O.: local resident desktop AI companion — Tauri+React UI, Python/LangGraph Brain, Python/Pipecat Voice, per Halo-PRD.md.
- Public repo: github.com/jagavaibhav2007-beep/H.A.L.O. — no secrets, standalone commit messages, small reviewable commits.
- **Phase 0 (Skeleton & Contract) is COMPLETE** — all 8 steps built, independently verified, committed and pushed (commits `6e1e3c8`, `4c9048e`). Three processes spawn, authenticate over WS, and recover from Brain crashes via backoff+respawn.
- Toolchain fully working on this machine: Rust/cargo 1.97.0 + MSVC Build Tools 2022 (C++ workload) — native Tauri builds and `cargo tauri dev` confirmed working end to end.
- `mem/` memory docs initialized this session (2026-07-10).
- Phase 0 was reliability-hardened on 2026-07-10: malformed active IPC fields are rejected, unauthenticated sockets time out, `hello_ack` gates application sends, a crash-safe lock prevents competing Brain instances, queued UI sends survive interrupted flushes, Tauri's spawn/shutdown race is closed, and `dev.ps1` no longer launches duplicate workers.
- Native Tauri lifecycle was re-verified on 2026-07-10: killing Brain caused Brain and Voice to restart on a new port and re-authenticate; graceful window close removed the Tauri, Brain, and Voice processes.
- Working pattern established: assess step complexity → dispatch Sonnet (standard) or Fable/Opus (complex) subagent → always independently re-verify subagent claims → call `advisor` before dispatching anything with non-obvious failure modes.
- **Phase 1 (Front-end shell) is IN PROGRESS.** Full plan: `phase-1-plan.md` (15 steps). Roadmap pointer: `phases.md`.
  - **Done, committed, and live-tested (2026-07-11 → 2026-07-12):**
    - **Step 1** — contract additions: `undo` (inbound), `belief_state`/`skill_state` (outbound, snapshot+delta), optional fields on `task_state`/`approval_request`/`activity`. 24 message types, all three mirrors in sync.
    - **Step 2** — mock Brain (`python -m brain --mock`): scripted `demo …` scenarios, snapshot-on-connect, reactive approval await-points. Ships with **role-based routing** (`hello.role:"ui"|"voice"`) so Voice only ever gets its contract subset — see Bugs.md.
    - **Step 3** — design tokens (`ui/src/styles/tokens.css`), glass recipe + reduced-transparency/motion fallbacks, primitives (`GlassPanel`/`Button`/`Chip`/`Icon`).
    - **Step 4** — UI event store: pure `applyFrame` reducer + zustand wrapper, replay-tested via `reducer.selfcheck.ts`.
    - **Step 5** — orb + workspace window architecture: two Tauri windows, global hotkey (`Alt+Space`, falls back to `Ctrl+Alt+Space`), manual pointer-drag with free placement (no edge-snap — reversed after user testing, see Decisions.md), **orb is user-resizable** (circle always locks to `min(width,height)` via `ResizeObserver`, native `startResizeDragging` for edge/corner handles since the window is borderless), tray + context menu, quit preserves the Phase-0 shutdown-flag-before-kill ordering. **User-confirmed working live** on 2026-07-12 (hotkey, workspace expand, resize, drag, all fine).
  - **Not started:** Steps 6–15 (workspace shell/sidebar, orb state language, and all 6 panel views — chat/activity/approvals/tasks/memory/skills+settings — plus voice presence and the phase E2E pass). Step 6 (sidebar, status strip, view routing) is next.
- Two bugs were found only by *running the real stack*, not by green tests — see Bugs.md. Standing lesson: for anything with a visual/runtime surface, an automated gate passing is not sufficient evidence; launch the actual app.
