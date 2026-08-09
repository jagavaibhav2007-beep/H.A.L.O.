# Floating Pill Approvals Design

## Goal

Let the user understand and answer a pending Tier-3 request directly from Halo's floating pill without opening the workspace.

## Chosen interaction

When the pill's existing WebSocket store receives an `approval_request`, the native pill window grows from 360×52 to 360×224 and reveals one compact approval panel below its normal status row. The panel leads with the Brain-provided plain-language `summary`, shows the tool name, and offers **Approve**, **Deny**, and **Review details**. Review details opens the workspace; argument inspection and editing remain there because fitting the full trust surface into the pill would make it cramped and error-prone.

The oldest pending approval is shown first. If more are waiting, the panel shows the total count; resolving the current request advances to the next without collapsing. The pill collapses only when no approval remains.

## Trust and safety

- The pill sends the existing authenticated `approval_response` frame. There is no new IPC message and no direct tool execution from the UI.
- Non-destructive requests use one-click Approve. Destructive or irreversible requests reuse the existing 700 ms press-and-hold control; Deny remains a normal safe action.
- Actions are disabled while the Brain connection is unavailable. A queued/offline click must not visually dismiss the request.
- A decision sent over a live socket removes the answered question locally, matching the workspace behavior; later Brain confirmation remains authoritative for task state and activity.
- The pill never steals focus when a request arrives. Its existing Windows toast remains as the away notification and still opens the full workspace.
- Redacted values stay redacted. The compact surface shows only `summary` and `tool`; raw arguments never appear there.

## Layout and motion

The existing 52 px status row remains visually unchanged and draggable. The approval panel uses the pill's fixed midnight palette, an amber separator/accent, two compact actions, and a quiet text link for full review. Expansion uses the existing 200–250 ms motion tokens; content enters with opacity and a short vertical translation. Reduced-motion mode removes the transition without hiding state.

The native window resize is clamped to the current monitor work area. On collapse it returns to 360×52. Native window clipping keeps a 26 px corner radius instead of turning the tall approval surface into a 112 px stadium.

## Architecture and data flow

1. `OrbRoot` keeps the return value from `useStoreConnection()` and selects the current approval from the orb window's own Zustand projection.
2. `FloatingApproval` renders the compact trust surface and calls `sendApprovalResponse`.
3. Shared approval-decision behavior preserves the workspace rules for live-send dismissal, reconnect recovery, and destructive hold.
4. A small pill-window sizing helper handles Tauri-only resize/monitor clamping; the browser fallback renders the expanded visual without native APIs.
5. The Brain receives the unchanged `approval_response`, resolves its existing interrupt, and broadcasts normal task/activity confirmation frames to both windows.

## Edge cases

- Several approvals: show one deterministically plus a count; advance without a collapse flash.
- Approval disappears from a task/activity/interrupt frame: collapse when the store becomes empty.
- Brain reconnects: keep the panel visible and re-enable it only when authenticated.
- Send validation fails: keep the approval and unlock actions.
- Pill near a monitor edge or on mixed DPI displays: size and position in logical pixels derived from the current monitor scale.
- Rapid approval arrival/removal: cancel stale resize continuations and apply only the latest desired state.
- Destructive action: pointer hold may cancel on release, leave, or pointer cancel; workspace remains available for keyboard completion because the pill intentionally stays non-focusable.
- Long summary/tool: clamp summary to three lines and ellipsize the tool label; the workspace contains complete details.
- Window resize failure: retain the normal pill and amber count; approval remains safely pending and accessible through the workspace/toast.

## Testing

- Component tests cover summary/tool/count rendering, approve/deny payloads, destructive hold, disconnected behavior, and advancing across multiple approvals.
- Pure sizing tests cover bottom-edge clamping and collapse restoration without mocking the operating system.
- Existing workspace approval tests must remain green to prove the shared trust behavior did not regress.
- Typecheck/build, Rust tests, focused UI tests, and the full repository verification gate run before completion.

