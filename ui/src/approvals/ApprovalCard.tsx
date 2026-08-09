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

import { useCallback, useEffect, useId, useRef, useState } from "react";
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
  /** The conversation on screen — used ONLY as a fallback when a card carries
   * no conversation_id of its own (older frames). */
  conversationId: string;
  sendApprovalResponse: (reply_to: string, decision: ApprovalResponseMsg["decision"], edited_args?: unknown) => boolean;
  sendInterrupt: (conversationId: string) => void;
}

/** Bottom-center stack of every pending approval, over the current view.
 *
 * The overlay wrapper is ALWAYS mounted, even with nothing pending: the
 * announcer below is a live region, and a live region that appears in the DOM
 * already carrying its text is routinely not announced at all. It has to exist
 * empty first, then change. */
export function ApprovalOverlay({ conversationId, sendApprovalResponse, sendInterrupt }: OverlayProps) {
  const approvals = useHaloStore(selectApprovals);
  const list = Object.values(approvals);
  const newest = list[list.length - 1];
  return (
    <div className="approval-overlay" role="region" aria-label="Pending approvals">
      {/* An arriving approval announces itself; it must NEVER take focus. The
          card's own initial-focus target is Deny — a destructive answer — so
          stealing focus armed it under whatever the user typed next. */}
      <span className="halo-sr-only" role="status" aria-live="polite" aria-atomic="true">
        {newest ? `Approval needed: ${newest.summary ?? `Halo wants to run ${newest.tool}.`}` : ""}
      </span>
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
  const setActiveConversation = useHaloStore((s) => s.setActiveConversation);
  // Interrupt is keyed by conversation. With several threads open, a card
  // raised by a background thread must stop THAT thread, not whichever one is
  // on screen — so route by the card's own conversation_id and fall back to
  // the visible one only when the frame predates that field.
  const targetConversation = approval.conversation_id ?? conversationId;
  const background = Boolean(approval.conversation_id) && approval.conversation_id !== conversationId;

  // Deciding on a background thread's card means deciding about work you can't
  // see — switch to it so the context is on screen.
  const reveal = useCallback(() => {
    if (background) setActiveConversation(targetConversation);
  }, [background, setActiveConversation, targetConversation]);
  // Once the user commits a decision the buttons lock and show a spinner; the
  // card stays until the store removes it on the confirming frame (rule 3).
  const [pending, setPending] = useState<"idle" | "approving" | "denying" | "editing-send" | "stopping">("idle");
  // An answered card is a question that has been answered, so it leaves the
  // screen as soon as the decision is actually on the wire — the user should
  // never have to watch a spinner on a dialog they already dismissed. Gated on
  // a live socket on purpose: with the socket down the decision is only sitting
  // in the reconnect queue, and silently dropping the card there would tell the
  // user they approved something the Brain never heard. In that case the card
  // stays with its rule-3 spinner until the flush lands.
  const wsStatus = useHaloStore((s) => s.connection.wsStatus);
  const resolveApprovalLocally = useHaloStore((s) => s.resolveApprovalLocally);
  const settle = useCallback(() => {
    if (wsStatus === "connected") resolveApprovalLocally(approval_id);
  }, [wsStatus, resolveApprovalLocally, approval_id]);
  // A decision taken while the socket was down can silently never arrive:
  // dropStaleControlFrames discards a queued approval_response when the Brain
  // comes back on a fresh port, while the approval itself is durable and
  // returns in the reconnect snapshot still pending. Being still mounted once
  // we are connected again is proof the answer did not land — so unlock and let
  // the user answer again, rather than leaving a card stuck on "Denying…"
  // forever with nothing left that could ever unlock it. A card whose answer
  // DID land is already gone (settle, or the confirming frame), so this can
  // only fire on the genuinely-unanswered ones.
  useEffect(() => {
    if (wsStatus === "connected") setPending("idle");
  }, [wsStatus]);
  const [editing, setEditing] = useState(false);
  const [argsText, setArgsText] = useState(() => prettyJson(args_redacted));
  const [editError, setEditError] = useState<string | null>(null);
  const [showArgs, setShowArgs] = useState(false);
  const busy = pending !== "idle";
  const cardId = useId();
  const titleId = `${cardId}-title`;
  const summaryId = `${cardId}-summary`;
  const editHelpId = `${cardId}-edit-help`;
  const editErrorId = `${cardId}-edit-error`;
  const holdHintId = `${cardId}-hold-hint`;

  // No focus management here on purpose. The card arrives unprompted while the
  // user is typing; moving focus into it put Deny — an irreversible answer —
  // under the next keystroke. The overlay announces instead (see
  // ApprovalOverlay), and the card is reachable by Tab and as a labelled
  // region. WCAG 3.2.1/3.2.2: no context change the user did not initiate.

  const approve = useCallback(
    (edited_args?: unknown): boolean => {
      if (busy) return false;
      reveal();
      // Send BEFORE locking: if the frame is contract-invalid (e.g. edited args
      // with a non-finite number), dispatch returns false and we must NOT flip
      // the rule-3 lock — nothing was sent, so no confirming frame can ever
      // unlock it, and every button (including Stop) would be stuck disabled.
      const ok = sendApprovalResponse(approval_id, edited_args === undefined ? "approve" : "edit", edited_args);
      if (!ok) return false;
      setPending(edited_args === undefined ? "approving" : "editing-send");
      settle();
      return true;
    },
    [busy, reveal, approval_id, sendApprovalResponse, settle],
  );

  const deny = useCallback(() => {
    if (busy) return;
    reveal();
    setPending("denying");
    sendApprovalResponse(approval_id, "deny");
    settle();
  }, [busy, reveal, approval_id, sendApprovalResponse, settle]);

  const saveEdit = useCallback(() => {
    const parsed = parseArgs(argsText);
    if (!parsed.ok) {
      setEditError(parsed.error);
      return;
    }
    if (!approve(parsed.value)) {
      // Parsed as JSON but rejected by the contract (e.g. a non-finite number).
      setEditError("These edited values aren't valid — check them and try again.");
      return;
    }
    setEditError(null);
  }, [approve, argsText]);

  const stopTask = useCallback(() => {
    if (busy) return;
    reveal();
    setPending("stopping");
    sendInterrupt(targetConversation); // implicit-deny + pause; distinct from Deny
    settle();
  }, [busy, reveal, targetConversation, sendInterrupt, settle]);

  return (
    <GlassPanel
      elevation="card"
      className="approval-card"
      id={cardId}
      data-destructive={destructive ? "true" : undefined}
      role="alertdialog"
      aria-modal="false"
      aria-labelledby={titleId}
      aria-describedby={summaryId}
      tabIndex={-1}
    >
      <h2 id={titleId} className="halo-sr-only">Approval required</h2>
      <div className="approval-head">
        <Chip
          icon={destructive ? AlertTriangle : ShieldAlert}
          label={destructive ? "Tier 3 · destructive" : "Tier 3"}
          tone={destructive ? "destructive" : "tier3"}
        />
        <code className="approval-tool">{tool}</code>
        {background && (
          <button type="button" className="approval-elsewhere" onClick={reveal}>
            from another conversation — open it
          </button>
        )}
      </div>

      <p id={summaryId} className="approval-summary">{summary ?? `Halo wants to run ${tool}.`}</p>

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
        <>
          <textarea
            className="approval-args-edit"
            value={argsText}
            onChange={(e) => {
              setArgsText(e.currentTarget.value);
              if (editError) setEditError(null);
            }}
            aria-label="Edit arguments"
            aria-invalid={editError ? "true" : undefined}
            aria-describedby={editError ? `${editHelpId} ${editErrorId}` : editHelpId}
            rows={5}
          />
          <p id={editHelpId} className="approval-edit-help">
            Redacted placeholders such as &lt;15 chars&gt; preserve the original value when left unchanged.
          </p>
        </>
      )}
      {editing && editError && (
        <p id={editErrorId} className="approval-edit-error" role="alert">
          {editError}
        </p>
      )}

      <div className="approval-actions">
        {editing ? (
          <>
            <Button variant="primary" onClick={saveEdit} disabled={busy}>
              {pending === "editing-send" ? "Sending…" : "Approve with edits"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setEditError(null);
              }}
              disabled={busy}
            >
              Cancel
            </Button>
          </>
        ) : destructive ? (
          <HoldButton
            label="Hold to approve"
            busyLabel="Approving…"
            busy={pending === "approving"}
            disabled={busy}
            hintId={holdHintId}
            onComplete={() => approve()}
          />
        ) : (
          <Button variant="primary" onClick={() => approve()} disabled={busy}>
            {pending === "approving" ? "Approving…" : "Approve"}
          </Button>
        )}

        {!editing && (
          <>
            {/* Deny is never red — denying is the safe choice (design rule). */}
            <Button variant="ghost" onClick={deny} disabled={busy} data-safe-initial-focus>
              {pending === "denying" ? "Denying…" : "Deny"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setEditing(true);
                setEditError(null);
              }}
              disabled={busy}
            >
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
  /** id of a hidden element describing the press-and-hold gesture, so a screen
   *  reader announces how to operate the control (the hold is otherwise opaque). */
  hintId: string;
  onComplete: () => boolean;
}

