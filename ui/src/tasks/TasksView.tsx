// Phase 1 Step 11 — Tasks view & lane chip (ui_ux/09-tasks.md): everything in
// flight, fed by the extended `task_state`. Cards ordered running →
// waiting_approval → paused → recent-done (collapsed). Lane chip doubles as a
// pin dropdown (`lane_pin`); pause/stop send `task_op`; sandbox (lane 3) cards
// host the scripted stream tile. All buttons follow rule 3 — disable on press,
// resolve only when the Brain's confirming `task_state` lands.
//
// ponytail: the waiting_approval card shows an amber "waiting" banner and the
// actual Approve/Deny lives in the shared ApprovalOverlay (one trust surface,
// no double-render). Embedding the full ApprovalCard inside the task card is a
// later refinement — the overlay already renders over this view.

import { AlertTriangle, Check, CircleDot, LoaderCircle, Pause, Play, ShieldAlert, Square } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "../components/Icon";
import { Chip } from "../components/Chip";
import { Button } from "../components/Button";
import {
  useHaloStore,
  selectTasks,
  selectTaskLogs,
  selectStream,
  selectOperationErrors,
  selectCapabilities,
} from "../state/store";
import { usePendingConfirm } from "../lib/usePendingConfirm";
import { LANE_LABEL, LANE_ICON, formatTaskProgress } from "../lib/lanes";
import type { TaskStateMsg } from "../ipc/contract";
import "./TasksView.css";

// Card ordering: running first, done last (collapsed). Arrival order breaks ties.
const STATE_RANK: Record<TaskStateMsg["state"], number> = {
  waiting: 0,
  running: 1,
  stopping: 2,
  waiting_approval: 3,
  paused: 4,
  stopped: 5,
  failed: 6,
  done: 7,
};

const DAY_MS = 24 * 60 * 60 * 1000;

interface TasksViewProps {
  sendTaskOp: (op: "pause" | "resume" | "stop", task_id?: string) => void;
  sendLanePin: (task_id: string, lane: 1 | 2 | 3) => void;
}

