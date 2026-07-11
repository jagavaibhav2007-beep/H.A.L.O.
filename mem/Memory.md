# Memory
_Current project state, active goals, and key context._

- H.A.L.O.: local resident desktop AI companion — Tauri+React UI, Python/LangGraph Brain, Python/Pipecat Voice, per Halo-PRD.md.
- Public repo: github.com/jagavaibhav2007-beep/H.A.L.O. — no secrets, standalone commit messages, small reviewable commits.
- **Phase 0 (Skeleton & Contract) is COMPLETE** — all 8 steps built, independently verified, committed and pushed (commits `6e1e3c8`, `4c9048e`). Three processes spawn, authenticate over WS, and recover from Brain crashes via backoff+respawn.
- Toolchain fully working on this machine: Rust/cargo 1.97.0 + MSVC Build Tools 2022 (C++ workload) — native Tauri builds and `cargo tauri dev` confirmed working end to end.
- `mem/` memory docs initialized this session (2026-07-10).
- Phase 0 was reliability-hardened on 2026-07-10: malformed active IPC fields are rejected, unauthenticated sockets time out, `hello_ack` gates application sends, a crash-safe lock prevents competing Brain instances, queued UI sends survive interrupted flushes, Tauri's spawn/shutdown race is closed, and `dev.ps1` no longer launches duplicate workers.
- Native Tauri lifecycle was re-verified on 2026-07-10: killing Brain caused Brain and Voice to restart on a new port and re-authenticate; graceful window close removed the Tauri, Brain, and Voice processes.
- Active goal: Phase 0 is complete and hardened; Phase 1 has not started.
- Working pattern established: assess step complexity → dispatch Sonnet (standard) or Fable/Opus (complex) subagent → always independently re-verify subagent claims → call `advisor` before dispatching anything with non-obvious failure modes.
