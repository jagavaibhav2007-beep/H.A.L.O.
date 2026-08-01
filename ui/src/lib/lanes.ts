import { ShieldAlert, Users, Zap, type LucideIcon } from "lucide-react";
import type { TaskStateMsg } from "../ipc/contract";

export const LANE_LABEL: Record<1 | 2 | 3, string> = { 1: "Fast", 2: "Takeover", 3: "Sandbox" };
export const LANE_ICON: Record<1 | 2 | 3, LucideIcon> = { 1: Zap, 2: Users, 3: ShieldAlert };

// The shared "step N/M — label" fragment. Callers prepend the title themselves
// (TasksView renders it separately in the card head; the capsule/strip inline it).
export function formatTaskProgress(task: TaskStateMsg): string {
  const step =
    task.step != null && task.steps_total != null ? `step ${task.step}/${task.steps_total}` : null;
  return [step, task.step_label].filter(Boolean).join(" — ");
}
