"""tools/tools_wakeword.py — Wake-word detection via openwakeword (T-054).

Listens continuously on the microphone for a wake word ("hey pi" / "ok pi").
Returns True the moment a detection fires, so the caller can start recording.

Dependencies:
    pip install openwakeword sounddevice numpy

Usage:
    from tools.tools_wakeword import WakeWordDetector
    det = WakeWordDetector()
    det.wait_for_wake_word()   # blocks until "hey pi" heard
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger(__name__)

# Audio parameters
_SAMPLE_RATE = 16000
_CHUNK_MS = 80   # openwakeword prefers ~80ms windows
_CHUNK_SAMPLES = int(_SAMPLE_RATE * _CHUNK_MS / 1000)

# Default wake words (openwakeword built-in models)
_DEFAULT_MODELS = ["hey_pi", "ok_pi", "alexa"]  # fallback to alexa if custom not available
_SCORE_THRESHOLD = 0.5


class WakeWordDetector:
    """Continuous wake-word listener.  Call wait_for_wake_word() to block
    until a detection fires.  Thread-safe — safe to use from voice_loop.
    """

    def __init__(
        self,
        models: Optional[list] = None,
        threshold: float = _SCORE_THRESHOLD,
    ):
        self.threshold = threshold
        self._models = models or _DEFAULT_MODELS
        self._oww = None
        self._lock = threading.Lock()

    def _get_oww(self):
        with self._lock:
            if self._oww is not None:
                return self._oww
            try:
                from openwakeword.model import Model
                # Try loading custom models; fall back to built-in
                try:
                    self._oww = Model(wakeword_models=self._models, inference_framework="onnx")
                except Exception:
                    self._oww = Model(inference_framework="onnx")
                log.info("[WakeWord] model loaded")
                return self._oww
            except ImportError:
                raise RuntimeError(
                    "openwakeword not installed. Run: pip install openwakeword"
                )

    def wait_for_wake_word(self, timeout_secs: float = 0) -> bool:
        """Block until wake word detected (or timeout). Returns True on detection."""
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError:
            raise RuntimeError("sounddevice + numpy required for wake word. pip install sounddevice numpy")

        oww = self._get_oww()
        oww.reset()

        deadline = None
        if timeout_secs > 0:
            import time
            deadline = time.monotonic() + timeout_secs

        def _audio_callback(indata, frames, time_info, status):
            pass  # captured via queue below

        import queue, time
        q: queue.Queue = queue.Queue()

        def _cb(indata, frames, time_info, status):
            q.put(indata.copy())

        with sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=_CHUNK_SAMPLES,
            callback=_cb,
        ):
            while True:
                if deadline and time.monotonic() > deadline:
                    return False
                try:
                    chunk = q.get(timeout=0.5)
                except queue.Empty:
                    continue

                audio_1d = chunk[:, 0] if chunk.ndim > 1 else chunk
                prediction = oww.predict(audio_1d)

                for model_name, scores in prediction.items():
                    score = scores if isinstance(scores, float) else max(scores)
                    if score >= self.threshold:
                        log.info("[WakeWord] detected '%s' (score=%.2f)", model_name, score)
                        return True

    def is_available(self) -> bool:
        """Return True if openwakeword is installed."""
        try:
            import openwakeword  # noqa: F401
            return True
        except ImportError:
            return False
