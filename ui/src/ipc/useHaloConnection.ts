// Phase 0 Step 7 — WS client hook: transport only, no business logic.
// Re-reads session.json on every (re)connect (Brain can respawn on a new
// port), sends `hello` first with no ack expected, queues outbound messages
// while disconnected, and reconnects on close. Spec: phase-0-plan.md Step 7.

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { parseIpcMessage, type IpcMessage, type UserMsg } from "./contract";

interface Session {
  port: number;
  token: string;
}

export type ConnState = "connecting" | "connected" | "reconnecting";

export function useHaloConnection(onMessage: (msg: IpcMessage) => void) {
  const [connState, setConnState] = useState<ConnState>("connecting");
  const [sidecarError, setSidecarError] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
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
          wsRef.current = ws;

          ws.onopen = () => {
            if (torndown) return;
            const hello = {
              type: "hello",
              id: crypto.randomUUID(),
              ts: new Date().toISOString(),
              token: session.token,
            };
            parseIpcMessage(hello);
            ws.send(JSON.stringify(hello));
            // ponytail: no auth ack exists (server.py _auth is silent on
            // success) — proceed optimistically and flush the queue.
            setConnState("connected");
            const queued = queueRef.current;
            queueRef.current = [];
            for (const msg of queued) ws.send(JSON.stringify(msg));
          };

          ws.onmessage = (ev) => {
            try {
              onMessageRef.current(parseIpcMessage(JSON.parse(ev.data)));
            } catch (e) {
              console.error("halo: dropping bad inbound frame", e);
            }
          };

          ws.onclose = () => {
            if (wsRef.current === ws) wsRef.current = null;
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
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    } else {
      queueRef.current.push(msg); // flushed on next successful hello
    }
  }, []);

  return { connState, sidecarError, sendUserMsg, conversationId: conversationIdRef.current };
}
