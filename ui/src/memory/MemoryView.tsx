// Phase 1 Step 12 — Memory panel (ui_ux/07-memory-panel.md): the inspectable
// second brain, fed by `belief_state`, driving `memory_edit`. Active beliefs
// grouped by kind and salience-sorted; provenance is the star (🗣 you said vs
// ✨ Halo inferred). Edit supersedes into a user-stated belief; delete is soft
// (5s undo toast → archived, recoverable forever); superseded chains expand.
// Nothing hard-deletes, and the store never mutates a belief locally — every
// change waits for the confirming belief_state (rule 3).

import { useEffect, useMemo, useRef, useState } from "react";
import { Archive, ChevronDown, ChevronRight, Pencil, RotateCcw, Sparkles, Trash2, User } from "lucide-react";
import { Icon } from "../components/Icon";
import { Chip } from "../components/Chip";
import { Button } from "../components/Button";
import { useHaloStore, selectBeliefs } from "../state/store";
import type { BeliefStateMsg, MemoryEditMsg } from "../ipc/contract";
import "./MemoryView.css";

const KIND_ORDER: BeliefStateMsg["kind"][] = ["preference", "project", "workflow", "decision", "lesson"];
const KIND_LABEL: Record<BeliefStateMsg["kind"], string> = {
  preference: "Preferences",
  project: "Projects",
  workflow: "Workflows",
  decision: "Decisions",
  lesson: "Lessons",
};

const DELETE_UNDO_MS = 5000;

interface MemoryViewProps {
  sendMemoryEdit: (belief_id: string, op: MemoryEditMsg["op"], text?: string) => void;
}

