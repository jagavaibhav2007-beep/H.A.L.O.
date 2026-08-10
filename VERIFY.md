# Native Verification (Phases 1–2)

Status: **Phase 1 COMPLETE** — automated gate passed, native checklist
user-confirmed complete on 2026-07-13.
Status: **Phase 2 COMPLETE (declared 2026-08-01).** The feature set and
2026-07-31 exit-hardening implementation (durable TaskRuntime, security/reconnect
fixes) are in place and automated-gate-verified. The checklist items below —
human visual, keyboard/NVDA, minimum-size, and real-OpenRouter-key checks —
remain unchecked and are still worth running, but are recommended follow-ups
rather than a blocker on Phase 3 work. Historical audit reports were retired
after remediation; their evidence remains available in git history and `mem/`.

Run the full automated gate first. Use `./dev.ps1 -Mock` for the scripted
Phase-1 visual scenarios (`demo ...`). Use the normal `./dev.ps1` launcher for
the real Phase-2 Brain and real OpenRouter verification; it intentionally does
not respond to mock demo triggers.

## Automated gates

- [x] Run `./dev.ps1 -Verify` from the repository root. This is the full
      automated gate: IPC contract sync, every Brain and Voice Python test,
      UI self-checks, Vitest, the UI production build, Rust tests, and all
      three phase protocol checks.
- [x] Confirm the final line says `FULL AUTOMATED VERIFICATION PASSED`
      (2026-07-27: all Python/Voice scripts, five TS self-checks, Vitest 32/32,
      production build, Rust 7/7, and Phase 0/1/2 gates).
- [x] Confirm both gate scripts parse in Windows PowerShell and resolve Python
      3.11+ via `python`, `py -3`, or a discoverable bundled Codex runtime.
- [x] During focused protocol work, `./dev.ps1 -Smoke` may be used as the
      faster Phase 0/1/2 protocol-only check. A green `-Smoke` is not a
      substitute for `-Verify` before declaring the repository gate green.
- [x] Re-run the full repository gate on the integrated tree and record success
      (2026-08-03, `d505231`: local full verification passed; GitHub Actions run
      30801830739 passed the same locked Phase 0–2 gate).
- [x] Re-ran the full gate for batched task completion/cancellation on
      2026-08-10: contract sync reported 34 schemas, every Python/Voice suite
      passed, five UI self-checks passed, Vitest passed 91/91, the production
      build and 11 Rust tests passed, and all Phase 0/1/2 protocol gates passed.
- [x] The Phase 2 `doc_digest` gate exercises a folder/glob batch with one good
      and one broken PDF, observes multiple progress snapshots on one task id,
      preserves the structured per-file failure, and receives exactly one
      content-bearing assistant conclusion after the batch becomes terminal.
- [x] Browser-mode rendered QA (2026-08-10) confirmed animated Running progress,
      immediate `Stopping…`, focus retained on the inert stopping control,
      neutral Stopped history, no 640px viewport overflow, and no console
      warnings/errors. Reduced-motion behavior is covered by the explicit CSS
      fallback and component tests; native assistive-technology review remains
      in the human checklist below.

## Render matrix

Repeat `demo everything` in each mode. Text and controls must remain readable,
focus must remain visible, and no panel may clip at the minimum window size.

- [ ] Light theme, normal motion/transparency.
- [ ] Dark theme, normal motion/transparency.
- [ ] Light theme, reduced motion and reduced transparency.
- [ ] Dark theme, reduced motion and reduced transparency.
- [ ] Theme changes apply immediately to both the orb and workspace windows.

## Demo everything

- [ ] The reply streams into one assistant bubble and completes cleanly.
- [ ] The task advances through named steps with a visible Fast lane chip.
- [ ] The regular Tier-3 approval supports Approve, Deny, and Edit without double-fire.
- [ ] Undo changes from `Undoing...` to `Undone` only after confirmation.
- [ ] The destructive approval has a red treatment and requires a 700 ms hold.
      Releasing or moving away early cancels it; holding Enter or Space works.
