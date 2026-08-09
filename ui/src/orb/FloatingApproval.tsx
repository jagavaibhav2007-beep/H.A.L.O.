import type { ApprovalRequestMsg, ApprovalResponseMsg } from "../ipc/contract";
import { HoldButton } from "../approvals/ApprovalCard";
import { Button } from "../components/Button";
import { useHaloStore } from "../state/store";

export interface FloatingApprovalProps {
  approval: ApprovalRequestMsg;
  count: number;
  connected: boolean;
  sendApprovalResponse: (replyTo: string, decision: ApprovalResponseMsg["decision"]) => boolean;
  onReview: () => void;
}

export function FloatingApproval({ approval, count, connected, sendApprovalResponse, onReview }: FloatingApprovalProps) {
  const resolveApprovalLocally = useHaloStore((s) => s.resolveApprovalLocally);
  const respond = (decision: ApprovalResponseMsg["decision"]) => {
    if (sendApprovalResponse(approval.approval_id, decision) && connected) {
      resolveApprovalLocally(approval.approval_id);
    }
  };

  return (
    <section className="floating-approval" aria-label="Pending approval">
      <code>{approval.tool}</code>
      <p>{approval.summary ?? `Halo wants to run ${approval.tool}.`}</p>
      <span>{count} approvals waiting</span>
      <div className="floating-approval-actions">
        {approval.destructive ? (
          <HoldButton
            label="Hold to approve"
            busyLabel="Approving…"
            busy={false}
            disabled={false}
            hintId="floating-approval-hold-hint"
            onComplete={() => respond("approve")}
          />
        ) : (
          <Button onClick={() => respond("approve")}>Approve</Button>
        )}
        <Button variant="ghost" onClick={() => respond("deny")}>Deny</Button>
        <Button variant="ghost" onClick={onReview}>Review</Button>
      </div>
    </section>
  );
}