export function TasksView({ sendTaskOp, sendLanePin }: TasksViewProps) {
  const tasks = useHaloStore(selectTasks);
  const operationErrors = useHaloStore(selectOperationErrors);
  const capabilities = useHaloStore(selectCapabilities);
  const controlsAvailable = capabilities.taskControls === true;
  const { pending, failures, begin } = usePendingConfirm(tasks, operationErrors);

  const op = (task: TaskStateMsg, kind: "pause" | "resume" | "stop", label: string) => {
    if (!controlsAvailable) return;
    const confirms = (value: TaskStateMsg | undefined) => {
      if (kind === "pause") return value?.state === "paused";
      if (kind === "resume") return value?.state === "running";
      return value == null || value.state === "stopped" || value.state === "done" || value.state === "failed";
    };
    if (begin(task.task_id, label, confirms, "task_op")) sendTaskOp(kind, task.task_id);
  };
  const pin = (task: TaskStateMsg, lane: 1 | 2 | 3) => {
    if (!controlsAvailable) return;
    if (lane === task.lane) return;
    if (begin(task.task_id, "pinning", (value) => value?.lane === lane, "lane_pin")) sendLanePin(task.task_id, lane);
  };

  // Done cards collapse out after 24h (by ts display logic only — plan edge case).
  const now = Date.now();
  const visible = Object.values(tasks).filter(
    (t) => t.state !== "done" || now - new Date(t.ts).getTime() < DAY_MS,
  );
  const ordered = visible.sort((a, b) => STATE_RANK[a.state] - STATE_RANK[b.state]);

  if (ordered.length === 0) {
    return <div className="halo-empty">Nothing running — tasks I take on will show up here.</div>;
  }

  return (
    <div className="halo-scroll">
      {!controlsAvailable && (
        <p className="task-reason" role="status">
          Task controls are not available in this build. Task progress remains visible here.
        </p>
      )}
      <ul className="halo-list tasks-list">
        {ordered.map((t) => (
          <li key={t.task_id}>
            <TaskCard
              task={t}
              pending={pending[t.task_id]}
              failure={failures[t.task_id]}
              controlsAvailable={controlsAvailable}
              op={op}
              pin={pin}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

interface CardProps {
  task: TaskStateMsg;
  pending: string | undefined;
  failure: string | undefined;
  controlsAvailable: boolean;
  op: (task: TaskStateMsg, kind: "pause" | "resume" | "stop", label: string) => void;
  pin: (task: TaskStateMsg, lane: 1 | 2 | 3) => void;
}

function TaskCard({ task, pending, failure, controlsAvailable, op, pin }: CardProps) {
  const { title, state, lane, step, steps_total, reason } = task;
  const stream = useHaloStore(selectStream(task.task_id));
  const logs = useHaloStore(selectTaskLogs(task.task_id));
  const busy = pending !== undefined;
  const hasProgress = step != null && steps_total != null && steps_total > 0;
  const progressPercent = hasProgress
    ? Math.min(100, Math.max(0, (step / steps_total) * 100))
    : 0;
  const collapsed = state === "done";
  const terminal = state === "done" || state === "stopped" || state === "failed";
  const controlsLocked = terminal || state === "stopping";

  return (
    <div className="halo-card task-card" data-state={state}>
      <div className="task-head">
        <span className="task-title">{title ?? "Working"}</span>
        <TaskStateChip state={state} />
        <LanePin task={task} disabled={busy || !controlsAvailable || controlsLocked} onPin={pin} />
      </div>

      {!collapsed && (
        <>
          {failure && <p className="task-reason" role="alert">{failure}</p>}
          {hasProgress && (
            <div className="task-progress">
              <div className="halo-meter task-progress-bar">
                <span style={{ width: `${progressPercent}%` }} />
              </div>
              <span className="task-progress-text">{formatTaskProgress(task)}</span>
            </div>
          )}

          {state === "waiting_approval" && (
            <p className="task-waiting">Waiting for your approval — see the card below.</p>
          )}
          {state === "waiting" && (
            <p className="task-reason">Queued — waiting for a task worker.</p>
          )}
          {state === "paused" && (
            <p className="task-reason">
              {reason ?? "Paused."}
              {hasProgress ? ` — will continue from step ${step}.` : ""}
            </p>
          )}
          {state === "failed" && <p className="task-reason">{reason ?? "This task failed."}</p>}
          {state === "stopped" && (
            <p className="task-reason">
              {hasProgress ? `Stopped after ${step} of ${steps_total}.` : "Stopped before completion."}
            </p>
          )}

          {lane === 3 && (
            <div className="task-stream" aria-label="Sandbox stream">
              {stream ? (
                <img className="task-stream-img" src={`data:image/jpeg;base64,${stream.jpeg_b64}`} alt="Live sandbox frame" />
              ) : (
                <span className="task-stream-idle">Waiting for the sandbox stream…</span>
              )}
            </div>
          )}

          {logs.length > 0 && (
            <pre className="task-log" aria-label="Task output">
              {logs.map((entry) => entry.text).join("")}
            </pre>
          )}

          {!terminal && (
            <div className="task-actions">
              {(state === "running" || state === "waiting_approval") && (
                <Button variant="ghost" disabled={busy || !controlsAvailable} onClick={() => op(task, "pause", "pausing")}>
                  <Icon icon={Pause} size={16} />
                  {pending === "pausing" ? "Pausing…" : "Pause"}
                </Button>
              )}
              {state === "paused" && (
                <Button variant="primary" disabled={busy || !controlsAvailable} onClick={() => op(task, "resume", "resuming")}>
                  <Icon icon={Play} size={16} />
                  {pending === "resuming" ? "Resuming…" : "Resume"}
                </Button>
              )}
              <Button
                variant="destructive"
                disabled={!controlsAvailable}
                aria-disabled={busy || state === "stopping"}
                onClick={() => {
                  if (!busy && state !== "stopping") op(task, "stop", "stopping");
                }}
              >
                <Icon icon={Square} size={16} />
                {pending === "stopping" || state === "stopping" ? "Stopping…" : "Stop"}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

const TASK_STATE_CHIP: Record<
  TaskStateMsg["state"],
  { label: string; tone: "primary" | "tier3" | "muted" | "success" | "destructive"; icon: LucideIcon }
> = {
  waiting: { label: "Queued", tone: "muted", icon: CircleDot },
  running: { label: "Running", tone: "primary", icon: CircleDot },
  stopping: { label: "Stopping", tone: "muted", icon: LoaderCircle },
  waiting_approval: { label: "Waiting for you", tone: "tier3", icon: ShieldAlert },
  paused: { label: "Paused", tone: "muted", icon: Pause },
  stopped: { label: "Stopped", tone: "muted", icon: Square },
  done: { label: "Done", tone: "success", icon: Check },
  failed: { label: "Failed", tone: "destructive", icon: AlertTriangle },
};

function TaskStateChip({ state }: { state: TaskStateMsg["state"] }) {
  const { label, tone, icon } = TASK_STATE_CHIP[state];
  return <Chip icon={icon} label={label} tone={tone} className="task-state-chip" />;
}

// Lane chip + pin dropdown: a native <select> is the laziest accessible
// control. Shows the current lane (icon + label) and re-pins on change.
function LanePin({ task, disabled, onPin }: { task: TaskStateMsg; disabled: boolean; onPin: CardProps["pin"] }) {
  return (
    <label className="task-lane">
      <Icon icon={LANE_ICON[task.lane]} size={16} />
      <select
        className="halo-input task-lane-select"
        value={task.lane}
        disabled={disabled}
        aria-label="Lane"
        onChange={(e) => onPin(task, Number(e.target.value) as 1 | 2 | 3)}
      >
        {([1, 2, 3] as const).map((l) => (
          <option key={l} value={l}>
            {LANE_LABEL[l]}
          </option>
        ))}
      </select>
    </label>
  );
}
