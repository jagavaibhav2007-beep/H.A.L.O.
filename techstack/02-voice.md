# Tech Stack: Voice

Design: [systemdesign/02-voice](../systemdesign/02-voice.md). Global stack: [00-stack-summary](00-stack-summary.md).

## Feature-specific
| Concern | Choice | Notes |
|---|---|---|
| Orchestration | **Pipecat**, `LocalAudioTransport` | mic/speaker direct; no media server, no Docker |
| Wake word | **openWakeWord** ("Halo" — **custom-trained model**, not stock; stock "Hey Jarvis" as dev interim) | on-device, free |
| VAD | Silero VAD (bundled with Pipecat) | speech boundaries |
| Audio I/O | `sounddevice`/PyAudio (via Pipecat) | local devices |
| STT | OpenRouter `openai/whisper-large-v3-turbo` (audio endpoint) | cloud |
| LLM | same Brain (OpenRouter, tiered) | reuses chat graph |
| TTS | **Deepgram Aura-1** (Deepgram SDK) | cloud |

## Cost note
- **Free/local:** wake word, VAD, audio capture/playback.
- **Cloud (per use):** Whisper STT (per audio minute), LLM (tiered — light by default), Deepgram Aura TTS (per character/minute).
- Cascaded pipeline (vs a single realtime model) = **cheaper + model choice**; slightly higher latency, acceptable now.

## Later upgrade path
- If sub-second latency becomes the goal, swap the cascade for a native speech-to-speech realtime model (higher cost). Not in initial scope.
