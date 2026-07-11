// Phase 0 Step 7 — WS client hook: transport only, no business logic.
// Re-reads session.json on every (re)connect (Brain can respawn on a new
// port), waits for `hello_ack`, queues outbound messages until authenticated,
// and reconnects on close. Spec: phase-0-plan.md Step 7.

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { parseIpcMessage, type IpcMessage, type UserMsg } from "./contract";
import { flushQueuedMessages, sendOrQueue } from "./queue";

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
  const queueRef = useRef<UserMsg[]>([]);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const conversationIdRef = useRef(crypto.randomUUID()); // ponytail: one conversation_id per session; multi-conversation UI is a later phase.

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

  const sendUserMsg = useCallback((text: string) => {
    const msg: UserMsg = {
      type: "user_msg",
      id: crypto.randomUUID(),
      ts: new Date().toISOString(),
      text,
      conversation_id: conversationIdRef.current,
      source: "ui",
    };
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

  return { connState, sidecarError, sendUserMsg, conversationId: conversationIdRef.current };
}