// Press-and-hold with visible progress; fires onComplete once at HOLD_MS.
// Progress is driven by requestAnimationFrame (inline width), NOT a CSS
// transition — the global prefers-reduced-motion rule zeroes transitions, and
// the hold's progress feedback is essential, not decoration. Cancels the
// instant the pointer leaves or the key lifts (design: "cancels if the pointer
// leaves the button"). Keyboard equivalent: hold Enter or Space.
export function HoldButton({ label, busyLabel, busy, disabled, hintId, onComplete }: HoldProps) {
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
        if (!onComplete()) {
          firedRef.current = false;
          pointerIdRef.current = null;
          setProgress(0);
        }
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
      aria-describedby={hintId}
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
      <span id={hintId} className="halo-sr-only">
        Press and hold, or hold Enter or Space, to approve this destructive action.
      </span>
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

// The edited args go back in approval_response.edited_args. The Brain's gate
// expects a JSON object, so malformed JSON and non-object values remain in the
// editor with an actionable error instead of crossing the trust boundary.
type ParsedArgs =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; error: string };

function parseArgs(text: string): ParsedArgs {
  try {
    const value: unknown = JSON.parse(text);
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return { ok: false, error: "Arguments must be a valid JSON object." };
    }
    return { ok: true, value: value as Record<string, unknown> };
  } catch {
    return { ok: false, error: "Arguments must be a valid JSON object." };
  }
}
