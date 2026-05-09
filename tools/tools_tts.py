"""
tools/tools_tts.py — Text-to-speech for Pi.

Primary: pyttsx3 (offline, Windows SAPI5, no API key).
Optional: gTTS (Google, online, better voices) when GTTS_LANG is set or requested.

Usage:
    from tools.tools_tts import TTSTools
    tts = TTSTools()
    tts.speak("Hello Ash")           # blocking
    tts.speak_async("message")       # fire-and-forget thread
    tts.save("message", "out.mp3")   # save to file
"""

import threading
from pathlib import Path
from typing import Dict, Optional


class TTSTools:
    """Text-to-speech output for Pi."""

    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()
        self._speaking = False

    def _get_engine(self):
        """Lazy-init pyttsx3 engine (Windows SAPI5 by default)."""
        if self._engine is None:
            try:
                import pyttsx3
                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", 175)   # words per minute
                self._engine.setProperty("volume", 0.9)
                voices = self._engine.getProperty("voices")
                if voices:
                    for v in voices:
                        if "english" in v.name.lower() or "david" in v.name.lower():
                            self._engine.setProperty("voice", v.id)
                            break
            except Exception:
                self._engine = None
        return self._engine

    def speak(self, text: str, rate: Optional[int] = None) -> Dict:
        """Speak text aloud using pyttsx3 (blocking).

        Args:
            text: Text to speak.
            rate: Words per minute override (default 175).

        Returns:
            {"success": bool, "backend": str, "error": str (if failed)}
        """
        if not text or not text.strip():
            return {"success": False, "error": "Empty text"}

        with self._lock:
            engine = self._get_engine()
            if engine is None:
                return {"success": False, "backend": "pyttsx3", "error": "pyttsx3 init failed"}

            try:
                if rate:
                    engine.setProperty("rate", rate)
                engine.say(text)
                engine.runAndWait()
                return {"success": True, "backend": "pyttsx3", "chars": len(text)}
            except Exception as e:
                return {"success": False, "backend": "pyttsx3", "error": str(e)}

    def speak_async(self, text: str) -> Dict:
        """Speak text in a background thread (non-blocking).

        Returns immediately. Pi can continue responding while speech plays.
        """
        if not text or not text.strip():
            return {"success": False, "error": "Empty text"}

        def _run():
            self.speak(text)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return {"success": True, "backend": "pyttsx3_async", "chars": len(text)}

    def save(self, text: str, path: str) -> Dict:
        """Save speech to an audio file (WAV via pyttsx3, MP3 via gTTS if available).

        Args:
            text: Text to convert.
            path: Output file path (.wav or .mp3).

        Returns:
            {"success": bool, "path": str, "backend": str}
        """
        if not text or not text.strip():
            return {"success": False, "error": "Empty text"}

        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Try gTTS for MP3 first (better quality)
        if str(path).lower().endswith(".mp3"):
            try:
                from gtts import gTTS
                tts = gTTS(text=text, lang="en", slow=False)
                tts.save(str(out_path))
                return {"success": True, "path": str(out_path), "backend": "gtts"}
            except ImportError:
                pass
            except Exception as e:
                return {"success": False, "error": f"gTTS: {e}"}

        # Fall back to pyttsx3 WAV
        with self._lock:
            engine = self._get_engine()
            if engine is None:
                return {"success": False, "error": "pyttsx3 not available"}
            try:
                engine.save_to_file(text, str(out_path))
                engine.runAndWait()
                return {"success": True, "path": str(out_path), "backend": "pyttsx3"}
            except Exception as e:
                return {"success": False, "error": str(e)}

    def list_voices(self) -> Dict:
        """Return available TTS voices on this system."""
        engine = self._get_engine()
        if engine is None:
            return {"success": False, "voices": [], "error": "pyttsx3 not available"}

        voices = engine.getProperty("voices") or []
        return {
            "success": True,
            "voices": [{"id": v.id, "name": v.name} for v in voices],
            "count": len(voices),
        }

    def stop(self) -> Dict:
        """Stop any in-progress speech."""
        with self._lock:
            if self._engine:
                try:
                    self._engine.stop()
                    return {"success": True}
                except Exception as e:
                    return {"success": False, "error": str(e)}
        return {"success": True}


# Module-level singleton
_tts = TTSTools()
