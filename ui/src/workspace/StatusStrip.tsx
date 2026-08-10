// Phase 1 Step 6 — top status strip: lane chip (while a task runs), mic
// state, and a compact running-task chip with a stop button. Nothing else
// competes with it (ui_ux/02-workspace.md). Badge/chip data comes from the
// store only — no local mirrors to drift.
// Spec: phase-1-plan.md Step 6.

import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { LoaderCircle, Mic, MicOff } from "lucide-react";
import { Icon } from "../components/Icon";
import { Chip } from "../components/Chip";
import { Button } from "../components/Button";
import {
  useHaloStore,
  selectActiveTask,
  selectVoice,
  selectCapabilities,
  selectTasks,
  selectOperationErrors,
} from "../state/store";
import { LANE_LABEL, LANE_ICON, formatTaskProgress } from "../lib/lanes";
import { usePendingConfirm } from "../lib/usePendingConfirm";
import type { TaskStateMsg } from "../ipc/contract";

const MIC_LABEL: Record<string, string> = {
  idle: "Mic idle",
  wake: "Waking",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  muted: "Muted",
};

const TASK_STATE_LABEL: Record<TaskStateMsg["state"], string> = {
  waiting: "Queued",
  running: "Running",
  stopping: "Stopping",
  paused: "Paused",
  waiting_approval: "Waiting for you",
  stopped: "Stopped",
  done: "Done",
  failed: "Failed",
};

interface StatusStripProps {
  sendTaskOp: (op: "pause" | "resume" | "stop", task_id?: string) => void;
}

interface HotkeyStatus {
  shortcut: string;
  notice: string | null;
}

export function StatusStrip({ sendTaskOp }: StatusStripProps) {
  const activeTask = useHaloStore(selectActiveTask);
  const voice = useHaloStore(selectVoice);
  const capabilities = useHaloStore(selectCapabilities);
  const wsStatus = useHaloStore((state) => state.connection.wsStatus);
  const tasks = useHaloStore(selectTasks);
  const operationErrors = useHaloStore(selectOperationErrors);
  const { pending, failures, begin } = usePendingConfirm(tasks, operationErrors);
  // rule 3: the stop button disables on press and resolves only once the
  // Brain confirms via task_state (the task leaves "running" or vanishes) —
  // never optimistically.
  const [hotkeyNotice, setHotkeyNotice] = useState<string | null>(null);
  const progress = activeTask ? formatTaskProgress(activeTask) : "";
  const pendingStop = activeTask ? pending[activeTask.task_id] === "stopping" : false;

  useEffect(() => {
    if (!isTauri()) return;
    void invoke<HotkeyStatus>("hotkey_status")
      .then((status) => setHotkeyNotice(status.notice))
      .catch(() => setHotkeyNotice("Halo could not read the summon hotkey. Open the workspace from the tray."));
  }, []);

  return (
    <div className="status-strip">
      {activeTask && (
        <Chip
          icon={LANE_ICON[activeTask.lane]}
          label={LANE_LABEL[activeTask.lane]}
          tone={activeTask.lane === 1 ? "primary" : "default"}
        />
      )}
      <span className="status-mic">
        <Icon icon={capabilities.voiceInput === true && voice.state !== "muted" ? Mic : MicOff} size={16} />
        <span>
          {capabilities.voiceInput === false || wsStatus === "unavailable"
            ? "Voice unavailable"
            : capabilities.voiceInput == null
              ? "Checking voice"
              : MIC_LABEL[voice.state] ?? voice.state}
        </span>
      </span>
      {hotkeyNotice && <span className="status-hotkey-note" role="status">{hotkeyNotice}</span>}
      {activeTask && (
        <div className="status-task-group">
          <div
            className="status-task-chip"
            data-state={pendingStop ? "stopping" : activeTask.state}
            role="status"
            aria-label="Task progress"
            aria-live="polite"
            aria-atomic="true"
          >
            <span className="status-task-spinner" aria-hidden="true">
              <Icon icon={LoaderCircle} size={16} />
            </span>
            <span className="status-task-state">
              {pendingStop ? "Stopping" : TASK_STATE_LABEL[activeTask.state]}
            </span>
            <span className="status-task-title">
              {activeTask.title ?? "Working"}
              {progress && ` · ${progress}`}
            </span>
          </div>
          {activeTask.state !== "stopping" && (
            <Button
              variant="destructive"
              disabled={capabilities.taskControls !== true || pending[activeTask.task_id] !== undefined}
              onClick={() => {
                if (
                  begin(
                    activeTask.task_id,
                    "stopping",
                    (value) => value == null
                      || value.state === "stopped"
                      || value.state === "done"
                      || value.state === "failed",
                    "task_op",
                  )
                ) {
                  sendTaskOp("stop", activeTask.task_id);
                }
              }}
              title={capabilities.taskControls === false ? "Task controls are not available in this build" : undefined}
            >
              {pendingStop ? "Stopping…" : "Stop"}
            </Button>
          )}
          {failures[activeTask.task_id] && (
            <span className="status-task-error" role="alert">
              {failures[activeTask.task_id]}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
