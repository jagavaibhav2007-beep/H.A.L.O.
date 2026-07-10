# Halo UI/UX: Voice

How speaking to Halo *feels*. Pipeline & events: [systemdesign/02-voice](../systemdesign/02-voice.md); orb states: [01-companion-orb](01-companion-orb.md).

## The loop, from your side
```
you: "Halo"        → orb ripples + chime (heard you)
you speak          → rim glows, transcript appears live (ghost text in chat/peek)
you stop           → orb swirls (thinking) — no dead silence: swirl starts ≤300ms
Halo answers       → glow pulses with speech; text lands in chat simultaneously
you talk over it   → speech stops INSTANTLY (barge-in), orb back to listening
```
Latency is a feature: acknowledge ≤300ms at every step, even if the answer takes seconds. The orb never looks frozen.

## Trust rules
- **You always see what it heard.** The live transcript is the STT's truth — if it misheard, you see it before damage is done; low-confidence hearings make Halo ask, not guess.
- **The mic state is never ambiguous.** Muted = gray slashed orb, everywhere, always. There is no state where audio leaves the machine without a visible listening indicator.
- **Narration is main-events-only** (never every click), spoken + mirrored as peek-bubble lines. Narration toggle in Settings for silent-except-asked mode.

## Stopping and steering
- Say **"stop"** mid-task: everything halts (≤2s, per the [IPC cancellation rules](../systemdesign/11-ipc-contract.md)), orb goes still, Halo asks *"what should I do differently?"* — your answer resumes from the checkpoint, not from zero. In chat this whole exchange is visible as the interrupted-divider + resume.

## Approvals by voice (user decision)
- Halo speaks Tier-3 requests: *"I need your OK to delete these 12 files — approve or deny?"*
- **"Approve" / "deny" by voice works** — except **money or irreversible-external actions** (buy, checkout, account changes): Halo says *"this one needs a click"* and the card waits. Full rule in [05-permissions-trust](05-permissions-trust.md).

## Degraded modes (honest, not silent)
- TTS down → replies appear as text + one soft chime; a status-strip note says voice replies are off.
- STT down → orb listening state disabled with tooltip; typing still works. Wake word is on-device and keeps working regardless of cloud state.
