// Phase 1 Step 10 — approval cards & trust surfaces (ui_ux/05-permissions-trust.md).
// The most trust-critical component in the app; the edge cases ARE the feature.
// One ApprovalCard, rendered by ApprovalOverlay as a bottom-center stack over
// whatever view is active. A card resolves ONLY when the store removes it (the
// Brain's confirming task_state/activity — rule 3), never optimistically on a
// click; a press just disables the buttons and shows a pending spinner until
// that confirming frame drops the card from the store and unmounts it.
//
// ponytail: inline-in-chat anchor (the card rendered at the chat pause point)
// deferred — this overlay already renders over the chat view, so the full
// round-trip works from chat; in-conversation placement is UX refinement, not
// a trust property. Add it when the chat view needs the card threaded between
// bubbles rather than floating over them.

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { Chip } from "../components/Chip";
import { Button } from "../components/Button";
import { GlassPanel } from "../components/GlassPanel";
import type { ApprovalRequestMsg, ApprovalResponseMsg } from "../ipc/contract";
import { useHaloStore, selectApprovals } from "../state/store";
import "./ApprovalCard.css";

// press-and-hold duration for destructive approvals (design language:
// "700ms hold-to-approve with visible progress"). Money/destructive only.
const HOLD_MS = 700;

interface OverlayProps {
  conversationId: string;
  sendApprovalResponse: (reply_to: string, decision: ApprovalResponseMsg["decision"], edited_args?: unknown) => void;
  sendInterrupt: (conversationId: string) => void;
}

/** Bottom-center stack of every pending approval, over the current view. */
export function ApprovalOverlay({ conversationId, sendApprovalResponse, sendInterrupt }: OverlayProps) {
  const approvals = useHaloStore(selectApprovals);
  const list = Object.values(approvals);
  if (list.length === 0) return null;
  return (
    <div className="approval-overlay" role="region" aria-label="Pending approvals">
      {list.map((a) => (
        <ApprovalCard
          key={a.approval_id}
          approval={a}
          conversationId={conversationId}
          sendApprovalResponse={sendApprovalResponse}
          sendInterrupt={sendInterrupt}
        />
      ))}
    </div>
  );
}

interface CardProps {
  approval: ApprovalRequestMsg;
  conversationId: string;
  sendApprovalResponse: OverlayProps["sendApprovalResponse"];
  sendInterrupt: OverlayProps["sendInterrupt"];
}

function ApprovalCard({ approval, conversationId, sendApprovalResponse, sendInterrupt }: CardProps) {
  const { approval_id, tool, args_redacted, summary, destructive } = approval;
  // Once the user commits a decision the buttons lock and show a spinner; the
  // card stays until the store removes it on the confirming frame (rule 3).
  const [pending, setPending] = useState<"idle" | "approving" | "denying" | "editing-send" | "stopping">("idle");
  const [editing, setEditing] = useState(false);
  const [argsText, setArgsText] = useState(() => prettyJson(args_redacted));
  const [showArgs, setShowArgs] = useState(false);
  const busy = pending !== "idle";

  const approve = useCallback(
    (edited_args?: unknown) => {
      if (busy) return;
      setPending(edited_args === undefined ? "approving" : "editing-send");
      sendApprovalResponse(approval_id, edited_args === undefined ? "approve" : "edit", edited_args);
    },
    [busy, approval_id, sendApprovalResponse],
  );

  const deny = useCallback(() => {
    if (busy) return;
    setPending("denying");
    sendApprovalResponse(approval_id, "deny");
  }, [busy, approval_id, sendApprovalResponse]);

  const saveEdit = useCallback(() => {
    approve(parseArgs(argsText));
  }, [approve, argsText]);

  const stopTask = useCallback(() => {
    if (busy) return;
    setPending("stopping");
    sendInterrupt(conversationId); // implicit-deny + pause; distinct from Deny
  }, [busy, conversationId, sendInterrupt]);

  return (
    <GlassPanel
      elevation="card"
      className="approval-card"
      data-destructive={destructive ? "true" : undefined}
      role="group"
      aria-label={summary ?? `Approve ${tool}`}
    >
      <div className="approval-head">
        <Chip
          icon={destructive ? AlertTriangle : ShieldAlert}
          label={destructive ? "Tier 3 · destructive" : "Tier 3"}
          tone={destructive ? "destructive" : "tier3"}
        />
        <code className="approval-tool">{tool}</code>
      </div>

      <p className="approval-summary">{summary ?? `Halo wants to run ${tool}.`}</p>

      <button
        type="button"
        className="approval-args-toggle"
        aria-expanded={showArgs}
        onClick={() => setShowArgs((v) => !v)}
      >
        {showArgs ? "Hide details" : "Show details"}
      </button>
      {showArgs && !editing && <pre className="approval-args">{prettyJson(args_redacted)}</pre>}
      {editing && (
        <textarea
          className="approval-args-edit"
          value={argsText}
          onChange={(e) => setArgsText(e.currentTarget.value)}
          aria-label="Edit arguments"
          rows={5}
        />
      )}

      <div className="approval-actions">
        {editing ? (
          <>
            <Button variant="primary" onClick={saveEdit} disabled={busy}>
              {pending === "editing-send" ? "Sending…" : "Approve with edits"}
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </Button>
          </>
        ) : destructive ? (
          <HoldButton label="Hold to approve" busyLabel="Approving…" busy={pending === "approving"} disabled={busy} onComplete={() => approve()} />
        ) : (
          <Button variant="primary" onClick={() => approve()} disabled={busy}>
            {pending === "approving" ? "Approving…" : "Approve"}
          </Button>
        )}

        {!editing && (
          <>
            {/* Deny is never red — denying is the safe choice (design rule). */}
            <Button variant="ghost" onClick={deny} disabled={busy}>
              {pending === "denying" ? "Denying…" : "Deny"}
            </Button>
            <Button variant="ghost" onClick={() => setEditing(true)} disabled={busy}>
              Edit
            </Button>
          </>
        )}
      </div>

      <div className="approval-foot">
        {destructive && <span className="approval-note">Voice approval is off for destructive actions.</span>}
        {/* Stop the whole task — implicit deny + pause. Distinct from Deny,
            which answers this one action while the task keeps waiting. */}
        <button type="button" className="approval-stop" onClick={stopTask} disabled={busy}>
          {pending === "stopping" ? "Stopping…" : "Stop this task"}
        </button>
      </div>
    </GlassPanel>
  );
}

