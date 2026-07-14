# Phase 1 Verification

Status: COMPLETE. The automated gate passed, and the native checklist was
user-confirmed complete on 2026-07-13.

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

## Result record

- Date:
- Commit:
- Windows/WebView2 version:
- Tester:
- Blocking findings: none / describe
- Non-blocking notes:

User confirmation: all listed native behaviors work as intended.

Phase 1 is complete only when the automated gate is green and this native
checklist has no blocking finding.
