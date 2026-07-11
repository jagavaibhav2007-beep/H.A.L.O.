# System Design: IPC Contract & Process Lifecycle

The canonical WebSocket message schema between the three processes, plus who launches what. **This doc is the Phase-1 build target** — the UI shell and mocked Brain are built against exactly these shapes.

## Process lifecycle
- **Tauri (UI process) is the parent.** On app start it spawns Brain and Voice as **sidecar processes** (packaged Python — PyInstaller or equivalent; a build-time concern, noted in [techstack/00](../techstack/00-stack-summary.md)).
- **Port:** Brain binds a random free loopback port and writes `{port, token}` to a user-only file (`%LOCALAPPDATA%\Halo\session.json`). UI and Voice read it to connect. No hard-coded ports.
- **Auth:** every WS connection's first frame is `{type:"hello", token}` — the per-session random token from that file. Wrong/missing token → connection dropped; success → Brain sends `hello_ack`. Clients must not send or flush application messages until that acknowledgement arrives. This closes the "any local process can drive the Brain or approve its own Tier-3 gates" hole; the permission gate is only a real choke point if the transport is authenticated.
- **Supervision:** Tauri watches sidecar exit; restarts with backoff (1s/5s/30s, then surface error state in UI). Brain death → UI "reconnecting", inputs queued locally; Voice buffers the last utterance.

## Message envelope
All messages: `{type, id, ts, ...payload}`. `id` is sender-generated (uuid) and is the **message** id; replies reference `reply_to`. Payload fields never reuse the key `id` (a payload `id` would clobber the envelope `id` once flattened) — domain identities get their own name, e.g. `approval_request.approval_id`.

## Inbound to Brain (from UI or Voice)
| type | payload | notes |
|---|---|---|
| `hello` | `token, role?:"ui"\|"voice"` | first frame, both clients; `role` (default `"ui"`) selects the outbound routing subset — see Routing below |
| `user_msg` | `text, conversation_id, source:"ui"\|"voice"` | **one shape for both clients** — voice includes conversation_id too |
| `interrupt` | `conversation_id` | typed **or** spoken "stop" — both clients can send it |
| `approval_response` | `reply_to (approval_request approval_id), decision:"approve"\|"deny"\|"edit", edited_args?` | closes the Tier-3 round-trip |
| `memory_edit` | `belief_id, op:"edit"\|"delete"\|"restore", text?` | memory panel |
| `skill_op` | `skill_name, op:"trial"\|"disable"\|"restore"\|"delete"` | skills panel |
| `lane_pin` | `task_id, lane:1\|2\|3` | user pins a lane |
| `task_op` | `task_id?, op:"pause"\|"resume"\|"stop"` | per-task controls (tasks view, orb menu); omitted `task_id` = all tasks. `stop` ≠ `interrupt`: stop kills a task, interrupt redirects a conversation |
| `mic` | `op:"mute"\|"unmute"` | UI → Brain → Voice (Brain is the hub; UI and Voice never talk directly) |
| `settings_update` | `key, value` | narration on/off, wake word on/off, model IDs, thresholds |
| `undo` | `undo_token` | the feed's Undo button, replying to an `activity`'s `undo_token` |

## Outbound from Brain (to UI; Voice receives the subset it speaks)
| type | payload | notes |
|---|---|---|
| `hello_ack` | none | confirms the connection is authenticated; clients may now flush queued application messages |
| `token` | `text, conversation_id` | streamed reply tokens |
| `activity` | `text, narrate:bool, task_id, undoable:bool, undo_token?, tier?:1\|2\|3, lane?:1\|2\|3` | feed events; `narrate:true` → Voice speaks it; **`undoable:false` shown explicitly** (sent email ≠ reversible); `tier`/`lane` drive the feed's chips and filters |
| `approval_request` | `approval_id, tool, args_redacted, tier, task_id, summary?, destructive?:bool` | suspends via `interrupt()`; resumed by `approval_response` whose `reply_to` = this `approval_id`; `summary` is the one plain sentence the card leads with; `destructive` drives the red-border / hold-to-approve / no-voice-approval variant |
| `done` | `conversation_id, task_id?` | turn/task complete |
| `error` | `code, message, recoverable:bool, conversation_id?` | never silently drop a turn |
| `task_state` | `task_id, state:"running"\|"paused"\|"waiting_approval"\|"done"\|"failed", lane, title?, step?, steps_total?, step_label?, reason?` | tasks panel + lane indicator; `step`/`steps_total`/`step_label` drive progress text, `reason` explains a paused task |
| `stream_frame` | `task_id, jpeg_b64, seq` | live desktop view, Lanes 2/3 only, throttled (~2 fps) |
| `voice_state` | `state:"idle"\|"wake"\|"listening"\|"thinking"\|"speaking"\|"muted"` | originates in the Voice worker, relayed by Brain → UI; drives the orb's state language |
| `transcript` | `text, final:bool, conversation_id` | STT partials for live ghost-text; `final:true` coincides with the `user_msg` the Voice worker submits |
| `spend_update` | `session_usd, month_usd` | Brain accumulates per-call cost (OpenRouter usage fields) into SQLite; feeds the Settings spend view |
| `belief_state` | `belief_id, text, kind:"preference"\|"project"\|"workflow"\|"decision"\|"lesson", provenance:"user"\|"inferred", salience, status:"active"\|"archived"\|"superseded", superseded_by?, used_at?` | memory panel cards; pushed as snapshot-on-connect + delta-on-change (same pattern as `task_state`) |
| `skill_state` | `skill_name, origin:"auto"\|"user", kind:"skill"\|"playbook", uses, success_rate, status:"active"\|"paused"\|"retired", born_at, reason?` | skills panel cards; same snapshot+delta pattern |

## Concurrency model
- **One LangGraph thread per `conversation_id`; turns are serialized per thread** (a queue). Voice and chat share the conversation they address — a voice utterance and a typed message to the same conversation queue in arrival order; different conversations run concurrently.
- `interrupt` targets its conversation's running turn only.
- **Interrupt vs pending approval:** if the conversation is in `waiting_approval` when `interrupt` arrives, the pending `approval_request` is **cancelled (implicit deny)** first, then the turn suspends. A stale approval card can never resume a task the user already stopped; the UI removes the card on the resulting `task_state: paused`.
- **Routing:** every client receives transport-level `hello_ack`; after that, Voice is sent only `token`, `activity(narrate:true)`, and `approval_request`, while the UI gets everything. Clients never filter a firehose — the Brain routes by the connection's `role` (from its `hello.role`, default `"ui"`), so a Voice connection never even receives the snapshot or non-narrated frames. Enforced in `server.py`'s `_frame_visible_to`.

## Cancellation ("stop" semantics)
- Between graph nodes: LangGraph `interrupt()` — clean suspend at last checkpoint.
- **Mid-tool:** long-running tools (coding-agent subprocess, browser action) must be **cooperatively cancellable**: subprocesses get terminate-then-kill; browser actions check a cancel flag between steps. "Halts immediately" (PRD §11) means ≤ ~2s, not instant mid-syscall.
- **Resume-after-side-effect:** a checkpoint records tool *intent* before execution and *result* after. On resume after a mid-tool stop/crash, the graph re-enters at the intent record and **reconciles first** (did the file move? did the form submit?) instead of blindly re-running. Reconciliation = a read-only check per tool type.

## Cross-refs
- Tiers & gate: [04-permissions](04-permissions.md) · UI panels consuming these events: [10-ui](10-ui.md) · Voice subset: [02-voice](02-voice.md)
