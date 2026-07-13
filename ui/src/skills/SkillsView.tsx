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
import { useHaloStore, selectSkills, selectActivities } from "../state/store";
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
  const all = Object.values(skills);

  const [filter, setFilter] = useState<Filter>("all");
  const [pending, setPending] = useState<Record<string, string>>({});
  const [trialOpenFor, setTrialOpenFor] = useState<string | null>(null);
  const prevRefs = useRef<Record<string, SkillStateMsg>>({});

  // rule 3: an op locks a skill's controls until a fresh skill_state (new
  // object reference) confirms it — same converge-on-ref-change pattern as
  // the tasks and memory views. `prev` is captured BEFORE mutating the ref so
  // the updater passed to setPending is a pure function of its closure (React
  // StrictMode double-invokes function-form updaters in dev specifically to
  // catch side effects like mutating a ref inside one — see mem/Bugs.md).
  useEffect(() => {
    const prev = prevRefs.current;
    prevRefs.current = skills;
    setPending((p) => {
      let changed = false;
      const next = { ...p };
      for (const name of Object.keys(p)) {
        if (skills[name] !== prev[name]) {
          delete next[name];
          changed = true;
        }
      }
      return changed ? next : p;
    });
  }, [skills]);

  const op = (s: SkillStateMsg, kind: "disable" | "restore" | "delete", label: string) => {
    if (pending[s.skill_name]) return;
    setPending((p) => ({ ...p, [s.skill_name]: label }));
    sendSkillOp(s.skill_name, kind);
  };

  const trial = (s: SkillStateMsg) => {
    setTrialOpenFor(s.skill_name);
    sendSkillOp(s.skill_name, "trial"); // no rule-3 lock — a dry run doesn't change skill_state
  };

  const filtered = all.filter((s) => filter === "all" || s.kind === "playbook");
  const retired = filtered.filter((s) => s.status === "retired");
  const auto = filtered.filter((s) => s.status !== "retired" && s.origin === "auto");
  const user = filtered.filter((s) => s.status !== "retired" && s.origin === "user");

  if (all.length === 0) {
    return (
      <div className="skills-empty">
        No skills yet — I create them when I notice you repeating a task. Do something a few times and watch this
        space.
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

      <div className="skills-scroll">
        <SkillGroup title="Auto-learned ✨" skills={auto} pending={pending} op={op} onTrial={trial} />
        <SkillGroup title="User-made 🛠" skills={user} pending={pending} op={op} onTrial={trial} />
        {retired.length > 0 && (
          <SkillGroup title="Retired" skills={retired} pending={pending} op={op} onTrial={trial} retiredGroup />
        )}
      </div>

      {trialOpenFor && (
        <TrialDrawer
          skillName={trialOpenFor}
          activities={activities}
          onClose={() => setTrialOpenFor(null)}
        />
      )}
    </div>
  );
}

function SkillGroup({
  title,
  skills,
  pending,
  op,
  onTrial,
  retiredGroup,
}: {
  title: string;
  skills: SkillStateMsg[];
  pending: Record<string, string>;
  op: (s: SkillStateMsg, kind: "disable" | "restore" | "delete", label: string) => void;
  onTrial: (s: SkillStateMsg) => void;
  retiredGroup?: boolean;
}) {
  if (skills.length === 0) return null;
  return (
    <section className="skills-group">
      <h3 className="skills-group-title">{title}</h3>
      <ul className="skills-list">
        {skills.map((s) => (
          <li key={s.skill_name}>
            <SkillCard skill={s} pending={pending[s.skill_name]} op={op} onTrial={onTrial} retiredGroup={retiredGroup} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function SkillCard({
  skill,
  pending,
  op,
  onTrial,
  retiredGroup,
}: {
  skill: SkillStateMsg;
  pending: string | undefined;
  op: (s: SkillStateMsg, kind: "disable" | "restore" | "delete", label: string) => void;
  onTrial: (s: SkillStateMsg) => void;
  retiredGroup?: boolean;
}) {
  const busy = pending !== undefined;
  const lowSuccess = skill.success_rate < 0.6;
  const learnedDate = formatLearned(skill.born_at);

  return (
    <div className="skill-card" data-status={skill.status}>
      <div className="skill-head">
        <Icon icon={skill.origin === "auto" ? Sparkles : Pencil} size={20} />
        <span className="skill-name">{skill.skill_name}</span>
        {skill.kind === "playbook" && <span className="skill-playbook-tag">playbook</span>}
      </div>

      <p className="skill-meta">
        used {skill.uses}× · {Math.round(skill.success_rate * 100)}% success · learned {learnedDate}
      </p>

      <div className="skill-rate-bar" data-low={lowSuccess || undefined}>
        <span style={{ width: `${Math.round(skill.success_rate * 100)}%` }} />
      </div>

      {retiredGroup && (
        <p className="skill-reason">{skill.reason ?? "Retired."}</p>
      )}

      <div className="skill-actions">
        {retiredGroup ? (
          <Button variant="ghost" disabled={busy} onClick={() => op(skill, "restore", "Restoring…")}>
            <Icon icon={RotateCcw} size={16} />
            {pending ?? "Restore"}
          </Button>
        ) : (
          <>
            <Button variant="ghost" disabled={busy} onClick={() => onTrial(skill)}>
              <Icon icon={Play} size={16} />
              Trial run
            </Button>
            {skill.status === "paused" ? (
              <Button variant="ghost" disabled={busy} onClick={() => op(skill, "restore", "Enabling…")}>
                {pending === "Enabling…" ? pending : "Enable"}
              </Button>
            ) : (
              <Button variant="ghost" disabled={busy} onClick={() => op(skill, "disable", "Pausing…")}>
                {pending === "Pausing…" ? pending : "Pause"}
              </Button>
            )}
            <Button variant="ghost" disabled={busy} onClick={() => op(skill, "delete", "Deleting…")}>
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
}: {
  skillName: string;
  activities: ActivityMsg[];
  onClose: () => void;
}) {
  const taskId = trialTaskId(skillName);
  const lines = activities.filter((a) => a.task_id === taskId);
  return (
    <div className="trial-drawer" role="dialog" aria-label={`Trial run: ${skillName}`}>
      <div className="trial-drawer-head">
        <span>Trial run — {skillName}</span>
        <button type="button" onClick={onClose} aria-label="Close">
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
