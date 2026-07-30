// Phase 1 Step 8 — Chat view (ui_ux/03-chat.md): the default panel, "a
// conversation, not a terminal". Renders the store's ordered turns (user +
// assistant, D7 arrival order), streams tokens live, and owns the input box.
// The user's own turns are recorded via the store's appendUserTurn at send
// time (user_msg is never echoed back) — see reducer.ts UserTurn.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Mic, MicOff, Wrench, ChevronDown, ChevronRight } from "lucide-react";
import { Icon } from "../components/Icon";
import { Markdown } from "./Markdown";
import type { ConnState } from "../ipc/useHaloConnection";
import { ChatTabs } from "./ChatTabs";
import {
  useHaloStore,
  selectConversation,
  selectActivities,
  selectVoice,
  selectChats,
  selectOperationErrors,
  selectCapabilities,
} from "../state/store";
import type { AssistantTurn, Turn } from "../state/reducer";
import type { ActivityMsg } from "../ipc/contract";
import "./ChatView.css";

const REAL_EXAMPLE_CHIPS = [
  "Summarize a file",
  "Search my files",
  "What do you remember about me?",
];
const DEMO_EXAMPLE_CHIPS = ["demo everything", "demo task", "demo voice"];

interface ChatViewProps {
  conversationId: string;
  connState: ConnState;
  sendUserMsg: (conversationId: string, text: string) => void;
  sendConversationHistoryQuery?: (conversationId: string) => void;
  sendInterrupt: (conversationId: string) => string;
  sendMic: (op: "mute" | "unmute") => void;
  /** Kept on the real textarea so the workspace's Ctrl+K focus still lands. */
  inputId: string;
}

