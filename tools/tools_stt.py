"""tools/tools_stt.py — Speech-to-text via faster-whisper (T-047).

Exposes:
  STTTools.transcribe_file(path)        — transcribe an audio file
  STTTools.transcribe_mic(seconds)      — record from mic then transcribe
  STTTools.get_tool_definitions()       — tool schema for agent dispatch

Model is loaded lazily on first use. Default: base.en (74M params, fast+accurate).
Override with STT_MODEL env var (tiny.en / base.en / small.en / medium.en / large-v3).
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper not installed. Run: pip install faster-whisper"
            ) from e

        model_name = os.getenv("STT_MODEL", "base.en")
        device = "cpu"
        compute_type = "int8"

        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
                compute_type = "float16"
        except ImportError:
            pass

        _model = WhisperModel(model_name, device=device, compute_type=compute_type)
        return _model


def _transcribe(audio_path: str) -> str:
    model = _get_model()
    segments, _ = model.transcribe(audio_path, beam_size=5, language="en")
    return " ".join(seg.text.strip() for seg in segments).strip()


def _record_mic(seconds: int, path: str) -> None:
    try:
        import sounddevice as sd
        import soundfile as sf
        import numpy as np
    except ImportError as e:
        raise RuntimeError(
            "sounddevice + soundfile required for mic capture. "
            "Run: pip install sounddevice soundfile"
        ) from e

    samplerate = 16000
    audio = sd.rec(
        int(seconds * samplerate),
        samplerate=samplerate,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    sf.write(path, audio, samplerate)


class STTTools:
    """Speech-to-text tools for Pi."""

    def transcribe_file(self, path: str) -> dict:
        """Transcribe an audio file to text.

        Args:
            path: Path to audio file (wav, mp3, m4a, flac, etc.)

        Returns:
            {"success": True, "text": "...", "path": "..."}
        """
        p = Path(path)
        if not p.exists():
            return {"success": False, "error": f"File not found: {path}"}
        try:
            text = _transcribe(str(p))
            return {"success": True, "text": text, "path": str(p)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def transcribe_mic(self, seconds: int = 5) -> dict:
        """Record from microphone and transcribe.

        Args:
            seconds: Recording duration (default 5, max 120)

        Returns:
            {"success": True, "text": "...", "duration": N}
        """
        seconds = max(1, min(int(seconds), 120))
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = f.name
            _record_mic(seconds, tmp_path)
            text = _transcribe(tmp_path)
            return {"success": True, "text": text, "duration": seconds}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    @staticmethod
    def get_tool_definitions() -> list:
        return [
            {
                "name": "transcribe_file",
                "description": "Transcribe an audio file to text using Whisper STT.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to audio file (wav, mp3, m4a, flac, ogg, etc.)",
                        }
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "listen",
                "description": (
                    "Record audio from the microphone for N seconds and transcribe to text. "
                    "Use when the user wants to speak input or when voice mode is active."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "seconds": {
                            "type": "integer",
                            "description": "Recording duration in seconds (default 5, max 120)",
                        }
                    },
                    "required": [],
                },
            },
        ]


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_listen(agent, tool_input, *, memory_override=None):
    return STTTools().transcribe_mic(seconds=tool_input.get("seconds", 5))


def _handle_transcribe_file(agent, tool_input, *, memory_override=None):
    return STTTools().transcribe_file(path=tool_input["path"])


TOOLS = [
    ToolSpec(
        name="listen",
        description=(
            "Record audio from the microphone and transcribe to text using Whisper STT. "
            "Use when the user speaks input or when voice mode is active."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "seconds": {"type": "integer",
                            "description": "Recording duration in seconds (default 5, max 120)"},
            },
            "required": [],
        },
        handler=_handle_listen,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="transcribe_file",
        description="Transcribe an audio file to text using Whisper STT.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Path to audio file (wav, mp3, m4a, flac, ogg, etc.)"},
            },
            "required": ["path"],
        },
        handler=_handle_transcribe_file,
        success_predicate=lambda r: r.get("success", False),
    ),
]
