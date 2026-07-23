# System Design: Voice

Real-time spoken conversation. A separate process (Pipecat) that feeds the same Brain as chat.

## Responsibility
- Detect the wake word "Halo" on-device, capture speech, transcribe, hand text to the Brain, speak the reply, handle barge-in.
- Narrate **main events only** during tasks, never every step.

## Pipeline (Pipecat, local audio transport — no media server, no Docker)
```
mic → openWakeWord ("Halo") → VAD (speech boundaries)
    → STT (faster-whisper, on-device) → text [cloud Whisper if unavailable]
    → WS to Brain (same graph as chat)
    → Brain streams reply text + narration events
    → TTS (Kokoro, on-device) → speaker [cloud Aura if unavailable]
```

## Interruption / "stop"
- **Barge-in:** user speaking while Halo talks → Pipecat cuts TTS immediately.
- **"stop" command:** Voice sends `{type:"interrupt"}` → Brain fires LangGraph `interrupt()` → graph suspends at last checkpoint → Halo asks "what should I do differently?" → user reply → **resume from that checkpoint** (not restart).

## Narration
- Brain emits `activity` events tagged `narrate:true` for main milestones ("opening the project", "coding agent finished, 3 files changed"). Voice speaks only those; the UI feed shows all.

## Interfaces
- `Voice → Brain`: `{type:"user_msg", text, source:"voice"}`, `{type:"interrupt"}`
- `Brain → Voice`: `{type:"token"}`, `{type:"activity", narrate:bool}`, `{type:"approval_request"}` (spoken as "I need your OK to …")

## Failure handling
- STT low confidence (faster-whisper) → Halo asks to repeat rather than guessing. If local STT unavailable → cloud fallback (OpenRouter Whisper).
- TTS local unavailable → fall back to cloud (Deepgram Aura) or on-screen text + soft chime as last resort; don't fail the task.
- Wake-word false positive → VAD finds no real utterance → silently drop.

## Wake-word reality check
- openWakeWord ships a fixed pretrained set — **"Halo" is not in it**. A custom "Halo" model must be trained (openWakeWord's synthetic-data training pipeline; a one-time build task, not a config value). Interim fallback during development: a stock word (e.g. "Hey Jarvis") until the custom model is trained.

## Privacy
- Wake word runs **fully on-device** (openWakeWord).
- **STT and TTS run on-device** (faster-whisper, Kokoro) — audio never leaves the machine during local inference.
- Cloud STT/TTS are fallback only if local models are unavailable; audio leaves the machine only as a fallback, not by default.
- See [techstack/02-voice](../techstack/02-voice.md).