export function ChatView({
  conversationId,
  connState,
  sendUserMsg,
  sendConversationHistoryQuery,
  sendInterrupt,
  sendMic,
  inputId,
}: ChatViewProps) {
  const conv = useHaloStore(selectConversation(conversationId));
  const activities = useHaloStore(selectActivities);
  const voice = useHaloStore(selectVoice);
  const operationErrors = useHaloStore(selectOperationErrors);
  const capabilities = useHaloStore(selectCapabilities);
  // Against the real Brain there is no voice capture yet, so a mic toggle comes
  // back as an operation_unsupported error (correlated by envelope id, so keyed
  // "mic:<uuid>"). Nothing else renders that error, so surface it here: once
  // one arrives, the mic control is honestly disabled instead of dead. In mock
  // mode voice_state confirms the toggle and no such error is ever produced.
  const micRejected = useMemo(
    () => Object.keys(operationErrors).some((k) => k.startsWith("mic:")),
    [operationErrors],
  );
  const micUnavailable = capabilities.voiceInput !== true || micRejected;
  const appendUserTurn = useHaloStore((s) => s.appendUserTurn);
  const appendLocalUserTurn = useHaloStore((s) => s.appendLocalUserTurn);
  const acknowledgeInputRestore = useHaloStore((s) => s.acknowledgeInputRestore);
  const titleFromMessage = useHaloStore((s) => s.titleFromMessage);
  const chats = useHaloStore(selectChats);

  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const lastSentRef = useRef<Record<string, string>>({});
  const historyRequestedRef = useRef(new Set<string>());
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true); // autoscroll only while the user sits at the bottom (rule 6)

  const turns = conv?.turns ?? [];
  const activeAssistantId = turns.find(
    (turn): turn is AssistantTurn => turn.role === "assistant" && turn.status === "streaming",
  )?.id;
  const input = drafts[conversationId] ?? "";
  const exampleChips = capabilities.demoScenarios ? DEMO_EXAMPLE_CHIPS : REAL_EXAMPLE_CHIPS;
  // Live voice transcript for THIS conversation, still being spoken -> ghost text.
  const ghost =
    voice.transcript && !voice.transcript.final && voice.transcript.conversationId === conversationId
      ? voice.transcript.text
      : null;

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || connState === "incompatible" || connState === "unavailable") return;
      lastSentRef.current[conversationId] = trimmed;
      if (activeAssistantId && trimmed.toLowerCase() === "stop") {
        appendLocalUserTurn(conversationId, trimmed, crypto.randomUUID());
        sendInterrupt(conversationId);
        setDrafts((current) => ({ ...current, [conversationId]: "" }));
        return;
      }
      appendUserTurn(conversationId, trimmed, crypto.randomUUID());
      titleFromMessage(conversationId, trimmed); // no-ops unless still untitled
      sendUserMsg(conversationId, trimmed);
      setDrafts((current) => ({ ...current, [conversationId]: "" }));
    },
    [
      activeAssistantId,
      appendLocalUserTurn,
      appendUserTurn,
      titleFromMessage,
      conversationId,
      connState,
      sendInterrupt,
      sendUserMsg,
    ],
  );

  // rule 8: on error/disconnect the user's text is restored to the box so a
  // turn is never lost. The store flags it; we refill from the last send and
  // acknowledge so it fires once.
  useEffect(() => {
    if (!conv?.needsInputRestore) return;
    setDrafts((current) =>
      current[conversationId]
        ? current
        : { ...current, [conversationId]: lastSentRef.current[conversationId] ?? "" },
    );
    acknowledgeInputRestore(conversationId);
  }, [conv?.needsInputRestore, conversationId, acknowledgeInputRestore]);

  // Track whether the user is pinned to the bottom before each render commit,
  // then keep them pinned after new content lands (rule 6 — never yank scroll).
  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }
  useLayoutEffect(() => {
    if (pinnedRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [turns, ghost]);

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // rule 7 — IME guard: Enter mid-composition (CJK) commits the word, never sends.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(input);
    }
  }

  const empty = turns.length === 0 && !ghost;
  // A thread restored from a previous session: the Brain still has its history
  // (LangGraph checkpoints are keyed by conversation_id, so the next message
  // continues with full context) but the UI store starts empty and nothing
  // replays past turns on connect. Say so plainly rather than render a blank
  // panel that reads as data loss.
  const restored = Boolean(empty && chats.all.find((c) => c.id === conversationId)?.restored);
  const historyLoaded = conv?.historyLoaded === true;
  useEffect(() => {
    if (connState !== "connected") {
      historyRequestedRef.current.clear();
      return;
    }
    if (
      !restored
      || historyLoaded
      || !sendConversationHistoryQuery
      || historyRequestedRef.current.has(conversationId)
    ) return;
    historyRequestedRef.current.add(conversationId);
    sendConversationHistoryQuery(conversationId);
  }, [restored, historyLoaded, connState, conversationId, sendConversationHistoryQuery]);
  const latestAssistant = [...turns].reverse().find((turn): turn is AssistantTurn => turn.role === "assistant");
  // First person, one sentence, plain (ui_ux/00-design-language.md "Voice of
  // the interface") — these are screen-reader-only (the sr-only live region
  // below), so the copy voice rule still applies even though sighted users
  // never see the text.
  const connectionStatus =
    connState === "incompatible"
      ? "I'm running a different protocol version — please restart the app to update."
      : connState === "unavailable"
        ? "Halo isn't running in this browser. Start it with the browser launcher to continue."
      : connState === "reconnecting"
        ? "I'm reconnecting. Your message will send once I'm back."
        : connState === "connecting"
          ? "I'm connecting. Your message will send once I'm here."
          : latestAssistant?.status === "streaming"
            ? "I'm thinking."
            : latestAssistant?.status === "done"
              ? "I'm done."
              : "";
  const latestError = latestAssistant?.status === "error" ? latestAssistant.error?.message ?? "I couldn't answer that." : "";

  return (
    <div className="chat-view">
      <div className="halo-sr-only" role="status" aria-live="polite" aria-atomic="true">
        {connectionStatus}
      </div>
      <div className="halo-sr-only" role="alert" aria-live="assertive" aria-atomic="true">
        {latestError}
      </div>
      <ChatTabs />
      <div className="halo-scroll chat-scroll" ref={scrollRef} onScroll={onScroll}>
        {empty ? (
          <div className="chat-empty">
            {connState === "unavailable" ? (
              <p className="chat-empty-line">Start Halo with ./dev.ps1 -Browser to chat here.</p>
            ) : restored ? (
              <p className="chat-empty-line">
                {historyLoaded
                    ? "This conversation has no earlier messages."
                    : connState === "connected"
                      ? "Loading earlier messages…"
                      : "Earlier messages will load when Halo reconnects."}
              </p>
            ) : (
              <>
                <p className="chat-empty-line">Ask me anything, or just say "Halo".</p>
                <div className="chat-empty-chips">
                  {exampleChips.map((c) => (
                    <button
                      key={c}
                      type="button"
                      className="chat-chip"
                      disabled={connState === "incompatible"}
                      onClick={() => send(c)}
                    >
                      {c}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="chat-turns">
            {turns.map((t, index) => {
              const retryText =
                t.role === "assistant" && t.status === "error" && t.error?.recoverable
                  ? [...turns.slice(0, index)].reverse().find((turn) => turn.role === "user")?.text
                  : undefined;
              return (
                <TurnRow
                  key={t.id}
                  turn={t}
                  activities={activities}
                  canInterrupt={connState === "connected" && t.role === "assistant" && t.id === activeAssistantId}
                  queued={connState !== "connected"}
                  onInterrupt={() => sendInterrupt(conversationId)}
                  onRetry={retryText ? () => send(retryText) : undefined}
                />
              );
            })}
            {ghost && <GhostBubble text={ghost} />}
          </div>
        )}
      </div>

      <div className="chat-input-bar">
        <label className="halo-sr-only" htmlFor={inputId}>Message Halo</label>
        <button
          type="button"
          className="chat-input-mic"
          data-muted={voice.state === "muted" || undefined}
          data-unavailable={micUnavailable || undefined}
          disabled={micUnavailable}
          title={micUnavailable ? "Voice input isn't available in this build." : undefined}
          aria-label={
            micUnavailable
              ? "Voice input unavailable"
              : voice.state === "muted"
                ? "Unmute mic"
                : "Mute mic"
          }
          aria-pressed={voice.state === "muted"}
          onClick={() => sendMic(voice.state === "muted" ? "unmute" : "mute")}
        >
          <Icon icon={voice.state === "muted" ? MicOff : Mic} size={20} />
        </button>
        <textarea
          id={inputId}
          className="chat-input"
          value={input}
          onChange={(event) => {
            const value = event.currentTarget.value;
            setDrafts((current) => ({ ...current, [conversationId]: value }));
          }}
          onKeyDown={onKeyDown}
          disabled={connState === "incompatible" || connState === "unavailable"}
          placeholder={
            connState === "incompatible"
              ? "Restart Halo to continue…"
              : connState === "unavailable"
                ? "Start Halo in browser mode to continue…"
                : "Message Halo…"
          }
          rows={1}
        />
        {connState === "incompatible" ? (
          <span className="chat-queued-badge">restart to update</span>
        ) : connState === "unavailable" ? (
          <span className="chat-queued-badge">browser connection unavailable</span>
        ) : connState !== "connected" ? (
          <span className="chat-queued-badge">will send when reconnected</span>
        ) : null}
      </div>
    </div>
  );
}

function TurnRow({
  turn,
  activities,
  canInterrupt,
  queued,
  onInterrupt,
  onRetry,
}: {
  turn: Turn;
  activities: ActivityMsg[];
  canInterrupt: boolean;
  queued: boolean;
  onInterrupt: () => string;
  onRetry?: () => void;
}) {
  if (turn.role === "user") {
    return (
      <div className="chat-turn chat-turn-user">
        <div className="chat-bubble chat-bubble-user">
          {turn.viaVoice && <Icon icon={Mic} size={16} className="chat-turn-mic" />}
          {turn.text}
        </div>
      </div>
    );
  }
  return (
    <AssistantRow
      turn={turn}
      activities={activities}
      canInterrupt={canInterrupt}
      queued={queued}
      onInterrupt={onInterrupt}
      onRetry={onRetry}
    />
  );
}

function AssistantRow({
  turn,
  activities,
  canInterrupt,
  queued,
  onInterrupt,
  onRetry,
}: {
  turn: AssistantTurn;
  activities: ActivityMsg[];
  canInterrupt: boolean;
  queued: boolean;
  onInterrupt: () => string;
  onRetry?: () => void;
}) {
  const [stopping, setStopping] = useState(false);
  const [interruptId, setInterruptId] = useState<string | null>(null);
  const operationErrors = useHaloStore(selectOperationErrors);
  const interruptError = interruptId ? operationErrors[`interrupt:${interruptId}`] : undefined;
  useEffect(() => {
    if (interruptError) setStopping(false);
  }, [interruptError]);
  const thinking = turn.status === "streaming" && turn.text.length === 0;
  return (
    <div className="chat-turn chat-turn-halo">
      <div className="chat-bubble chat-bubble-halo">
        {thinking && queued ? (
          <span>Queued — waiting for Halo to reconnect.</span>
        ) : thinking ? (
          <span className="chat-thinking" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        ) : (
          <Markdown text={turn.text} />
        )}
        {turn.status === "error" && turn.error && (
          <div className="chat-error">
            <span>{turn.error.message} — the turn is saved; your message is back in the box.</span>
            {onRetry && (
              <button type="button" className="chat-retry" onClick={onRetry}>
                Retry response
              </button>
            )}
          </div>
        )}
        {turn.status === "interrupted" && (
          <div className="chat-interrupted">{turn.note ?? "stopped · what should I do differently?"}</div>
        )}
        {canInterrupt && (
          <button
            type="button"
            className="chat-stop"
            aria-label="Stop response"
            disabled={stopping}
            onClick={() => {
              setStopping(true);
              setInterruptId(onInterrupt());
            }}
          >
            {stopping ? "Stopping…" : "Stop"}
          </button>
        )}
        {interruptError && (
          <div className="chat-interrupted" role="alert">
            {interruptError.message}
          </div>
        )}
      </div>
      {turn.taskId && <WhatIDidRow taskId={turn.taskId} activities={activities} />}
    </div>
  );
}

// "Work is visible inline": activities sharing this turn's task_id collapse
// into a slim tool row under the message, expandable without leaving chat.
function WhatIDidRow({ taskId, activities }: { taskId: string; activities: ActivityMsg[] }) {
  const [open, setOpen] = useState(false);
  const mine = useMemo(() => activities.filter((a) => a.task_id === taskId), [activities, taskId]);
  if (mine.length === 0) return null;
  return (
    <div className="chat-did">
      <button type="button" className="chat-did-toggle" onClick={() => setOpen((o) => !o)}>
        <Icon icon={open ? ChevronDown : ChevronRight} size={16} />
        <Icon icon={Wrench} size={16} />
        <span>
          {mine.length} {mine.length === 1 ? "action" : "actions"}
        </span>
      </button>
      {open && (
        <ul className="chat-did-list">
          {mine.map((a) => (
            <li key={a.id}>{a.text}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function GhostBubble({ text }: { text: string }) {
  return (
    <div className="chat-turn chat-turn-user">
      <div className="chat-bubble chat-bubble-user chat-bubble-ghost">
        <Icon icon={Mic} size={16} className="chat-ghost-mic" />
        {text}
      </div>
    </div>
  );
}
