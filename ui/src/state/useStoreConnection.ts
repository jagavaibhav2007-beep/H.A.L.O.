// Shared store<->connection wiring for both window roots (orb + workspace).
// Each webview boots its OWN store instance and owns its OWN live connection
// (see store.ts) -- this hook is the glue that was byte-identical in both
// roots: open the connection, feed every inbound frame into the store, and
// forward the two Phase-0 connection signals into the store's connection
// slice, keeping WS state and sidecar health distinct per the "never conflate"
// rule. Returns the full useHaloConnection result so the workspace can
// destructure its send* fns + connState; the orb ignores the return.
import { useCallback, useEffect } from "react";
import { useHaloConnection } from "../ipc/useHaloConnection";
import type { IpcMessage } from "../ipc/contract";
import { useHaloStore } from "./store";

export function useStoreConnection() {
  const onMessage = useCallback((frame: IpcMessage) => {
    useHaloStore.getState().applyFrame(frame);
  }, []);
  const onAuthenticated = useCallback(() => {
    useHaloStore.getState().applyConnectionEvent({ type: "authenticated" });
  }, []);
  const conn = useHaloConnection(onMessage, onAuthenticated);
  const { connState, sidecars } = conn;

  useEffect(() => {
    if (connState === "connected") return;
    const event = connState === "unavailable"
          ? ({ type: "ws_unavailable" } as const)
          : connState === "reconnecting" || connState === "incompatible"
          ? ({ type: "ws_closed" } as const) // incompatible: terminal, treated as not-connected for the store slice
          : ({ type: "ws_open" } as const);
    useHaloStore.getState().applyConnectionEvent(event);
  }, [connState]);

  useEffect(() => {
    if (sidecars.brain !== "unknown") {
      useHaloStore.getState().applyConnectionEvent({
        type: "sidecar_state",
        process: "brain",
        state: sidecars.brain,
      });
    }
    if (sidecars.voice !== "unknown") {
      useHaloStore.getState().applyConnectionEvent({
        type: "sidecar_state",
        process: "voice",
        state: sidecars.voice,
      });
    }
  }, [sidecars]);

  return conn;
}
