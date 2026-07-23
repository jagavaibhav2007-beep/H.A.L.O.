# Tech Stack: Voice

Design: [systemdesign/02-voice](../systemdesign/02-voice.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| Orchestration | **Pipecat**, `LocalAudioTransport` | mic/speaker direct; no media server, no Docker |
| Wake word | **openWakeWord** ("Halo" — **custom-trained model**, not stock; stock "Hey Jarvis" as dev interim) | on-device, free |
| VAD | Silero VAD (bundled with Pipecat) | speech boundaries |
| Audio I/O | `sounddevice`/PyAudio (via Pipecat) | local devices |
| STT | **faster-whisper** (local, CTranslate2 int8, SYSTRAN/MIT) | on-device, CPU workable; cloud fallback: OpenRouter Whisper |
| LLM | same Brain (OpenRouter, tiered) | reuses chat graph |
| TTS | **Kokoro** (local, 82M params, Apache-2.0) | on-device real-time CPU; cloud fallback: Deepgram Aura-1 |

## Cost note
- **Free/local:** wake word, VAD, audio capture/playback, STT (faster-whisper), TTS (Kokoro).
- **Cloud (fallback only):** OpenRouter Whisper STT (if local unavailable), Deepgram Aura TTS (if local unavailable), LLM (tiered — light by default).
- Cascaded pipeline (vs a single realtime model) = **cheaper + model choice**; on-device first keeps audio private until the wake word.

## Later upgrade path
- If sub-second latency becomes the goal, swap the cascade for a native speech-to-speech realtime model (higher cost). Not in initial scope.
