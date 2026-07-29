// Phase 1 Step 13 — Skills panel (ui_ux/08-skills-panel.md): Halo's
// self-taught abilities, visible/trialable/killable. Cards in Auto-learned ✨
// / User-made 🛠 groups, a Playbooks filter, a success-rate bar that reddens
// under the 60% auto-retire threshold before it fires, and a trial-run drawer
// fed by narrated activity for the skill's synthetic task_id. All ops
// (trial/disable/restore/delete) are rule-3 locked — the store never mutates
// a skill locally; a fresh skill_state is the only source of truth.

import { useEffect, useRef, useState } from "react";
import { Pencil, Play, RotateCcw, Sparkles, Trash2 } from "lucide-react";
import { Icon } from "../components/Icon";
import { Button } from "../components/Button";
import {
  useHaloStore,
  selectSkills,
  selectActivities,
  selectCapabilities,
  selectOperationErrors,
} from "../state/store";
import { usePendingConfirm } from "../lib/usePendingConfirm";
import type { ActivityMsg, SkillOpMsg, SkillStateMsg } from "../ipc/contract";
import "./SkillsView.css";

type Filter = "all" | "playbooks";

interface SkillsViewProps {
  sendSkillOp: (skill_name: string, op: SkillOpMsg["op"]) => void;
}

function trialTaskId(name: string): string {
  return `skill-trial-${name}`;
}

export function SkillsView({ sendSkillOp }: SkillsViewProps) {
  const skills = useHaloStore(selectSkills);
  const activities = useHaloStore(selectActivities);
  const capabilities = useHaloStore(selectCapabilities);
  const operationErrors = useHaloStore(selectOperationErrors);
  const controlsAvailable = capabilities.skillControls === true;
  const all = Object.values(skills);

  const [filter, setFilter] = useState<Filter>("all");
  const { pending, failures, begin } = usePendingConfirm(skills, operationErrors);
  const [trialOpenFor, setTrialOpenFor] = useState<string | null>(null);
  const trialOpenerRef = useRef<HTMLButtonElement | null>(null);

  const op = (s: SkillStateMsg, kind: "disable" | "restore" | "delete", label: string) => {
    if (!controlsAvailable) return;
    const expected = kind === "disable" ? "paused" : kind === "restore" ? "active" : "retired";
    if (begin(s.skill_name, label, (value) => value?.status === expected, "skill_op"))
      sendSkillOp(s.skill_name, kind);
  };

  const trial = (s: SkillStateMsg, opener: HTMLButtonElement) => {
    if (!controlsAvailable) return;
    trialOpenerRef.current = opener;
    setTrialOpenFor(s.skill_name);
    sendSkillOp(s.skill_name, "trial"); // no rule-3 lock — a dry run doesn't change skill_state
  };

  const filtered = all.filter((s) => filter === "all" || s.kind === "playbook");
  const retired = filtered.filter((s) => s.status === "retired");
  const auto = filtered.filter((s) => s.status !== "retired" && s.origin === "auto");
  const user = filtered.filter((s) => s.status !== "retired" && s.origin === "user");

  if (all.length === 0) {
    return (
      <div className="halo-empty skills-empty">
        {capabilities.skillControls === false
          ? "Skills are not available in this build."
          : "No skills yet — I create them when I notice you repeating a task. Do something a few times and watch this space."}
      </div>
    );
  }

  return (
    <div className="skills-view">
      <div className="skills-toolbar">
        <div className="skills-filter" role="tablist" aria-label="Skill filter">
          <button type="button" role="tab" aria-selected={filter === "all"} onClick={() => setFilter("all")}>
            All
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={filter === "playbooks"}
            onClick={() => setFilter("playbooks")}
          >
            Playbooks
          </button>
        </div>
      </div>

      <div className="halo-scroll">
        <SkillGroup title="Auto-learned ✨" skills={auto} pending={pending} failures={failures} controlsAvailable={controlsAvailable} op={op} onTrial={trial} />
        <SkillGroup title="User-made 🛠" skills={user} pending={pending} failures={failures} controlsAvailable={controlsAvailable} op={op} onTrial={trial} />
        {retired.length > 0 && (
          <SkillGroup title="Retired" skills={retired} pending={pending} failures={failures} controlsAvailable={controlsAvailable} op={op} onTrial={trial} retiredGroup />
        )}
      </div>

      {trialOpenFor && (
        <TrialDrawer
          skillName={trialOpenFor}
          activities={activities}
          onClose={() => setTrialOpenFor(null)}
          restoreFocusTo={trialOpenerRef.current}
        />
      )}
    </div>
  );
}

