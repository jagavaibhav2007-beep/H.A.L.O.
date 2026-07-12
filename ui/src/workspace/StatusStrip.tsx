// Phase 1 Step 6 — top status strip: lane chip (while a task runs), mic
// state, and a compact running-task chip with a stop button. Nothing else
// competes with it (ui_ux/02-workspace.md). Badge/chip data comes from the
// store only — no local mirrors to drift.
// Spec: phase-1-plan.md Step 6.

import { useEffect, useState } from "react";
import { Mic, MicOff, ShieldAlert, Users, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "../components/Icon";
import { Chip } from "../components/Chip";
import { Button } from "../components/Button";
import { useHaloStore, selectRunningTask, selectVoice } from "../state/store";

const LANE_LABEL: Record<1 | 2 | 3, string> = { 1: "Fast", 2: "Takeover", 3: "Sandbox" };
const LANE_ICON: Record<1 | 2 | 3, LucideIcon> = { 1: Zap, 2: Users, 3: ShieldAlert };

const MIC_LABEL: Record<string, string> = {
  idle: "Mic idle",
  wake: "Waking",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  muted: "Muted",
};

interface StatusStripProps {
  sendTaskOp: (op: "pause" | "resume" | "stop", task_id?: string) => void;
}

export function StatusStrip({ sendTaskOp }: StatusStripProps) {
  const runningTask = useHaloStore(selectRunningTask);
  const voice = useHaloStore(selectVoice);
  // rule 3: the stop button disables on press and resolves only once the
  // Brain confirms via task_state (the task leaves "running" or vanishes) —
  // never optimistically.
  const [stoppingId, setStoppingId] = useState<string | null>(null);

  useEffect(() => {
    if (!stoppingId) return;
    const stillRunning = runningTask?.task_id === stoppingId && runningTask.state === "running";
    if (!stillRunning) setStoppingId(null);
  }, [runningTask, stoppingId]);

  return (
    <div className="status-strip">
      {runningTask && (
        <Chip
          icon={LANE_ICON[runningTask.lane]}
          label={LANE_LABEL[runningTask.lane]}
          tone={runningTask.lane === 1 ? "primary" : "default"}
        />
      )}
      <span className="status-mic">
        <Icon icon={voice.state === "muted" ? MicOff : Mic} size={16} />
        <span>{MIC_LABEL[voice.state] ?? voice.state}</span>
      </span>
      {runningTask && (
        <div className="status-task-chip">
          <span className="status-task-title">
            {runningTask.title ?? "Working"}
            {runningTask.step != null && runningTask.steps_total != null
              ? ` · step ${runningTask.step}/${runningTask.steps_total}`
              : ""}
            {runningTask.step_label ? ` — ${runningTask.step_label}` : ""}
          </span>
          <Button
            variant="destructive"
            disabled={stoppingId === runningTask.task_id}
            onClick={() => {
              setStoppingId(runningTask.task_id);
              sendTaskOp("stop", runningTask.task_id);
            }}
          >
            {stoppingId === runningTask.task_id ? "Stopping…" : "Stop"}
          </Button>
        </div>
      )}
    </div>
  );
}
