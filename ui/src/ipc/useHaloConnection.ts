// Phase 0 Step 7 — WS client hook: transport only, no business logic.
// Re-reads session.json on every (re)connect (Brain can respawn on a new
// port), waits for `hello_ack`, queues outbound messages until authenticated,
// and reconnects on close. Spec: phase-0-plan.md Step 7.

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  parseIpcMessage,
  type ApprovalResponseMsg,
  type InterruptMsg,
  type IpcMessage,
  type LanePinMsg,
  type MemoryEditMsg,
  type MicMsg,
  type SettingsUpdateMsg,
  type SkillOpMsg,
  type TaskOpMsg,
  type UndoMsg,
  type UserMsg,
} from "./contract";
import { flushQueuedMessages, sendOrQueue } from "./queue";

// Every outbound type the UI can send; one queue/dispatch path for all of them.
type Outbound =
  | UserMsg
  | TaskOpMsg
  | UndoMsg
  | ApprovalResponseMsg
  | InterruptMsg
  | LanePinMsg
  | MemoryEditMsg
  | SkillOpMsg
  | SettingsUpdateMsg
  | MicMsg;

interface Session {
  port: number;
  token: string;
}

export type ConnState = "connecting" | "connected" | "reconnecting";

export function useHaloConnection(onMessage: (msg: IpcMessage) => void) {
  const [connState, setConnState] = useState<ConnState>("connecting");
  const [sidecarError, setSidecarError] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const authenticatedRef = useRef(false);
  const queueRef = useRef<Outbound[]>([]);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  // No conversation identity lives here: which thread a message belongs to is
  // a UI decision (store.ts's conversation registry), and this hook is
  // transport-only. Callers pass the id in.

  useEffect(() => {
    let torndown = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    function connect() {
      if (torndown) return;
      setConnState((s) => (s === "connected" ? "reconnecting" : s));

      invoke<Session>("read_session")
        .then((session) => {
          if (torndown) return;
          const ws = new WebSocket(`ws://127.0.0.1:${session.port}`);
          authenticatedRef.current = false;
          wsRef.current = ws;

          ws.onopen = () => {
            if (torndown) return;
            const hello = {
              type: "hello",
              id: crypto.randomUUID(),
              ts: new Date().toISOString(),
              token: session.token,
              role: "ui" as const, // gets the full stream (11-ipc-contract.md routing)
            };
            try {
              parseIpcMessage(hello);
              ws.send(JSON.stringify(hello));
            } catch (error) {
              console.error("halo: send failed while connecting", error);
              ws.close();
            }
          };

          ws.onmessage = (ev) => {
            try {
              const message = parseIpcMessage(JSON.parse(ev.data));
              if (message.type === "hello_ack") {
                if (authenticatedRef.current) return;
                flushQueuedMessages(ws, queueRef.current);
                authenticatedRef.current = true;
                setConnState("connected");
                return;
              }
              if (!authenticatedRef.current) {
                throw new Error(`received ${message.type} before hello_ack`);
              }
              onMessageRef.current(message);
            } catch (e) {
              console.error("halo: dropping bad inbound frame", e);
              ws.close();
            }
          };

          ws.onclose = () => {
            if (wsRef.current === ws) {
              wsRef.current = null;
              authenticatedRef.current = false;
            }
            if (torndown) return;
            setConnState("reconnecting");
            // ponytail: fixed ~1s retry; the real backoff ladder (1s/5s/30s)
            // already lives in supervisor.rs for the process itself.
            retryTimer = setTimeout(connect, 1000);
          };
        })
        .catch((e) => {
          if (torndown) return;
          console.error("halo: read_session failed, retrying", e);
          retryTimer = setTimeout(connect, 1000);
        });
    }

    connect();

    const unlisten = listen<{ process: string; state: string }>("sidecar-state", (e) => {
      if (e.payload.process === "brain" && e.payload.state === "error") {
        setSidecarError(true);
      }
    });

    return () => {
      torndown = true;
      if (retryTimer) clearTimeout(retryTimer);
      unlisten.then((f) => f());
      const ws = wsRef.current;
      if (ws) {
        // Clear handlers first so the intentional teardown close doesn't
        // trigger the reconnect loop (StrictMode double-invokes this effect).
        ws.onclose = null;
        ws.onmessage = null;
        ws.onopen = null;
        ws.close(); // safe to call while CONNECTING too
        wsRef.current = null;
        authenticatedRef.current = false;
      }
    };
  }, []);

  // One send/queue path for every outbound type: validate, send if
  // authenticated, else queue for the reconnect flush (Phase-0 rules).
  const dispatch = useCallback((msg: Outbound) => {
    parseIpcMessage(msg);
    const ws = wsRef.current;
    const openSocket = ws?.readyState === WebSocket.OPEN ? ws : null;
    try {
      if (sendOrQueue(openSocket, authenticatedRef.current, msg, queueRef.current)) return;
    } catch (error) {
      console.error("halo: send failed, queueing for reconnect", error);
      ws?.close();
      queueRef.current.push(msg);
    }
  }, []);

  const env = () => ({ id: crypto.randomUUID(), ts: new Date().toISOString() });

  const sendUserMsg = useCallback(
    (conversation_id: string, text: string) =>
      dispatch({ type: "user_msg", ...env(), text, conversation_id, source: "ui" }),
    [dispatch],
  );
  const sendTaskOp = useCallback(
    (op: TaskOpMsg["op"], task_id?: string) => dispatch({ type: "task_op", ...env(), op, task_id }),
    [dispatch],
  );
  const sendUndo = useCallback(
    (undo_token: string) => dispatch({ type: "undo", ...env(), undo_token }),
    [dispatch],
  );
  // Approval card (Step 10). reply_to MUST be the approval's `approval_id`
  // (what the Brain keys pending approvals by), never the request's envelope
  // id — sending the envelope id leaves the approval unresolved forever.
  const sendApprovalResponse = useCallback(
    (reply_to: string, decision: ApprovalResponseMsg["decision"], edited_args?: unknown) =>
      dispatch({ type: "approval_response", ...env(), reply_to, decision, edited_args }),
    [dispatch],
  );
  // Stale-card rule (Step 10): an interrupt cancels the conversation's pending
  // approval (implicit deny) and pauses the task — the mock then broadcasts
  // task_state:paused, which removes the card from the store.
  const sendInterrupt = useCallback(
    (conversation_id: string) => dispatch({ type: "interrupt", ...env(), conversation_id }),
    [dispatch],
  );
  // Tasks view (Step 11): re-pin a task's lane. The mock confirms by
  // broadcasting a task_state with the new lane (rule 3 disable-until-confirm).
  const sendLanePin = useCallback(
    (task_id: string, lane: LanePinMsg["lane"]) => dispatch({ type: "lane_pin", ...env(), task_id, lane }),
    [dispatch],
  );
  // Memory panel (Step 12): edit/delete/restore a belief. The store never
  // mutates a belief locally (rule 3) — the mock replies with the confirming
  // belief_state(s) (superseding user-stated for edit, archived/active for
  // delete/restore).
  const sendMemoryEdit = useCallback(
    (belief_id: string, op: MemoryEditMsg["op"], text?: string) =>
      dispatch({ type: "memory_edit", ...env(), belief_id, op, text }),
    [dispatch],
  );
  // Skills panel (Step 13): trial/disable/restore/delete a skill. The mock
  // confirms with a fresh skill_state (rule 3); "trial" additionally streams
  // scripted activity into the trial-run drawer.
  const sendSkillOp = useCallback(
    (skill_name: string, op: SkillOpMsg["op"]) => dispatch({ type: "skill_op", ...env(), skill_name, op }),
    [dispatch],
  );
  // Settings view (Step 13): a toggle/value change. No confirming frame is
  // expected — settings apply locally (theme) or are display-only against the
  // mock (rule: "settings that need a real backend render disabled" — this
  // sender exists for the toggles that ARE wired, not a promise every key does).
  const sendSettingsUpdate = useCallback(
    (key: string, value: unknown) => dispatch({ type: "settings_update", ...env(), key, value }),
    [dispatch],
  );
  // Voice mute (Step 14): the mock replies voice_state:muted/idle — mute is
  // visually loud in orb + chat + status strip at once.
  const sendMic = useCallback(
    (op: MicMsg["op"]) => dispatch({ type: "mic", ...env(), op }),
    [dispatch],
  );

  return {
    connState,
    sidecarError,
    sendUserMsg,
    sendTaskOp,
    sendUndo,
    sendApprovalResponse,
    sendInterrupt,
    sendLanePin,
    sendMemoryEdit,
    sendSkillOp,
    sendSettingsUpdate,
    sendMic,
  };
}
