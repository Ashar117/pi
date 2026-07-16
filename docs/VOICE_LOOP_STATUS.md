# Voice loop status (T-267)

**Code: complete and tested. Live hardware verification: blocked in this
session's environment.** This is an honest close, not a punt — per T-267's
own risk notes, "an honest blocker doc is a fully legitimate outcome."

## What's actually there

`agent/voice_loop.py` implements the full loop end to end:

- **PTT** (push-to-talk) — Enter to start/stop recording.
- **VAD** (voice activity detection via silero-vad) — hands-free, ends on
  1.5s silence, with a pre-speech ring buffer so the onset isn't clipped.
- **WAKE** — wake word (openwakeword) gates entry into VAD.
- **Barge-in** (`BargeinMonitor`) — monitors the mic during TTS playback and
  interrupts speech if the user starts talking.
- Every recording path degrades gracefully (returns `None` / falls back to a
  fixed-length recording) when a dependency is missing, rather than crashing.
- `VoiceLoop.run()` wires it all together: record → transcribe (`tools_stt`)
  → `agent.process_input()` → speak (`tts.speak()`), with a `"voice on"` /
  `"voice off"` toggle already present in `pi_agent.py`.

`testing/test_voice_loop.py` — 13 tests, all passing. They cover every
degradation path (missing sounddevice, missing torch/silero-vad, missing
openwakeword) by patching `sys.modules`, plus the transcribe → respond →
speak wiring with mocked STT/TTS. This is real coverage of the loop's logic.

## The actual blocker

This session's Python environment is missing every audio-hardware dependency
`requirements.txt` lists for voice:

```
sounddevice   — MISSING
soundfile     — MISSING
torch         — MISSING (silero-vad needs it)
openwakeword  — MISSING
```

None of these can be exercised without a live microphone and speakers, which
this sandboxed coding session doesn't have access to regardless of whether
the packages are installed. Installing ~2GB of `torch` here wouldn't change
that — it would just prove the code *imports*, not that voice actually works
end to end with a human talking into a real mic.

## Smallest unblock

Run this on Ash's actual desktop (where these packages are presumably
already installed, since the feature was built and documented as live):

```bash
python pi_agent.py
> voice on
# speak into the mic; confirm transcription + spoken reply
> voice off
```

If that already works, this ticket's real content is done — the code was
never the gap, hardware access from a coding session is. If it *doesn't*
work on the desktop either, the first failure message (import error vs.
device error vs. silent timeout) tells you which of the three sub-modes to
debug next, and that's a concrete new ticket, not a re-run of this one.
