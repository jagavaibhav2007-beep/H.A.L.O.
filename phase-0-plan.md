# Phase 0 — Skeleton & Contract: Implementation Plan

The walking-skeleton plumbing from [phases.md](phases.md#phase-0--skeleton--contract-the-plumbing). Goal: **three processes exist and talk, authenticated, over the real message envelope — nothing intelligent yet.** Built strictly against [systemdesign/11-ipc-contract.md](systemdesign/11-ipc-contract.md).

**Phase exit criteria (the whole phase is done when):**
1. UI sends a `user_msg` over an authenticated WS; a stub Brain streams back `token`(s) + `done`.
2. Killing the Brain process → UI shows "reconnecting", queues input; Tauri restarts Brain (1s/5s/30s backoff) and it reconnects.
3. A wrong/missing `hello` token is dropped by the Brain; valid authentication returns `hello_ack` before clients flush application messages.
4. Voice sidecar connects and idles (no audio yet).

**Stack (from [techstack/](techstack/00-stack-summary.md)):** Tauri (Rust) + React/TypeScript UI · Python Brain + Voice sidecars · local-loopback WebSocket. Packaging sidecars (PyInstaller) is deferred — Phase 0 runs them from source in dev.

**Out of scope for all steps:** any real LLM/model call, memory, permission gate, voice audio/STT/TTS, UI panels beyond a minimal chat box, sidecar binary packaging. Those are Phase 1+.

---

## Step 1 — Repo scaffold & monorepo layout

**Intent:** Establish the three-process project layout so every later step has a home. Decide and create the folder structure: `ui/` (Tauri + React/TS), `brain/` (Python package), `voice/` (Python package), plus root dev scripts to launch all three together. No behavior yet — just a runnable empty shell for each process.

**Deliverables:** `ui/` Tauri+Vite+React scaffold that opens a window; `brain/` and `voice/` Python packages with `pyproject.toml` and an empty `main`; a root README/dev-script documenting `run all three`. `.gitignore` already covers the build artifacts.

**Acceptance:** `ui` dev window opens; `python -m brain` and `python -m voice` each start and exit cleanly; layout matches the process model in [11-ipc-contract](systemdesign/11-ipc-contract.md).

---

## Step 2 — Shared IPC contract types (TypeScript + Python)

**Intent:** Encode the message envelope (`{type,id,ts,...}`), the inbound-to-Brain table, and the outbound-from-Brain table from [11-ipc-contract](systemdesign/11-ipc-contract.md) as typed definitions the UI and Brain both import. One authoritative source, mirrored to both languages (TS types + Python dataclasses/TypedDicts), so message shapes can't drift between processes.

**Deliverables:** TS types for every `type` in the inbound/outbound tables; matching Python types; a tiny validator on each side that rejects unknown/malformed frames. Only the Phase-0 subset needs runtime use (`hello`, `hello_ack`, `user_msg`, `token`, `done`, `error`), but the full envelope is typed.

**Acceptance:** a `user_msg` built in TS deserializes to the Python type and back with identical fields; unknown `type` is rejected by both validators; the two definitions are provably in sync (shared schema or a check that fails on drift).

---

## Step 3 — Brain: WS server, session handshake & auth

**Intent:** Stand up one Brain WebSocket server on a random free loopback port, write `{port, token}` to `%LOCALAPPDATA%\Halo\session.json` (user-only perms), and enforce the auth handshake: every connection's first frame must be `{type:"hello", token}` matching the session token — wrong or missing token drops the connection, while success returns `hello_ack`. This is the security choke point that makes the later permission gate meaningful.

**Deliverables:** loopback-only WS server, no hard-coded port; crash-safe single-instance lock; `session.json` written atomically with user-only file permissions; `hello`/`hello_ack` token gate; clean logging of dropped connections.

**Acceptance:** server binds a random port each run; a client with the correct token receives `hello_ack`; application frames remain queued until that acknowledgement; a client with a wrong/absent token is dropped before any other frame is processed; a second Brain cannot compete for `session.json`; `session.json` is not world-readable.
**Out of scope:** token rotation, TLS (loopback only), multi-user.

---

## Step 4 — Brain: stub echo turn (`user_msg` → `token` + `done`)

**Intent:** Give the Brain just enough behavior to prove the round-trip: on an inbound `user_msg`, stream one or more `token` frames (echoing/acknowledging the text) then a `done`, keyed to the same `conversation_id`. Serialize turns per `conversation_id` (a queue) as the contract's concurrency model requires — no model call, no graph, just the shape.

**Deliverables:** `user_msg` handler that emits `token`+`done`; per-`conversation_id` serialization; an `error` frame path so a turn is never silently dropped.

**Acceptance:** sending `user_msg` yields streamed `token`(s) then `done` with the matching `conversation_id`; two messages to one conversation are handled in arrival order; a handler exception surfaces as an `error` frame, not a dropped turn.

---

## Step 5 — Voice: stub sidecar connection

**Intent:** Bring the Voice worker up as a real third process that reads `session.json`, connects to the Brain over WS, completes the `hello` handshake, and idles. No wake word, capture, STT, or TTS yet — this only proves the three-process topology and that Voice authenticates through the same choke point as the UI.

**Deliverables:** Voice process that reads `session.json`, connects, sends `hello`, stays connected, and logs a heartbeat; exits cleanly on Brain disconnect (so Tauri supervision can restart it).

**Acceptance:** Voice connects and passes the token handshake; with a wrong token it is dropped like any client; on Brain death it exits/reconnects cleanly.

---

## Step 6 — Tauri: sidecar spawn & supervision with backoff

**Intent:** Make the UI process the parent that owns lifecycle. On app start, Tauri spawns Brain and Voice as sidecar subprocesses, watches for their exit, and restarts them with 1s → 5s → 30s backoff; after exhausting backoff it surfaces a persistent error state to the UI. On Brain death the UI shows "reconnecting" and queues input locally.

**Deliverables:** Tauri (Rust) sidecar spawn for `brain` and `voice`; exit-watch + backoff restart ladder; an app-state signal (`reconnecting` / `error`) pushed to the React layer; local input queue that flushes on reconnect.

**Acceptance:** starting the app launches all three processes; `kill` the Brain → UI shows "reconnecting", Brain is respawned within the backoff schedule and the UI recovers; repeated crashes escalate through 1s/5s/30s then show the error state.
**Out of scope:** packaged-binary sidecars (dev runs from source); crash telemetry.

---

## Step 7 — UI: WS client & minimal chat round-trip

**Intent:** Wire the React UI to the Brain: read `session.json` (via a Tauri command, since the browser layer can't read the filesystem directly), open the WS, complete the `hello` handshake, send a typed `user_msg` from a minimal input box, and render the streamed `token`+`done` reply. This is the visible proof the skeleton works end to end. UI holds no business logic — pure transport + render.

**Deliverables:** Tauri command exposing `session.json` to the frontend; React WS client with reconnect; a bare chat input + streamed-reply view driven only by contract frames; connection-state indicator wired to Step 6's signal.

**Acceptance:** typing a message shows the streamed echo reply; the connection indicator reflects reconnecting/connected; no business logic lives in the UI (it only sends/receives contract frames).

---

## Step 8 — End-to-end smoke test & supervision verification

**Intent:** Lock the phase exit criteria behind a repeatable check. Exercise the full skeleton: UI→Brain echo round-trip, the auth-drop path, and the kill-Brain→reconnect→recover supervision loop, so Phase 1 can build on a proven contract.

**Deliverables:** an end-to-end test/script that (a) sends a `user_msg` and asserts `token`+`done`, (b) asserts a bad-token connection is dropped, (c) kills the Brain mid-session and asserts the UI reconnects and a subsequent message succeeds.

**Acceptance:** all four phase exit criteria pass in one run; the check is runnable from the root dev script and documented for CI later.

---

## Build order & dependencies

```
Step 1 (scaffold)
   └─> Step 2 (contract types)
          ├─> Step 3 (Brain server+auth) ─> Step 4 (Brain echo)
          ├─> Step 5 (Voice stub)         ┘
          └─> Step 6 (Tauri supervision) ─> Step 7 (UI client) ─> Step 8 (E2E)
```

Steps 3–5 can proceed in parallel once the contract (Step 2) lands. Step 7 needs Steps 3–4 (something to talk to) and Step 6 (spawn + session.json availability). Step 8 is last.
