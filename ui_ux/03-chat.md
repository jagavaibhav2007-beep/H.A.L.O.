# Halo UI/UX: Chat

The default main view. Behavior source: [systemdesign/01-chat](../systemdesign/01-chat.md).

## Feel
- A conversation, not a terminal. Your messages right-aligned in soft baby-blue glass; Halo's left-aligned on plain surface. 16px body, generous spacing, streamed tokens render as they arrive.
- Markdown rendered fully (code blocks in JetBrains Mono with copy button, collapsible if >20 lines).

## What makes it Halo (not a chatbot)
- **Work is visible inline:** when a reply involved actions, a slim "what I did" row sits under the message — tool icons + one-line summary. Clicking expands the exact activity entries (from the [Activity feed](06-watching-halo-work.md)) without leaving chat.
- **Voice and text share this thread.** Spoken turns appear here with a small mic glyph; the live transcript materializes as ghost text while you speak, solidifying when STT finalizes ([04-voice](04-voice.md)).
- **Approval cards appear inline** at the point in the conversation where the task paused — the chat tells the story of the pause and the resume.

## States
- **Thinking:** three-dot pulse in Halo's bubble (≤300ms to appear). If a tool is running, the dots are replaced by the live narration line ("opening the project…").
- **Interrupted:** a quiet divider — "stopped · what should I do differently?" — with your next message resuming from the checkpoint.
- **Error:** in-bubble, cause + action ("Model unreachable — Retry"). Never a blank drop; the turn is never lost (input restored to the box).
- **Empty state (first run):** one line — "Ask me anything, or just say 'Halo'." plus 3 example chips drawn from actual capabilities.

## Input
- Single box, `Enter` sends, `Shift+Enter` newline. Mic button mirrors orb state. Slash-free: no commands to memorize — natural language is the only interface.
- Disconnected Brain: input stays usable, queued badge on send ("will send when reconnected"), per the IPC contract's reconnect rules.
