# Patterns
_Established code patterns and conventions for this project._

## Single-source-of-truth IPC contract with a drift check — 2026-07-10
`shared/ipc-contract.json` is the canonical schema (message types + required fields). `ui/src/ipc/contract.ts` and `brain/brain/ipc/contract.py` are hand-mirrored from it. `shared/check_contract_sync.py` fails the build if TS/Python/schema diverge. Run it after editing any of the three. Chosen over codegen for simplicity at this scale.

## Backoff/pure-logic extraction for testability — 2026-07-10
`ui/src-tauri/src/supervisor.rs`'s `backoff_delay(attempt) -> Option<Duration>` is a standalone pure function (1s/5s/30s/None) covered by one deterministic `#[test]`, separate from the actual process-spawning loop that calls it. Prefer extracting the pure decision logic out of anything that touches processes/IO so it's unit-testable without spawning.

## Subagent dispatch: model-by-complexity + independent verification — 2026-07-10
Standing workflow for this project: assess a step's complexity, dispatch Sonnet for standard work / Fable or Opus for harder problems, always instruct ponytail-mode explicitly in the subagent prompt (subagents don't inherit session-level mode), and independently re-verify every subagent claim (re-read the diff, re-run the test/build myself) before reporting done — never just relay the subagent's self-report. Call the `advisor` tool before dispatching a subagent for anything with a non-obvious failure mode (race conditions, OS-specific behavior) to get the trap list into the brief up front.
