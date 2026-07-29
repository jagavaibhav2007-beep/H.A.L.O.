// Multi-conversation tab strip above the chat view. Each tab is one Brain
// conversation_id, which is also its LangGraph thread_id — two tabs share no
// context, which is the entire point (ui_ux/02-workspace.md, 03-chat.md).
//
// Registry logic is pure (state/conversations.ts); this file is presentation +
// keyboard. Open/close/rename are local UI state with no confirming frame, so
// rule 3 (lock-until-confirm) does NOT apply — they take effect immediately.

import { useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";
import { Icon } from "../components/Icon";
import { useHaloStore, selectChats, selectApprovals } from "../state/store";
import "./ChatTabs.css";

export function ChatTabs() {
  const chats = useHaloStore(selectChats);
  const approvals = useHaloStore(selectApprovals);
  const setActiveConversation = useHaloStore((s) => s.setActiveConversation);
  const newConversation = useHaloStore((s) => s.newConversation);
  const closeConversation = useHaloStore((s) => s.closeConversation);
  const deleteConversation = useHaloStore((s) => s.deleteConversation);
  const renameConversation = useHaloStore((s) => s.renameConversation);

  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const stripRef = useRef<HTMLDivElement>(null);

  const meta = (id: string) => chats.all.find((c) => c.id === id);
  const recent = chats.all.filter((c) => !chats.open.includes(c.id));
  // A pending Tier-3 approval is a "you must decide" signal, not "new text" —
  // it gets its own louder marker (--tier-3) rather than the unread dot.
  const awaiting = new Set(
    Object.values(approvals)
      .map((a) => a.conversation_id)
      .filter((id): id is string => Boolean(id)),
  );

  // Roving arrow-key navigation across the tablist (WAI-ARIA tabs pattern).
  function onTabKeyDown(e: React.KeyboardEvent, index: number) {
    const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    const next = (index + delta + chats.open.length) % chats.open.length;
    setActiveConversation(chats.open[next]);
    // The newly-selected tab mounts as the focusable one; focus it next frame.
    requestAnimationFrame(() =>
      stripRef.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus(),
    );
  }

  return (
    <div className="chat-tabs" ref={stripRef}>
      <div className="chat-tabs-strip" role="tablist" aria-label="Conversations">
        {chats.open.map((id, i) => {
          const m = meta(id);
          if (!m) return null;
          const active = id === chats.activeId;
          const needsApproval = awaiting.has(id);
          const unread = !active && m.unread;
          return (
            <div key={id} className="chat-tab-wrap" data-active={active || undefined}>
              {renaming === id ? (
                <RenameInput
                  initial={m.title}
                  onDone={(title) => {
                    if (title) renameConversation(id, title);
                    setRenaming(null);
                  }}
                />
              ) : (
                <button
                  type="button"
                  role="tab"
                  aria-selected={active}
                  tabIndex={active ? 0 : -1}
                  className="chat-tab"
                  title={m.title}
                  onClick={() => setActiveConversation(id)}
                  onDoubleClick={() => setRenaming(id)}
                  onKeyDown={(e) => {
                    if (e.key === "F2") {
                      e.preventDefault(); // keyboard route to rename (double-click is mouse-only)
                      setRenaming(id);
                    } else {
                      onTabKeyDown(e, i);
                    }
                  }}
                >
                  {needsApproval && <span className="chat-tab-dot" data-kind="approval" aria-hidden="true" />}
                  {!needsApproval && unread && <span className="chat-tab-dot" aria-hidden="true" />}
                  <span className="chat-tab-title">{m.title}</span>
                  {/* Dots are decorative; the state reaches assistive tech as text. */}
                  {needsApproval && <span className="halo-sr-only"> — waiting for your approval</span>}
                  {!needsApproval && unread && <span className="halo-sr-only"> — new reply</span>}
                </button>
              )}
              <button
                type="button"
                className="chat-tab-close"
                aria-label={`Close ${m.title}`}
                onClick={() => closeConversation(id)}
              >
                <Icon icon={X} size={16} />
              </button>
            </div>
          );
        })}
      </div>

      <div className="chat-tabs-new">
        <button type="button" className="chat-tab-add" aria-label="New conversation" onClick={newConversation}>
          <Icon icon={Plus} size={16} />
        </button>
        <button
          type="button"
          className="chat-tab-more"
          aria-label="Recent conversations"
          aria-expanded={menuOpen}
          disabled={recent.length === 0}
          onClick={() => setMenuOpen((v) => !v)}
        >
          Recent
        </button>
        {menuOpen && recent.length > 0 && (
          <ul
            className="chat-tabs-menu halo-list"
            // Closing on blur-out beats a document listener: focus leaving the
            // menu is exactly the dismissal condition, and it works for
            // keyboard and mouse alike.
            onBlur={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setMenuOpen(false);
            }}
          >
            {recent.map((c) => (
              <li key={c.id} className="chat-tabs-menu-row">
                <button
                  type="button"
                  className="chat-tabs-menu-open"
                  title={c.title}
                  onClick={() => {
                    setActiveConversation(c.id);
                    setMenuOpen(false);
                  }}
                >
                  {c.title}
                </button>
                <button
                  type="button"
                  className="chat-tabs-menu-del"
                  aria-label={`Remove ${c.title} from recent conversations`}
                  title="Remove from recent conversations on this device"
                  onClick={() => {
                    const confirmed = window.confirm(
                      `Remove "${c.title}" from recent conversations on this device? This local removal can't be undone here.`,
                    );
                    if (confirmed) deleteConversation(c.id);
                  }}
                >
                  <Icon icon={X} size={16} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RenameInput({ initial, onDone }: { initial: string; onDone: (title: string | null) => void }) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => ref.current?.select(), []);
  return (
    <input
      ref={ref}
      className="chat-tab-rename"
      aria-label="Conversation title"
      value={value}
      onChange={(e) => setValue(e.currentTarget.value)}
      onBlur={() => onDone(value.trim() || null)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onDone(value.trim() || null);
        if (e.key === "Escape") onDone(null);
      }}
    />
  );
}