interface HoldProps {
  label: string;
  busyLabel: string;
  busy: boolean;
  disabled: boolean;
  onComplete: () => void;
}

// Press-and-hold with visible progress; fires onComplete once at HOLD_MS.
// Progress is driven by requestAnimationFrame (inline width), NOT a CSS
// transition — the global prefers-reduced-motion rule zeroes transitions, and
// the hold's progress feedback is essential, not decoration. Cancels the
// instant the pointer leaves or the key lifts (design: "cancels if the pointer
// leaves the button"). Keyboard equivalent: hold Enter or Space.
function HoldButton({ label, busyLabel, busy, disabled, onComplete }: HoldProps) {
  const [progress, setProgress] = useState(0);
  const rafRef = useRef<number | null>(null);
  const firedRef = useRef(false);
  const pointerIdRef = useRef<number | null>(null);

  const cancel = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    pointerIdRef.current = null;
    if (!firedRef.current) setProgress(0);
  }, []);

  const cancelPointer = useCallback((pointerId: number) => {
    if (pointerIdRef.current === pointerId) cancel();
  }, [cancel]);

  const start = useCallback(() => {
    if (disabled || busy || rafRef.current != null || firedRef.current) return;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min((now - t0) / HOLD_MS, 1);
      setProgress(p);
      if (p >= 1) {
        firedRef.current = true;
        rafRef.current = null;
        onComplete();
        return;
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [disabled, busy, onComplete]);

  useEffect(() => () => cancel(), [cancel]);

  return (
    <button
      type="button"
      className="halo-btn halo-btn-destructive approval-hold"
      disabled={disabled}
      aria-label={label}
      onPointerDown={(e) => {
        if (e.button !== 0 || !e.isPrimary) return;
        pointerIdRef.current = e.pointerId;
        e.currentTarget.setPointerCapture(e.pointerId);
        start();
      }}
      onPointerUp={(e) => cancelPointer(e.pointerId)}
      onPointerLeave={(e) => cancelPointer(e.pointerId)}
      onPointerCancel={(e) => cancelPointer(e.pointerId)}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
          e.preventDefault(); // stop the browser's synthetic click on keyup
          start();
        }
      }}
      onKeyUp={(e) => {
        if (e.key === "Enter" || e.key === " ") cancel();
      }}
    >
      <span className="approval-hold-fill" style={{ width: `${progress * 100}%` }} aria-hidden="true" />
      <span className="approval-hold-label">{busy ? busyLabel : label}</span>
    </button>
  );
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

// The edited args go back in approval_response.edited_args. Parse to an object
// when possible; fall back to the raw string otherwise (there's no real
// backend to validate against in Phase 1 — the mock only branches on the
// decision, not the payload). # ponytail: strict schema validation of edits is
// a Phase-2 concern when a real tool consumes edited_args.
function parseArgs(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