- [ ] The memory correction supersedes npm with the user-stated pnpm belief.
- [ ] The `weekly-report-formatter` skill appears as auto-learned.
- [ ] Voice moves wake -> listening -> partial ghost -> final user bubble ->
      thinking -> speaking -> barge-in listening -> speaking -> idle.
- [ ] The orb shows task, approval, voice, error, and muted states without color alone.
- [ ] A pending approval expands the floating pill from 360×52px to 360×224px;
      resolving the final request collapses it without stealing focus.
- [ ] A regular floating approval can Approve and Deny directly; its summary,
      tool, and count remain legible with long copy and it exposes no unredacted
      arguments.
- [ ] A destructive floating approval requires the visible 700ms hold. Release
      early, leave the control, then retry and confirm that only the completed
      hold responds.
- [ ] With multiple pending approvals, the oldest appears first and each
      response advances to the next without an intermediate collapse.
- [ ] **Review details** opens the workspace approval card, including arguments
      and Edit.
- [ ] Test the pill at monitor edges and across mixed-DPI monitors: expansion
      remains within the active work area and restores sensibly when collapsed.
- [ ] Disconnect/reconnect with an approval pending: Approve/Deny are disabled
      while disconnected, Review details remains available, and controls resume
      after reconnect without a duplicate response.
- [ ] With reduced motion enabled, the expansion/collapse and destructive hold
      remain understandable without non-essential animation.

## Keyboard and accessibility

- [ ] Summon the workspace with the registered global hotkey.
- [ ] Tab and Shift+Tab follow visual order; every control has a visible focus
      ring and an understandable accessible name.
- [ ] `Ctrl+K` opens Chat and focuses the composer from every panel.
- [ ] `Esc` collapses the workspace to the orb without quitting.
- [ ] Complete Approve, Deny, Edit, destructive hold, and Stop using only the keyboard.
- [ ] At 200% text scaling, essential content and controls remain available.
- [ ] Reduced motion removes non-essential movement without hiding state changes.
- [ ] Reduced transparency makes every glass surface opaque and readable.

## Performance and recovery

- [ ] Run `demo flood`; all 2,000 entries arrive and scrolling/filtering stays responsive.
- [ ] While away from newest activity, new events do not move the viewport; the
      new-activity pill returns to newest.
- [ ] During a streamed reply, kill Brain. Orb/chat show reconnecting and the
      current turn becomes interrupted rather than staying animated.
- [ ] Submit text while disconnected; the queued badge says it will send later.
- [ ] After Tauri restarts Brain on a new port, the queued turn sends once, the
      badge clears, and snapshot-backed state does not duplicate.
- [ ] Quit from the tray; no Tauri, Brain, or Voice process remains.
- [x] Force-kill the Tauri parent; its exact Brain and Voice children are reaped
      (2026-07-22 app-scoped run: UI 28628, Voice 10088, Brain 35148; all gone
      within three seconds). Tray-quit remains a separate human interaction check.

## Phase 2 native checklist

The full automated gate (`./dev.ps1 -Verify`) includes the Phase 0 -> Phase 1 ->
Phase 2 protocol checks. Those protocol checks prove the real Brain's paths
offline, against `HALO_LLM_STUB`/`HALO_EXTRACT_STUB`.
It cannot prove a real model reply looks right, a real router escalation reads as
intended, or that the UI's rendering of a real multi-step task/undo/memory round-trip
holds up outside a script. Run the plain (non-`-Mock`) app with a real OpenRouter key
set in Settings for all of the below.

All validated P0/P1 audit findings are resolved. Real task controls now execute
through the durable, bounded `TaskRuntime`; unsupported pause remains an exact
correlated error. Tasks have cooperative progress/stop, restart reconciliation,
bounded logs, per-move organize receipts, and one atomic batch undo. Authority
separation, admission bounds, reconnect/turn correlation, dependency locking,
and project-root repair are implemented. The checks below still require a
human and, for provider behavior, a real key that must never be pasted into
source or logs.