export function MemoryView({ sendMemoryEdit }: MemoryViewProps) {
  const beliefs = useHaloStore(selectBeliefs);
  const all = useMemo(() => Object.values(beliefs), [beliefs]);

  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [pending, setPending] = useState<Record<string, string>>({});
  const [pendingDelete, setPendingDelete] = useState<{ id: string; text: string } | null>(null);
  const deleteTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // rule 3: a belief's op locks until a fresh belief_state (new object ref)
  // confirms it. Same converge-on-ref-change pattern as the tasks view.
  const prevRefs = useRef<Record<string, BeliefStateMsg>>({});
  useEffect(() => {
    setPending((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const id of Object.keys(prev)) {
        if (beliefs[id] !== prevRefs.current[id]) {
          delete next[id];
          changed = true;
        }
      }
      prevRefs.current = beliefs;
      return changed ? next : prev;
    });
  }, [beliefs]);

  useEffect(() => () => clearTimeout(deleteTimer.current), []);

  // Predecessors of an active belief (the superseded chain behind it).
  const historyOf = (id: string) => all.filter((b) => b.superseded_by === id);

  const q = query.trim().toLowerCase();
  const matches = (b: BeliefStateMsg) => !q || b.text.toLowerCase().includes(q);

  const saveEdit = (id: string, text: string) => {
    if (pending[id]) return;
    setPending((p) => ({ ...p, [id]: "Saving…" }));
    sendMemoryEdit(id, "edit", text);
  };

  const requestDelete = (b: BeliefStateMsg) => {
    // Soft: the card stays (no local mutation) while a 5s toast offers undo;
    // at expiry the delete is sent and the confirming `belief_state: archived`
    // moves it to the archived filter. Undo before then simply never sends.
    clearTimeout(deleteTimer.current);
    setPendingDelete({ id: b.belief_id, text: b.text });
    deleteTimer.current = setTimeout(() => {
      setPending((p) => ({ ...p, [b.belief_id]: "Deleting…" }));
      sendMemoryEdit(b.belief_id, "delete");
      setPendingDelete(null);
    }, DELETE_UNDO_MS);
  };

  const undoDelete = () => {
    clearTimeout(deleteTimer.current);
    setPendingDelete(null);
  };

  const restore = (id: string) => {
    if (pending[id]) return;
    setPending((p) => ({ ...p, [id]: "Restoring…" }));
    sendMemoryEdit(id, "restore");
  };

  const archived = all.filter((b) => b.status === "archived").filter(matches);
  const active = all.filter((b) => b.status === "active").filter(matches);

  return (
    <div className="memory-view">
      <div className="memory-toolbar">
        <input
          className="memory-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search what I remember…"
        />
        <button
          type="button"
          className="memory-archived-toggle"
          data-active={showArchived || undefined}
          onClick={() => setShowArchived((v) => !v)}
        >
          <Icon icon={Archive} size={16} />
          {showArchived ? "Back to active" : `Archived (${all.filter((b) => b.status === "archived").length})`}
        </button>
      </div>

      <div className="memory-scroll">
        {showArchived ? (
          archived.length === 0 ? (
            <div className="memory-empty">Nothing archived.</div>
          ) : (
            <ul className="memory-list">
              {archived.map((b) => (
                <li key={b.belief_id}>
                  <BeliefCard
                    belief={b}
                    history={[]}
                    pending={pending[b.belief_id]}
                    archivedView
                    onEdit={saveEdit}
                    onDelete={requestDelete}
                    onRestore={restore}
                  />
                </li>
              ))}
            </ul>
          )
        ) : active.length === 0 ? (
          <div className="memory-empty">
            {all.length === 0 ? "I haven't learned anything about you yet." : "No memories match that search."}
          </div>
        ) : (
          KIND_ORDER.map((kind) => {
            const group = active
              .filter((b) => b.kind === kind)
              .sort((a, c) => c.salience - a.salience);
            if (group.length === 0) return null;
            return (
              <section key={kind} className="memory-group">
                <h3 className="memory-group-title">{KIND_LABEL[kind]}</h3>
                <ul className="memory-list">
                  {group.map((b) => (
                    <li key={b.belief_id}>
                      <BeliefCard
                        belief={b}
                        history={historyOf(b.belief_id)}
                        pending={pending[b.belief_id]}
                        onEdit={saveEdit}
                        onDelete={requestDelete}
                        onRestore={restore}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            );
          })
        )}
      </div>

      {pendingDelete && (
        <div className="memory-toast" role="status">
          <span>Deleted “{truncate(pendingDelete.text)}”.</span>
          <button type="button" onClick={undoDelete}>
            <Icon icon={RotateCcw} size={16} />
            Undo
          </button>
        </div>
      )}
    </div>
  );
}

interface CardProps {
  belief: BeliefStateMsg;
  history: BeliefStateMsg[];
  pending: string | undefined;
  archivedView?: boolean;
  onEdit: (id: string, text: string) => void;
  onDelete: (b: BeliefStateMsg) => void;
  onRestore: (id: string) => void;
}

function BeliefCard({ belief, history, pending, archivedView, onEdit, onDelete, onRestore }: CardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(belief.text);
  const [showHistory, setShowHistory] = useState(false);
  const busy = pending !== undefined;
  const inferred = belief.provenance === "inferred";

  return (
    <div className="belief-card" data-provenance={belief.provenance}>
      {editing ? (
        <>
          <textarea
            className="belief-edit"
            value={draft}
            onChange={(e) => setDraft(e.currentTarget.value)}
            aria-label="Edit memory"
            rows={2}
          />
          <div className="belief-actions">
            <Button
              variant="primary"
              disabled={busy || !draft.trim()}
              onClick={() => {
                onEdit(belief.belief_id, draft.trim());
                setEditing(false);
              }}
            >
              {pending ?? "Save"}
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="belief-text">{belief.text}</p>
          <div className="belief-meta">
            <Chip
              icon={inferred ? Sparkles : User}
              label={inferred ? "Halo inferred" : "you said"}
              tone={inferred ? "primary" : "success"}
            />
            <span className="belief-salience" aria-label={`salience ${belief.salience.toFixed(2)}`}>
              <span style={{ width: `${Math.round(belief.salience * 100)}%` }} />
            </span>
          </div>

          {history.length > 0 && (
            <div className="belief-history">
              <button type="button" className="belief-history-toggle" onClick={() => setShowHistory((v) => !v)}>
                <Icon icon={showHistory ? ChevronDown : ChevronRight} size={16} />
                {history.length} earlier {history.length === 1 ? "version" : "versions"}
              </button>
              {showHistory && (
                <ul className="belief-history-list">
                  {history.map((h) => (
                    <li key={h.belief_id}>
                      <span>{h.text}</span>
                      <button type="button" disabled={busy} onClick={() => onRestore(h.belief_id)}>
                        Restore
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="belief-actions">
            {archivedView ? (
              <Button variant="ghost" disabled={busy} onClick={() => onRestore(belief.belief_id)}>
                <Icon icon={RotateCcw} size={16} />
                {pending ?? "Restore"}
              </Button>
            ) : (
              <>
                <Button variant="ghost" disabled={busy} onClick={() => { setDraft(belief.text); setEditing(true); }}>
                  <Icon icon={Pencil} size={16} />
                  Edit
                </Button>
                <Button variant="ghost" disabled={busy} onClick={() => onDelete(belief)}>
                  <Icon icon={Trash2} size={16} />
                  {pending ?? "Delete"}
                </Button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function truncate(s: string): string {
  return s.length > 48 ? s.slice(0, 47) + "…" : s;
}
