// Phase 1 Step 6 — left rail navigation. Order: Chat, Tasks, Activity,
// Memory, Skills, then a separator and Settings. Active item is marked by
// color PLUS a left indicator bar (never color alone). Styles live in
// WorkspaceRoot.css alongside the rest of the workspace shell.
// Spec: phase-1-plan.md Step 6, ui_ux/02-workspace.md.

import { AlertCircle, Activity, Brain, ListTodo, MessageSquare, Settings, Sparkles } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "../components/Icon";
import { Chip } from "../components/Chip";
import { useHaloStore, selectActiveView, selectPendingApprovalCount } from "../state/store";
import type { ActiveView } from "../state/store";

const NAV_ITEMS: { id: ActiveView; label: string; icon: LucideIcon }[] = [
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "tasks", label: "Tasks", icon: ListTodo },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "skills", label: "Skills", icon: Sparkles },
];

export function Sidebar() {
  const activeView = useHaloStore(selectActiveView);
  const setActiveView = useHaloStore((s) => s.setActiveView);
  const pendingApprovals = useHaloStore(selectPendingApprovalCount);

  function renderItem(id: ActiveView, label: string, icon: LucideIcon) {
    const active = activeView === id;
    return (
      <button
        type="button"
        className="sidebar-item"
        data-active={active || undefined}
        aria-current={active ? "page" : undefined}
        onClick={() => setActiveView(id)}
      >
        <Icon icon={icon} size={20} />
        <span>{label}</span>
        {id === "tasks" && pendingApprovals > 0 && (
          <Chip
            icon={AlertCircle}
            label={String(pendingApprovals)}
            tone="tier3"
            className="sidebar-badge"
          />
        )}
      </button>
    );
  }

  return (
    <nav className="sidebar" aria-label="Workspace views">
      <ul className="halo-list">
        {NAV_ITEMS.map((item) => (
          <li key={item.id}>{renderItem(item.id, item.label, item.icon)}</li>
        ))}
      </ul>
      <div className="sidebar-separator" role="separator" />
      {renderItem("settings", "Settings", Settings)}
    </nav>
  );
}
