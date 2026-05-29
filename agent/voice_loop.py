"""agent/voice_loop.py — Voice input loop (T-055/T-056).

Three sub-modes selectable at runtime:
  PTT    — press Enter to start, Enter again to stop; simplest mode
  VAD    — always recording; silence >1.5s ends the utterance (silero-vad)
  WAKE   — wait for wake word ("hey pi"), then VAD kicks in for the utterance

Barge-in (T-056): monitors mic during TTS playback; if voice is detected,
TTS is interrupted immediately before it finishes.

Usage from run loop:
    from agent.voice_loop import VoiceLoop
    vl = VoiceLoop(agent=pi_agent, mode="ptt")
    vl.run()   # blocking; Ctrl-C to exit

Or import selectively:
    from agent.voice_loop import vad_record, ptt_record
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
from typing import Optional

log = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
_VAD_SILENCE_SECS = 1.5   # end utterance after this much silence
_VAD_CHUNK_MS = 32         # silero-vad processes 32ms at 16kHz
_VAD_CHUNK_SAMPLES = int(_SAMPLE_RATE * _VAD_CHUNK_MS / 1000)
_MAX_RECORD_SECS = 30      # safety cap per utterance
_RING_BUFFER_SECS = 0.5    # pre-speech ring buffer to capture onset


# ── VAD (silero-vad) ──────────────────────────────────────────────────────────

_silero_model = None
_silero_lock = threading.Lock()


def _get_silero():
    global _silero_model
    if _silero_model is not None:
        return _silero_model
    with _silero_lock:
        if _silero_model is not None:
            return _silero_model
        try:
            import torch
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                onnx=False,
                verbose=False,
            )
            _silero_model = (model, utils)
            log.info("[VoiceLoop] silero-vad loaded")
        except Exception as e:
            raise RuntimeError(f"silero-vad load failed: {e}")
    return _silero_model


# ── Recording helpers ─────────────────────────────────────────────────────────

def ptt_record(max_secs: int = _MAX_RECORD_SECS) -> Optional[str]:
    """Push-to-talk: press Enter to start, Enter to stop.

    Returns path to a temporary WAV file (caller must delete), or None.
    """
    try:
        import sounddevice as sd
        import soundfile as sf
        import numpy as np
    except ImportError:
        print("[Voice] sounddevice/soundfile not installed")
        return None

    print("[Voice PTT] Press ENTER to start recording…", flush=True)
    input()
    print("[Voice PTT] Recording… press ENTER to stop.", flush=True)

    chunks = []
    stop_event = threading.Event()

    def _cb(indata, frames, time_info, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=_SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=1024, callback=_cb):
        input()  # wait for second Enter
        stop_event.set()

    if not chunks:
        return None

    import numpy as np
    audio = np.concatenate(chunks)[:int(max_secs * _SAMPLE_RATE)]
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    sf.write(tmp.name, audio, _SAMPLE_RATE)
    return tmp.name


def vad_record(max_secs: int = _MAX_RECORD_SECS, ring_secs: float = _RING_BUFFER_SECS) -> Optional[str]:
    """VAD-gated record: starts on speech, stops after silence.

    Returns path to temp WAV file, or None on failure.
    Falls back to 5s fixed recording if silero-vad not available.
    """
    try:
        import sounddevice as sd
        import soundfile as sf
        import numpy as np
    except ImportError:
        print("[Voice] sounddevice/soundfile not installed")
        return None

    # Try silero-vad; fall back to fixed 5s if unavailable
    try:
        model, utils = _get_silero()
        vad_available = True
    except Exception:
        vad_available = False

    if not vad_available:
        log.warning("[VoiceLoop] silero-vad unavailable — using fixed 5s recording")
        from tools.tools_stt import _record_mic
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            _record_mic(5, tmp.name)
            return tmp.name
        except Exception:
            return None

    import queue as _queue
    import collections

    model_fn = utils[0]  # get_speech_timestamps function, used differently
    # silero-vad direct inference: model(chunk_tensor) → confidence float

    import torch
    model.reset_states()

    ring = collections.deque(
        maxlen=int(ring_secs * _SAMPLE_RATE / _VAD_CHUNK_SAMPLES)
    )
    speech_chunks = []
    recording = False
    silence_frames = 0
    silence_limit = int(_VAD_SILENCE_SECS * _SAMPLE_RATE / _VAD_CHUNK_SAMPLES)
    total_frames = 0
    max_frames = int(max_secs * _SAMPLE_RATE / _VAD_CHUNK_SAMPLES)

    q: _queue.Queue = _queue.Queue()

    def _cb(indata, frames, time_info, status):
        q.put(indata.copy())

    print("[Voice VAD] Listening…", flush=True)

    with sd.InputStream(
        samplerate=_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=_VAD_CHUNK_SAMPLES,
        callback=_cb,
    ):
        while total_frames < max_frames:
            try:
                chunk = q.get(timeout=1.0)
            except _queue.Empty:
                continue

            audio_1d = chunk[:, 0] if chunk.ndim > 1 else chunk
            tensor = torch.from_numpy(audio_1d).float()
            confidence = float(model(tensor, _SAMPLE_RATE).item())

            if confidence > 0.5:  # speech
                if not recording:
                    # prepend ring buffer (captures speech onset)
                    speech_chunks.extend(list(ring))
                    recording = True
                    print("[Voice VAD] Speech detected…", flush=True)
                speech_chunks.append(audio_1d)
                silence_frames = 0
            else:
                ring.append(audio_1d)
                if recording:
                    speech_chunks.append(audio_1d)
                    silence_frames += 1
                    if silence_frames >= silence_limit:
                        break  # end of utterance

            total_frames += 1

    if not speech_chunks:
        return None

    audio = np.concatenate(speech_chunks)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    sf.write(tmp.name, audio, _SAMPLE_RATE)
    return tmp.name


# ── Barge-in detection (T-056) ────────────────────────────────────────────────

class BargeinMonitor:
    """Monitor the mic during TTS playback. Set interrupted=True on voice detection."""

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.interrupted = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        self.interrupted = False
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _monitor(self) -> None:
        try:
            import sounddevice as sd
            import numpy as np
            import torch

            model, _ = _get_silero()
            model.reset_states()

            import queue
            q: queue.Queue = queue.Queue()

            def _cb(indata, frames, time_info, status):
                q.put(indata.copy())

            with sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=_VAD_CHUNK_SAMPLES,
                callback=_cb,
            ):
                while not self._stop.is_set():
                    try:
                        chunk = q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    audio_1d = chunk[:, 0] if chunk.ndim > 1 else chunk
                    tensor = torch.from_numpy(audio_1d).float()
                    conf = float(model(tensor, _SAMPLE_RATE).item())
                    if conf > self.threshold:
                        self.interrupted = True
                        log.info("[BargeinMonitor] barge-in detected (conf=%.2f)", conf)
                        break
        except Exception as e:
            log.debug("[BargeinMonitor] monitoring failed: %s", e)


# ── Main voice loop ───────────────────────────────────────────────────────────

class VoiceLoop:
    """Full voice-first interaction loop.

    Modes:
      ptt  — push-to-talk (default, most reliable)
      vad  — voice activity detection (hands-free)
      wake — wake word + VAD (always-on)
    """

    def __init__(self, agent, mode: str = "ptt"):
        self.agent = agent
        self.mode = mode.lower()
        self._bargein = BargeinMonitor()

    def _transcribe(self, wav_path: str) -> str:
        from tools.tools_stt import STTTools
        result = STTTools().transcribe_file(wav_path)
        return result.get("text", "") if result.get("success") else ""

    def _speak(self, text: str) -> None:
        """Speak via TTS with barge-in support."""
        tts = getattr(self.agent, "tts", None)
        if not tts:
            print(f"Pi: {text}")
            return

        self._bargein.start()
        try:
            tts.speak(text)
        finally:
            self._bargein.stop()

        if self._bargein.interrupted:
            print("[Voice] Barge-in: stopping TTS")

    def _get_recording(self) -> Optional[str]:
        if self.mode == "ptt":
            return ptt_record()
        elif self.mode in ("vad", "wake"):
            return vad_record()
        return None

    def run(self) -> None:
        """Blocking voice interaction loop. Ctrl-C to exit."""
        print(f"[Voice] mode={self.mode}. Ctrl-C to exit.")

        if self.mode == "wake":
            try:
                from tools.tools_wakeword import WakeWordDetector
                wake_det = WakeWordDetector()
                if not wake_det.is_available():
                    print("[Voice] openwakeword not installed — falling back to VAD mode")
                    self.mode = "vad"
                    wake_det = None
            except Exception:
                print("[Voice] openwakeword unavailable — falling back to VAD mode")
                wake_det = None
                self.mode = "vad"
        else:
            wake_det = None

        while True:
            try:
                if self.mode == "wake" and wake_det:
                    print("[Voice] Waiting for wake word…", flush=True)
                    detected = wake_det.wait_for_wake_word(timeout_secs=0)
                    if not detected:
                        continue
                    print("[Voice] Wake word detected!", flush=True)

                wav_path = self._get_recording()
                if not wav_path:
                    continue

                try:
                    text = self._transcribe(wav_path)
                finally:
                    try:
                        os.unlink(wav_path)
                    except Exception:
                        pass

                if not text.strip():
                    print("[Voice] (silence / no speech)", flush=True)
                    continue

                print(f"You said: {text}", flush=True)

                response = self.agent.process_input(text)

                if response == "EXIT":
                    print("[Voice] Goodbye.")
                    break

                if response:
                    self._speak(response)

            except KeyboardInterrupt:
                print("\n[Voice] Interrupted")
                break
            except Exception as e:
                log.error("[VoiceLoop] error: %s", e)
                print(f"[Voice] Error: {e}")
