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

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, CircleDot, Pause, Play, ShieldAlert, Square, Users, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "../components/Icon";
import { Chip } from "../components/Chip";
import { Button } from "../components/Button";
import { useHaloStore, selectTasks, selectStream } from "../state/store";
import type { TaskStateMsg } from "../ipc/contract";
import "./TasksView.css";

const LANE_LABEL: Record<1 | 2 | 3, string> = { 1: "Fast", 2: "Takeover", 3: "Sandbox" };
const LANE_ICON: Record<1 | 2 | 3, LucideIcon> = { 1: Zap, 2: Users, 3: ShieldAlert };

// Card ordering: running first, done last (collapsed). Arrival order breaks ties.
const STATE_RANK: Record<TaskStateMsg["state"], number> = {
  running: 0,
  waiting_approval: 1,
  paused: 2,
  failed: 3,
  done: 4,
};

const DAY_MS = 24 * 60 * 60 * 1000;

interface TasksViewProps {
  sendTaskOp: (op: "pause" | "resume" | "stop", task_id?: string) => void;
  sendLanePin: (task_id: string, lane: 1 | 2 | 3) => void;
}

export function TasksView({ sendTaskOp, sendLanePin }: TasksViewProps) {
  const tasks = useHaloStore(selectTasks);

  // Rule 3: a task's pending op (pause/resume/stop/pin) locks its controls
  // until the Brain confirms via a fresh task_state. Every task_state upserts a
  // NEW object, so a changed reference == confirmation — clear pending then.
  // `prev` is captured BEFORE mutating the ref so the updater is a pure
  // function of its closure (StrictMode double-invokes function-form
  // updaters in dev to catch side effects like mutating a ref inside one).
  const [pending, setPending] = useState<Record<string, string>>({});
  const prevRefs = useRef<Record<string, TaskStateMsg>>({});
  useEffect(() => {
    const prev = prevRefs.current;
    prevRefs.current = tasks;
    setPending((p) => {
      let changed = false;
      const next = { ...p };
      for (const id of Object.keys(p)) {
        if (tasks[id] !== prev[id]) {
          delete next[id];
          changed = true;
        }
      }
      return changed ? next : p;
    });
  }, [tasks]);

  const op = (task: TaskStateMsg, kind: "pause" | "resume" | "stop", label: string) => {
    if (pending[task.task_id]) return;
    setPending((p) => ({ ...p, [task.task_id]: label }));
    sendTaskOp(kind, task.task_id);
  };

  const pin = (task: TaskStateMsg, lane: 1 | 2 | 3) => {
    if (pending[task.task_id] || lane === task.lane) return;
    setPending((p) => ({ ...p, [task.task_id]: "pinning" }));
    sendLanePin(task.task_id, lane);
  };

  // Done cards collapse out after 24h (by ts display logic only — plan edge case).
  const now = Date.now();
  const visible = Object.values(tasks).filter(
    (t) => t.state !== "done" || now - new Date(t.ts).getTime() < DAY_MS,
  );
  const ordered = visible
    .map((t, i) => ({ t, i }))
    .sort((a, b) => STATE_RANK[a.t.state] - STATE_RANK[b.t.state] || a.i - b.i)
    .map(({ t }) => t);

  if (ordered.length === 0) {
    return <div className="tasks-empty">Nothing running — tasks I take on will show up here.</div>;
  }

  return (
    <div className="tasks-view">
      <ul className="tasks-list">
        {ordered.map((t) => (
          <li key={t.task_id}>
            <TaskCard task={t} pending={pending[t.task_id]} op={op} pin={pin} />
          </li>
        ))}
      </ul>
    </div>
  );
}

interface CardProps {
  task: TaskStateMsg;
  pending: string | undefined;
  op: (task: TaskStateMsg, kind: "pause" | "resume" | "stop", label: string) => void;
  pin: (task: TaskStateMsg, lane: 1 | 2 | 3) => void;
}

function TaskCard({ task, pending, op, pin }: CardProps) {
  const { title, state, lane, step, steps_total, step_label, reason } = task;
  const stream = useHaloStore(selectStream(task.task_id));
  const busy = pending !== undefined;
  const hasProgress = step != null && steps_total != null;
  const collapsed = state === "done";

  return (
    <div className="task-card" data-state={state}>
      <div className="task-head">
        <span className="task-title">{title ?? "Working"}</span>
        <TaskStateChip state={state} />
        <LanePin task={task} disabled={busy} onPin={pin} />
      </div>

      {!collapsed && (
        <>
          {hasProgress && (
            <div className="task-progress">
              <div className="task-progress-bar">
                <span style={{ width: `${(step! / steps_total!) * 100}%` }} />
              </div>
              <span className="task-progress-text">
                step {step}/{steps_total}
                {step_label ? ` — ${step_label}` : ""}
              </span>
            </div>
          )}

          {state === "waiting_approval" && (
            <p className="task-waiting">Waiting for your approval — see the card below.</p>
          )}
          {state === "paused" && (
            <p className="task-reason">
              {reason ?? "Paused."}
              {hasProgress ? ` — will continue from step ${step}.` : ""}
            </p>
          )}
          {state === "failed" && <p className="task-reason">{reason ?? "This task failed."}</p>}

          {lane === 3 && (
            <div className="task-stream" aria-label="Sandbox stream">
              {stream ? (
                <img className="task-stream-img" src={`data:image/jpeg;base64,${stream.jpeg_b64}`} alt="Live sandbox frame" />
              ) : (
                <span className="task-stream-idle">Waiting for the sandbox stream…</span>
              )}
            </div>
          )}

          <div className="task-actions">
            {(state === "running" || state === "waiting_approval") && (
              <Button variant="ghost" disabled={busy} onClick={() => op(task, "pause", "pausing")}>
                <Icon icon={Pause} size={16} />
                {pending === "pausing" ? "Pausing…" : "Pause"}
              </Button>
            )}
            {state === "paused" && (
              <Button variant="primary" disabled={busy} onClick={() => op(task, "resume", "resuming")}>
                <Icon icon={Play} size={16} />
                {pending === "resuming" ? "Resuming…" : "Resume"}
              </Button>
            )}
            <Button variant="destructive" disabled={busy} onClick={() => op(task, "stop", "stopping")}>
              <Icon icon={Square} size={16} />
              {pending === "stopping" ? "Stopping…" : "Stop"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function TaskStateChip({ state }: { state: TaskStateMsg["state"] }) {
  const map: Record<
    TaskStateMsg["state"],
    { label: string; tone: "primary" | "tier3" | "muted" | "success" | "destructive"; icon: LucideIcon }
  > = {
    running: { label: "Running", tone: "primary", icon: CircleDot },
    waiting_approval: { label: "Waiting for you", tone: "tier3", icon: ShieldAlert },
    paused: { label: "Paused", tone: "muted", icon: Pause },
    done: { label: "Done", tone: "success", icon: Check },
    failed: { label: "Failed", tone: "destructive", icon: AlertTriangle },
  };
  const { label, tone, icon } = map[state];
  return <Chip icon={icon} label={label} tone={tone} className="task-state-chip" />;
}

// Lane chip + pin dropdown: a native <select> is the laziest accessible
// control. Shows the current lane (icon + label) and re-pins on change.
function LanePin({ task, disabled, onPin }: { task: TaskStateMsg; disabled: boolean; onPin: CardProps["pin"] }) {
  return (
    <label className="task-lane">
      <Icon icon={LANE_ICON[task.lane]} size={16} />
      <select
        className="task-lane-select"
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