function SkillGroup({
  title,
  skills,
  pending,
  failures,
  controlsAvailable,
  op,
  onTrial,
  retiredGroup,
}: {
  title: string;
  skills: SkillStateMsg[];
  pending: Record<string, string>;
  failures: Record<string, string>;
  controlsAvailable: boolean;
  op: (s: SkillStateMsg, kind: "disable" | "restore" | "delete", label: string) => void;
  onTrial: (s: SkillStateMsg, opener: HTMLButtonElement) => void;
  retiredGroup?: boolean;
}) {
  if (skills.length === 0) return null;
  return (
    <section className="skills-group">
      <h3 className="halo-group-title skills-group-title">{title}</h3>
      <ul className="halo-list skills-list">
        {skills.map((s) => (
          <li key={s.skill_name}>
            <SkillCard
              skill={s}
              pending={pending[s.skill_name]}
              failure={failures[s.skill_name]}
              controlsAvailable={controlsAvailable}
              op={op}
              onTrial={onTrial}
              retiredGroup={retiredGroup}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

function SkillCard({
  skill,
  pending,
  failure,
  controlsAvailable,
  op,
  onTrial,
  retiredGroup,
}: {
  skill: SkillStateMsg;
  pending: string | undefined;
  failure: string | undefined;
  controlsAvailable: boolean;
  op: (s: SkillStateMsg, kind: "disable" | "restore" | "delete", label: string) => void;
  onTrial: (s: SkillStateMsg, opener: HTMLButtonElement) => void;
  retiredGroup?: boolean;
}) {
  const busy = pending !== undefined;
  const lowSuccess = skill.success_rate < 0.6;
  const learnedDate = formatLearned(skill.born_at);

  return (
    <div className="halo-card skill-card" data-status={skill.status}>
      <div className="skill-head">
        <Icon icon={skill.origin === "auto" ? Sparkles : Pencil} size={20} />
        <span className="skill-name">{skill.skill_name}</span>
        {skill.kind === "playbook" && <span className="skill-playbook-tag">playbook</span>}
      </div>

      <p className="skill-meta">
        used {skill.uses}× · {Math.round(skill.success_rate * 100)}% success · learned {learnedDate}
      </p>

      <div className="halo-meter skill-rate-bar" data-low={lowSuccess || undefined}>
        <span style={{ width: `${Math.round(skill.success_rate * 100)}%` }} />
      </div>

      {retiredGroup && (
        <p className="skill-reason">{skill.reason ?? "Retired."}</p>
      )}
      {failure && <p className="skill-reason" role="alert">{failure}</p>}

      <div className="skill-actions">
        {retiredGroup ? (
          <Button variant="ghost" disabled={busy || !controlsAvailable} onClick={() => op(skill, "restore", "Restoring…")}>
            <Icon icon={RotateCcw} size={16} />
            {pending ?? "Restore"}
          </Button>
        ) : (
          <>
            <Button variant="ghost" disabled={busy || !controlsAvailable} onClick={(event) => onTrial(skill, event.currentTarget)}>
              <Icon icon={Play} size={16} />
              Trial run
            </Button>
            {skill.status === "paused" ? (
              <Button variant="ghost" disabled={busy || !controlsAvailable} onClick={() => op(skill, "restore", "Enabling…")}>
                {pending === "Enabling…" ? pending : "Enable"}
              </Button>
            ) : (
              <Button variant="ghost" disabled={busy || !controlsAvailable} onClick={() => op(skill, "disable", "Pausing…")}>
                {pending === "Pausing…" ? pending : "Pause"}
              </Button>
            )}
            <Button variant="ghost" disabled={busy || !controlsAvailable} onClick={() => op(skill, "delete", "Deleting…")}>
              <Icon icon={Trash2} size={16} />
              {pending === "Deleting…" ? pending : "Delete"}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function TrialDrawer({
  skillName,
  activities,
  onClose,
  restoreFocusTo,
}: {
  skillName: string;
  activities: ActivityMsg[];
  onClose: () => void;
  restoreFocusTo: HTMLButtonElement | null;
}) {
  const taskId = trialTaskId(skillName);
  const lines = activities.filter((a) => a.task_id === taskId);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    return () => {
      if (restoreFocusTo?.isConnected) restoreFocusTo.focus();
    };
  }, [restoreFocusTo]);

  function onDialogKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
    if (focusable.length === 0) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      ref={dialogRef}
      className="trial-drawer"
      role="dialog"
      aria-modal="true"
      aria-label={`Trial run — ${skillName}`}
      tabIndex={-1}
      onKeyDown={onDialogKeyDown}
    >
      <div className="trial-drawer-head">
        <span>Trial run — {skillName}</span>
        <button ref={closeRef} type="button" onClick={onClose} aria-label="Close trial run">
          ×
        </button>
      </div>
      <div className="trial-drawer-body">
        {lines.length === 0 ? (
          <p className="trial-drawer-waiting">Running…</p>
        ) : (
          <ul>
            {lines.map((l) => (
              <li key={l.id}>{l.text}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function formatLearned(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "recently";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