- [ ] Run `./dev.ps1 -Verify` and confirm the complete repository gate, including
      Phase 0, Phase 1, and Phase 2, reports green in one run.
- [x] Add a real OpenRouter key in Settings; send a normal chat message and watch
      a real streamed reply render token-by-token into one assistant bubble
      (2026-07-27 no-mock native run; final generated sanity reply:
      `FINAL_NATIVE_SANITY`).
- [ ] Send a reasoning-heavy prompt (a multi-step plan, a stack trace, or "think
      hard about ..."); confirm the router visibly escalates to the heavy model
      (spend/activity or logs show the heavy model id was used).
- [ ] Ask Halo to "organize my Downloads by type": confirm it runs as a real task
      with named stepped progress, a Tier-2/3 approval covering the whole batch,
      a per-move activity entry for each file, and that one Undo restores every
      file to its original location.
- [ ] Open the memory panel: edit a belief's text, delete it, then restore it —
      each action reaches the real Brain and the panel reflects the confirmed
      state (not an optimistic local change).
- [ ] Kill the Brain process mid-task (during a pending Tier-3 approval). On
      respawn/reconnect, confirm the same approval card reappears (not a new
      one) and approving it resumes and completes the task.
- [ ] With no OpenRouter key set (or the key deleted from Settings), send a chat
      message: confirm a plain in-bubble error naming Settings appears — no
      crash, no hang, no silently dropped turn.

## Result record

- Date:
- Commit:
- Windows/WebView2 version:
- Tester:
- Blocking findings: none / describe
- Non-blocking notes:

User confirmation: all listed native behaviors work as intended.

A phase is complete only when its automated gate is green **and** its native
checklist above has no blocking finding. Record the Phase-2 run separately
below when you work through that section.

### Phase 2 result record

- Date: 2026-07-27 (targeted real-key/native regression pass; unchecked items
  above remain outstanding)
- Commit:
- Tester: Codex native desktop automation
- Blocking findings: none in the exercised approval/stop/chat/orb paths
- Non-blocking notes: Deny and Stop now remove the modal approval, leave the
  composer enabled, and accept a follow-up. Stop terminates queued tool calls
  instead of raising replacement permission cards. Ordinary orb click opens
  the workspace. Destructive approvals and destructive Undo were not executed.

## Phase-3a managed-command foundation

- [x] Normalization/policy covers structured argv, roots, scripts,
      install/network/overwrite escalation, opaque-shell refusals, executable
      identity, trusted-profile binding, outside path-bearing flags, destructive
      Git escalation, custom environments, and current-user intent binding.
- [x] Generated Python creates a PDF and succeeds only after `pypdf` plus
      structural verification; exit-zero/missing and unchanged-overwrite cases
      fail truthfully.
- [x] Secret references stay out of approval/task/action/result projections and
      split output chunks are redacted before broadcast.
- [x] Native Windows Stop kills a spawned grandchild through the Job Object and
      returns within the halt budget; the child begins suspended so it cannot
      spawn before Job assignment.
- [x] Task admission freezes the normalized fingerprint and policy tier across
      queue waits; executable/cwd changes cannot acquire fresh authority later.
- [x] Stdout/stderr live caps are independent, binary output is suppressed,
      script scratch is capped, and artifact hashing does not block the event loop.
- [x] Artifact leases span baseline through final verification; files above
      256 MiB refuse, and a stalled PDF parser is killed at the operation deadline.
- [x] Authenticated WebSocket -> Tier-3 approval -> TaskRuntime -> verified PDF
      completes offline under `HALO_LLM_STUB`.
- [ ] In the native UI with a real model, ask for a simple folder, project test,
      and multi-step PDF respectively; confirm selection is `dir_create`,
      `command_run`, then `script_run`, with no prose-only permission request.
