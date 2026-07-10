# System Design: Chat

The primary text interface and the entry point every other feature reuses.

## Responsibility
- Accept typed input, stream responses, hold conversation state.
- Be the same control loop voice uses — voice is "chat with an audio front-end," not a separate brain.

## Components
- **UI (Tauri/React):** message list, streaming renderer (markdown), input box, per-message "what Halo did" affordance linking into the activity feed.
- **Brain:** one LangGraph invocation per user turn. Streams tokens + tool/narration events back over WebSocket.

## Data flow
```
user types → UI → WS → Brain graph run
   → model router picks tier → plan → (tools via permission gate) → checkpoint
   → stream tokens + activity events → UI renders
```

## Interfaces
- `UI → Brain`: `{type:"user_msg", text, conversation_id}`
- `Brain → UI`: streamed `{type:"token"|"activity"|"approval_request"|"done"}`

## State
- Conversation history: session tier (RAM) during a turn; summarized into curated memory when it contains something durable (see [memory](03-memory.md)).
- Long chats are **summarized, not truncated blindly**, so context survives without unbounded token cost.
- Once a span is distilled (into the summary or a belief), it is **dropped from live history** — the same fact never rides into a prompt twice via history *and* memory injection.

## Failure handling
- Model/router error → surface a plain message + retry option; never silently drop the turn.
- Brain disconnect mid-stream → UI marks the message incomplete and offers resume (graph checkpoint still holds state).

## Cost note
- Most turns route to the **light** model; escalate to heavy only when the router flags reasoning/coding/planning depth. See [techstack/01-chat](../techstack/01-chat.md).
