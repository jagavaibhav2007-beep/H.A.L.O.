# Floating Pill Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand Halo's floating pill for pending approvals and let the user approve or deny through the existing authenticated Brain connection.

**Architecture:** Reuse the orb window's existing Zustand projection and `sendApprovalResponse` transport. Add one compact view component and one small native-window sizing hook; share the existing destructive hold control and live-send dismissal semantics instead of creating another permission path.

**Tech Stack:** React 19, Zustand, Tauri 2 window API, TypeScript, CSS tokens, Vitest/Testing Library, Rust window clipping tests.

## Global Constraints

- Do not change the WebSocket contract or Brain gate.
- Never display unredacted arguments in the floating pill.
- Destructive approval requires the existing 700 ms hold; Deny is always one click.
- The pill remains non-focusable and must not steal focus on arrival.
- Use existing color, motion, typography, and button tokens; add no dependency.

---

### Task 1: Compact approval behavior

**Files:**
- Create: `ui/src/orb/FloatingApproval.tsx`
- Create: `ui/src/orb/FloatingApproval.test.tsx`
- Modify: `ui/src/approvals/ApprovalCard.tsx`

**Interfaces:**
- Consumes: `ApprovalRequestMsg`, `ApprovalResponseMsg["decision"]`, store `connection.wsStatus`, and `resolveApprovalLocally`.
- Produces: `FloatingApproval({ approval, count, connected, sendApprovalResponse, onReview })` and exported `HoldButton`.

- [ ] Write a failing component test that renders the compact request:

```tsx
render(<FloatingApproval approval={approval} count={2} connected sendApprovalResponse={send} onReview={review} />);
expect(screen.getByText("Delete the generated draft?")).toBeTruthy();
expect(screen.getByText("file_delete")).toBeTruthy();
expect(screen.getByText("2 approvals waiting")).toBeTruthy();
```

- [ ] Run `npm test -- --run src/orb/FloatingApproval.test.tsx` and confirm the missing component fails.
- [ ] Add action tests with literal expectations:

```tsx
fireEvent.click(screen.getByRole("button", { name: "Approve" }));
expect(send).toHaveBeenCalledWith("approval-1", "approve");
expect(useHaloStore.getState().approvals["approval-1"]).toBeUndefined();
expect(useHaloStore.getState().approvals["approval-2"]).toBeDefined();
```

- [ ] Add a fake-animation-frame test proving a destructive request does not approve on release before 700 ms and approves once after the threshold.
- [ ] Export the existing hold control and implement the exact compact interface:

```tsx
export interface FloatingApprovalProps {
  approval: ApprovalRequestMsg;
  count: number;
  connected: boolean;
  sendApprovalResponse: (replyTo: string, decision: ApprovalResponseMsg["decision"]) => boolean;
  onReview: () => void;
}
```

- [ ] In the action handler, send first and call `resolveApprovalLocally(approval_id)` only when the send returned true and `connected` is true; leave the request visible otherwise.
- [ ] Re-run the focused component and existing `ApprovalCard` tests until both pass.

### Task 2: Pill expansion and monitor-safe sizing

**Files:**
- Create: `ui/src/orb/useApprovalWindow.ts`
- Create: `ui/src/orb/useApprovalWindow.test.ts`
- Modify: `ui/src/orb/OrbRoot.tsx`
- Modify: `ui/src/orb/OrbRoot.css`
- Modify: `ui/src-tauri/src/windows.rs`

**Interfaces:**
- Consumes: `expanded: boolean` and Tauri's current window/monitor measurements.
- Produces: `useApprovalWindow(expanded)` and pure `fitApprovalWindow(...)` geometry.

- [ ] Write failing pure tests for unchanged in-bounds expansion, bottom-edge upward clamping, and collapsed-position restoration:

```ts
expect(fitApprovalWindow({ x: 20, y: 20 }, { x: 0, y: 0, width: 1920, height: 1040 }, 224)).toEqual({ x: 20, y: 20 });
expect(fitApprovalWindow({ x: 20, y: 980 }, { x: 0, y: 0, width: 1920, height: 1040 }, 224)).toEqual({ x: 20, y: 816 });
```

- [ ] Run the focused sizing test and confirm the missing helper fails.
- [ ] Implement the pure clamp and a minimal effect with an incrementing generation ref so a stale expand cannot overwrite a later collapse:

```ts
export function fitApprovalWindow(position: Point, workArea: Rect, height: number): Point {
  return {
    x: Math.min(Math.max(position.x, workArea.x), workArea.x + workArea.width - PILL_WIDTH),
    y: Math.min(Math.max(position.y, workArea.y), workArea.y + workArea.height - height),
  };
}
```

- [ ] Update `OrbRoot` to keep `sendApprovalResponse`, select the oldest approval, resize when any approval exists, render the compact panel, and stop approval controls from triggering drag/workspace toggle.
- [ ] Add CSS for a 52 px status row plus approval panel, 200–250 ms opacity/translate motion, line clamping, connected/disabled states, and reduced motion through existing global tokens.
- [ ] Change native capsule clipping to `corner_diameter(height) = height.min(52)` and add a Rust unit test proving both 52 px and 224 px windows use a 52 px diameter.
- [ ] Run focused UI and Rust tests and fix only behavior required by the design.

### Task 3: Durable docs and verification

**Files:**
- Modify: `ui_ux/01-companion-orb.md`
- Modify: `ui_ux/05-permissions-trust.md`
- Modify: `systemdesign/10-ui.md`
- Modify: `VERIFY.md`

**Interfaces:**
- Consumes: completed UI behavior.
- Produces: canonical design and native verification checklist matching the implementation.

- [ ] Document the expanded-pill approval flow, compact/full-card boundary, destructive hold, multi-request behavior, and unchanged Brain contract.
- [ ] Add native checks for expand/collapse, Approve/Deny, destructive hold, multiple approvals, monitor edges, and reconnect.
- [ ] Run `npm test -- --run`, `npm run build`, `cargo test`, and `./dev.ps1 -Verify`.
- [ ] Inspect the rendered pill in browser/native mode at collapsed and approval-expanded sizes, including reduced motion and long copy.
- [ ] Review `git diff --check`, `git status`, and the final diff for secrets or unrelated changes.
