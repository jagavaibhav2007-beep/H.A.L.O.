# Patterns
_Established code patterns and conventions for this project._

## Single-source-of-truth IPC contract with a drift check — 2026-07-10
`shared/ipc-contract.json` is the canonical schema (message types + required fields). `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py` are hand-mirrored from it. `shared/check_contract_sync.py` fails the build if TS/Python/schema diverge. Run it after editing any of the three. Chosen over codegen for simplicity at this scale.

## Backoff/pure-logic extraction for testability — 2026-07-10
`ui/src-tauri/src/supervisor.rs`'s `backoff_delay(attempt) -> Option<Duration>` is a standalone pure function (1s/5s/30s/None) covered by one deterministic `#[test]`, separate from the actual process-spawning loop that calls it. Prefer extracting the pure decision logic out of anything that touches processes/IO so it's unit-testable without spawning.

## Subagent dispatch: model-by-complexity + independent verification — 2026-07-10
Standing workflow for this project: assess a step's complexity, dispatch Sonnet for standard work / Fable or Opus for harder problems, always instruct ponytail-mode explicitly in the subagent prompt (subagents don't inherit session-level mode), and independently re-verify every subagent claim (re-read the diff, re-run the test/build myself) before reporting done — never just relay the subagent's self-report. Call the `advisor` tool before dispatching a subagent for anything with a non-obvious failure mode (race conditions, OS-specific behavior) to get the trap list into the brief up front.

## Full-window hit-area + visually-constrained child, for transparent/borderless windows — 2026-07-12
`ui/src/orb/OrbRoot.tsx`: a `.orb-hit-area` div fills the entire (possibly transparent, possibly non-square) window and owns all pointer handlers (drag, resize-edge detection, click, context menu); the visible `.orb` glass circle is a plain centered child, sized in JS to `min(window width, height)` via `ResizeObserver`, with no pointer logic of its own. Reusable whenever a window's visible content doesn't fill its own bounds (letterboxing, a shape constraint, an aspect-locked graphic) but must still be draggable/resizable/clickable across its full extent — putting interaction handlers on the visual element alone leaves the "empty" transparent margins dead to input. Step 7 (orb state language) inherits this hit-area/visual split as-is; only the visual child's contents change.

## Build gates prove compilation, not behavior — 2026-07-11/12
Four real bugs across two sessions (Voice routing, orb size/centering, edge-resize-vs-drag conflict, mock missing `task_op`) all shipped through fully green `tsc`/`cargo build`/selfcheck runs. None was catchable by a type or unit check — they were runtime/visual/cross-process facts only observable by launching the actual app. Standing rule for this project: a green build is necessary but never sufficient for anything with a visual or cross-process runtime surface — budget an actual `./dev.ps1 -Mock` run before calling a UI/IPC-facing step done, and say plainly when you could not rather than implying the build gates covered it.

## When a subagent/advisor call fails to spawn, do the review yourself rather than block — 2026-07-12
Mid-session the Agent tool briefly failed ("claude-opus-4-8 is temporarily unavailable... auto mode cannot determine the safety of Agent") when dispatching a review subagent. Rather than retry-loop or skip the review, read the same files directly with Read/Grep/Bash and perform the review inline — read-only tools aren't gated by that classifier. Independent verification (re-reading diffs, re-running tests) was already the standing practice for *trusting* a subagent's report; this extends it to the case where a subagent can't be dispatched at all. Don't silently drop a requested review step just because the delegation mechanism hiccuped.
