# Native Verification (Phases 1–2)

Status: **Phase 1 COMPLETE** — automated gate passed, native checklist
user-confirmed complete on 2026-07-13.
Status: **Phase 2 automated gate green (2026-07-21); its native checklist
below is NOT yet user-run** — it needs a real OpenRouter key, so only you
can complete it.

Run the automated gate first, then start the native app with
`./dev.ps1 -Mock`. The normal launcher uses the Phase 0 echo Brain and does
not respond to `demo ...` triggers.

## Automated gate

- [ ] Run `./dev.ps1 -Smoke` from the repository root.
- [ ] Confirm the Phase 0 transport smoke passes before Phase 1 starts.
- [ ] Confirm Phase 1 reports snapshot idempotence, approval branches, undo,
      the full walkthrough, and remaining scenarios as passing.
- [ ] Run `npx tsc --noEmit` and `npm run build` from `ui/`.
- [ ] Run `cargo test` from `ui/src-tauri/`.

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

## Phase 2 native checklist

The automated gate (`./dev.ps1 -Smoke`, now Phase 0 -> Phase 1 -> Phase 2) proves
the real Brain's protocol paths offline, against `HALO_LLM_STUB`/`HALO_EXTRACT_STUB`.
It cannot prove a real model reply looks right, a real router escalation reads as
intended, or that the UI's rendering of a real multi-step task/undo/memory round-trip
holds up outside a script. Run the plain (non-`-Mock`) app with a real OpenRouter key
set in Settings for all of the below.

- [ ] Run `./dev.ps1 -Smoke` and confirm Phase 0, Phase 1, and Phase 2 all report
      green in one run.
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
