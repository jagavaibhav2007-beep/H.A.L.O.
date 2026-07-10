import { useCallback, useRef, useState } from "react";
import "./App.css";
import { useHaloConnection } from "./ipc/useHaloConnection";
import type { IpcMessage } from "./ipc/contract";

interface ChatLine {
  id: string;
  who: "you" | "brain" | "system";
  text: string;
}

function App() {
  const [lines, setLines] = useState<ChatLine[]>([]);
  const [input, setInput] = useState("");
  const streamingRef = useRef<Map<string, string>>(new Map()); // conversation_id -> in-progress text

  const onMessage = useCallback((msg: IpcMessage) => {
    if (msg.type === "token") {
      const prev = streamingRef.current.get(msg.conversation_id) ?? "";
      streamingRef.current.set(msg.conversation_id, prev + msg.text);
    } else if (msg.type === "done") {
      const text = streamingRef.current.get(msg.conversation_id) ?? "";
      streamingRef.current.delete(msg.conversation_id);
      setLines((ls) => [...ls, { id: msg.id, who: "brain", text }]);
    } else if (msg.type === "error") {
      setLines((ls) => [...ls, { id: msg.id, who: "system", text: `error: ${msg.message}` }]);
    }
    // Other inbound types are out of scope for Phase 0 Step 7.
  }, []);

  const { connState, sidecarError, sendUserMsg } = useHaloConnection(onMessage);

  function submit() {
    const text = input.trim();
    if (!text) return;
    setLines((ls) => [...ls, { id: crypto.randomUUID(), who: "you", text }]);
    sendUserMsg(text);
    setInput("");
  }

  return (
    <main className="container">
      <div className="conn-indicator" data-state={connState}>
        {connState === "connected" && "connected"}
        {connState === "connecting" && "connecting…"}
        {connState === "reconnecting" && "reconnecting…"}
      </div>
      {sidecarError && <div className="conn-indicator" data-state="error">Brain failed to start</div>}

      <div className="chat-log">
        {lines.map((l) => (
          <div key={l.id} className={`chat-line chat-${l.who}`}>
            {l.text}
          </div>
        ))}
      </div>

      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input
          id="chat-input"
          value={input}
          onChange={(e) => setInput(e.currentTarget.value)}
          placeholder="Type a message…"
        />
        <button type="submit">Send</button>
      </form>
    </main>
  );
}

export default App;
