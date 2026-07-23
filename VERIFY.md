# Native Verification (Phases 1–2)

Status: **Phase 1 COMPLETE** — automated gate passed, native checklist
user-confirmed complete on 2026-07-13.
Status: **Phase 2 and the 2026-07-22 automated audit hardening are integration-verified.**
`./dev.ps1 -Verify` reached `FULL AUTOMATED VERIFICATION PASSED`. App-scoped
native mock startup confirmed Brain/Voice authentication, and forced death of
the exact Tauri PID reaped its exact Brain/Voice child PIDs. Human visual,
keyboard/NVDA, minimum-size, and real-OpenRouter-key checks remain unchecked.
See [AUDIT_PLAN.md](AUDIT_PLAN.md).

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
      (2026-07-22: all Python/Voice scripts, five TS self-checks, Vitest 16/16,
      production build, Rust 7/7, and Phase 0/1/2 gates).
- [x] Confirm both gate scripts parse in Windows PowerShell and resolve Python
      3.11+ via `python`, `py -3`, or a discoverable bundled Codex runtime.
- [x] During focused protocol work, `./dev.ps1 -Smoke` may be used as the
      faster Phase 0/1/2 protocol-only check. A green `-Smoke` is not a
      substitute for `-Verify` before declaring the repository gate green.

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

All validated P0/P1 audit findings are resolved and one complete `-Verify` run
is green. Real task controls now fail honestly with exact correlated errors
instead of hanging; global operation failures unlock the correct control;
memory correction is transactional; admission is bounded; and the Skills/chat
accessibility regressions pass. The checks below still require a human and, for
provider behavior, a real key that must never be pasted into source or logs.

- [x] Run `./dev.ps1 -Verify` and confirm the complete repository gate, including
      Phase 0, Phase 1, and Phase 2, reports green in one run.
- [ ] Add a real OpenRouter key in Settings; send a normal chat message and watch
      a real streamed reply render token-by-token into one assistant bubble.
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

- Date:
- Commit:
- Tester:
- Blocking findings: none / describe
- Non-blocking notes:
