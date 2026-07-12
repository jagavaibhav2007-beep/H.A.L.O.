// Phase 1 Step 9 — Activity feed (ui_ux/06-watching-halo-work.md): the
// flight recorder. One virtualized, newest-first timeline of every `activity`
// frame, with tier/lane/task/undoable filters + text search (all client-side
// over the store's capped slice), and an Undo button wherever an inverse
// exists. Spec: phase-1-plan.md Step 9.

import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Activity as ActivityIcon, ArrowDown, RotateCcw, ShieldAlert, Users, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "../components/Icon";
import { Chip } from "../components/Chip";
import { useHaloStore, selectActivities } from "../state/store";
import type { ActivityMsg } from "../ipc/contract";
import "./ActivityFeed.css";

const LANE_LABEL: Record<1 | 2 | 3, string> = { 1: "Fast", 2: "Takeover", 3: "Sandbox" };
const LANE_ICON: Record<1 | 2 | 3, LucideIcon> = { 1: Zap, 2: Users, 3: ShieldAlert };

type TierFilter = "all" | "1" | "2" | "3";
type LaneFilter = "all" | "1" | "2" | "3";

function formatTs(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString();
}

interface ActivityFeedProps {
  sendUndo: (undo_token: string) => void;
}

export function ActivityFeed({ sendUndo }: ActivityFeedProps) {
  const activities = useHaloStore(selectActivities);

  const [tier, setTier] = useState<TierFilter>("all");
  const [lane, setLane] = useState<LaneFilter>("all");
  const [task, setTask] = useState<string>("all");
  const [undoableOnly, setUndoableOnly] = useState(false);
  const [query, setQuery] = useState("");

  // Undo is a rule-3 surface: a click disables the button (pending) and only
  // resolves when the reversal `activity` (undoable:false, same task_id)
  // arrives — never optimistically. `sinceLen` scopes the search to entries
  // that arrive after the click so a pre-existing reversal can't false-resolve.
  const [pending, setPending] = useState<Record<string, { taskId: string; sinceLen: number }>>({});
  const [doneUndo, setDoneUndo] = useState<Set<string>>(new Set());

  const scrollRef = useRef<HTMLDivElement>(null);

  const taskIds = useMemo(() => [...new Set(activities.map((a) => a.task_id))], [activities]);

  // Newest-first, filtered. Reverse a shallow copy — the store keeps arrival
  // order (D7) and must never be mutated.
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    // .filter returns a fresh array, so .reverse() (newest-first) never
    // mutates the store's arrival-order slice (D7).
    return activities
      .filter(
        (a) =>
          (tier === "all" || String(a.tier ?? "") === tier) &&
          (lane === "all" || String(a.lane ?? "") === lane) &&
          (task === "all" || a.task_id === task) &&
          (!undoableOnly || a.undoable) &&
          (!q || a.text.toLowerCase().includes(q)),
      )
      .reverse();
  }, [activities, tier, lane, task, undoableOnly, query]);

  // Resolve any pending undo whose reversal has now landed (see comment above).
  useEffect(() => {
    const resolved = Object.entries(pending)
      .filter(([, info]) =>
        activities.slice(info.sinceLen).some((a) => a.task_id === info.taskId && !a.undoable),
      )
      .map(([token]) => token);
    if (resolved.length === 0) return;
    setPending((prev) => {
      const next = { ...prev };
      for (const t of resolved) delete next[t];
      return next;
    });
    setDoneUndo((prev) => {
      const next = new Set(prev);
      for (const t of resolved) next.add(t);
      return next;
    });
  }, [activities, pending]);

  function onUndo(token: string, taskId: string) {
    // No double-send (rule 3 / edge case): a token already pending is ignored.
    if (pending[token] || doneUndo.has(token)) return;
    setPending((prev) => ({ ...prev, [token]: { taskId, sinceLen: activities.length } }));
    sendUndo(token);
  }

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 52,
    overscan: 12,
  });

  // "new activity ↓" pill: only when the user has scrolled away from the top
  // (where the newest lands). At the top, new rows simply appear — never yank
  // someone reading history back down (rule 6).
  const [hasNew, setHasNew] = useState(false);
  const prevLenRef = useRef(activities.length);
  useEffect(() => {
    if (activities.length <= prevLenRef.current) {
      prevLenRef.current = activities.length;
      return;
    }
    prevLenRef.current = activities.length;
    const el = scrollRef.current;
    if (el && el.scrollTop > 24) setHasNew(true);
  }, [activities.length]);

  function scrollToNewest() {
    scrollRef.current?.scrollTo({ top: 0 });
    setHasNew(false);
  }

  const items = virtualizer.getVirtualItems();

  return (
    <div className="feed">
      <div className="feed-filters">
        <select className="feed-select" value={tier} onChange={(e) => setTier(e.target.value as TierFilter)}>
          <option value="all">All tiers</option>
          <option value="1">Tier 1</option>
          <option value="2">Tier 2</option>
          <option value="3">Tier 3</option>
        </select>
        <select className="feed-select" value={lane} onChange={(e) => setLane(e.target.value as LaneFilter)}>
          <option value="all">All lanes</option>
          <option value="1">Fast</option>
          <option value="2">Takeover</option>
          <option value="3">Sandbox</option>
        </select>
        <select className="feed-select" value={task} onChange={(e) => setTask(e.target.value)}>
          <option value="all">All tasks</option>
          {taskIds.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
        <label className="feed-toggle">
          <input type="checkbox" checked={undoableOnly} onChange={(e) => setUndoableOnly(e.target.checked)} />
          Undoable only
        </label>
        <input
          className="feed-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search the log…"
        />
      </div>

      <div className="feed-scroll" ref={scrollRef}>
        {rows.length === 0 ? (
          <div className="feed-empty">
            {activities.length === 0
              ? "Nothing yet — everything I do will show up here."
              : "No entries match these filters."}
          </div>
        ) : (
          <div className="feed-list" style={{ height: virtualizer.getTotalSize() }}>
            {items.map((vi) => {
              const a = rows[vi.index];
              return (
                <div
                  key={a.id}
                  className="feed-row"
                  data-index={vi.index}
                  ref={virtualizer.measureElement}
                  style={{ transform: `translateY(${vi.start}px)` }}
                >
                  <FeedRow
                    activity={a}
                    pending={!!a.undo_token && !!pending[a.undo_token]}
                    done={!!a.undo_token && doneUndo.has(a.undo_token)}
                    onUndo={onUndo}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>

      {hasNew && (
        <button type="button" className="feed-new-pill" onClick={scrollToNewest}>
          <Icon icon={ArrowDown} size={16} />
          New activity
        </button>
      )}
    </div>
  );
}

function FeedRow({
  activity,
  pending,
  done,
  onUndo,
}: {
  activity: ActivityMsg;
  pending: boolean;
  done: boolean;
  onUndo: (token: string, taskId: string) => void;
}) {
  const { text, tier, lane, undoable, undo_token, task_id, ts } = activity;
  return (
    <div className="feed-entry">
      <Icon icon={ActivityIcon} size={16} className="feed-entry-icon" />
      <div className="feed-entry-body">
        <span className="feed-entry-text">{text}</span>
        <div className="feed-entry-meta">
          {tier != null && (
            <Chip
              icon={tier === 3 ? ShieldAlert : ActivityIcon}
              label={`Tier ${tier}`}
              tone={tier === 3 ? "tier3" : "muted"}
            />
          )}
          {lane != null && <Chip icon={LANE_ICON[lane]} label={LANE_LABEL[lane]} tone={lane === 1 ? "primary" : "default"} />}
          <span className="feed-entry-ts">{formatTs(ts)}</span>
        </div>
      </div>
      <div className="feed-entry-action">
        {undoable && undo_token ? (
          <button
            type="button"
            className="feed-undo"
            disabled={pending || done}
            onClick={() => onUndo(undo_token, task_id)}
          >
            <Icon icon={RotateCcw} size={16} />
            {done ? "Undone" : pending ? "Undoing…" : "Undo"}
          </button>
        ) : (
          <span className="feed-not-reversible">not reversible</span>
        )}
      </div>
    </div>
  );
}
