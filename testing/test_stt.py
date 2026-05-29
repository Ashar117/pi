"""Tests for tools/tools_stt.py (T-047).

All tests avoid actual model inference and mic hardware — we mock at the
boundary so the test suite stays fast and CI-safe.
"""

import os
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_faster_whisper():
    """Build a minimal fake faster_whisper module."""
    fw = types.ModuleType("faster_whisper")

    class FakeSegment:
        def __init__(self, text):
            self.text = text

    class FakeModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, path, **kw):
            return [FakeSegment(" hello world ")], None

    fw.WhisperModel = FakeModel
    return fw


# ---------------------------------------------------------------------------
# Import the module under test (with faster_whisper faked out)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_model_singleton():
    """Reset the module-level _model singleton between tests."""
    import tools.tools_stt as stt_mod
    stt_mod._model = None
    yield
    stt_mod._model = None


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestSTTToolsTranscribeFile:

    def test_file_not_found(self):
        from tools.tools_stt import STTTools
        result = STTTools().transcribe_file("/does/not/exist.wav")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_transcribe_real_file(self, tmp_path):
        """With faster_whisper mocked, transcription returns joined segment text."""
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"RIFF")  # fake wav content

        fake_fw = _make_fake_faster_whisper()
        with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
            import importlib
            import tools.tools_stt as stt_mod
            importlib.reload(stt_mod)
            stt_mod._model = None

            result = stt_mod.STTTools().transcribe_file(str(wav))

        assert result["success"] is True
        assert result["text"] == "hello world"
        assert "path" in result

    def test_model_error_caught(self, tmp_path):
        wav = tmp_path / "bad.wav"
        wav.write_bytes(b"data")

        bad_fw = types.ModuleType("faster_whisper")
        class BrokenModel:
            def __init__(self, *a, **kw): pass
            def transcribe(self, *a, **kw): raise RuntimeError("model exploded")
        bad_fw.WhisperModel = BrokenModel

        with patch.dict("sys.modules", {"faster_whisper": bad_fw}):
            import importlib
            import tools.tools_stt as stt_mod
            importlib.reload(stt_mod)
            stt_mod._model = None
            result = stt_mod.STTTools().transcribe_file(str(wav))

        assert result["success"] is False
        assert "error" in result


class TestSTTToolsTranscribeMic:

    def test_mic_success(self):
        """Mock sounddevice + soundfile + faster_whisper — happy path."""
        import importlib
        import tools.tools_stt as stt_mod

        fake_fw = _make_fake_faster_whisper()

        sd_mock = MagicMock()
        sd_mock.rec.return_value = None
        sd_mock.wait.return_value = None

        sf_mock = MagicMock()

        with patch.dict("sys.modules", {
            "faster_whisper": fake_fw,
            "sounddevice": sd_mock,
            "soundfile": sf_mock,
        }):
            importlib.reload(stt_mod)
            stt_mod._model = None
            result = stt_mod.STTTools().transcribe_mic(seconds=3)

        assert result["success"] is True
        assert result["text"] == "hello world"
        assert result["duration"] == 3

    def test_mic_seconds_clamped(self):
        """seconds is clamped to [1, 120] before _record_mic is called."""
        import importlib
        import tools.tools_stt as stt_mod

        fake_fw = _make_fake_faster_whisper()
        captured = []

        def fake_record_mic(seconds, path):
            captured.append(seconds)

        with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
            importlib.reload(stt_mod)
            stt_mod._model = None
            with patch.object(stt_mod, "_record_mic", side_effect=fake_record_mic):
                with patch.object(stt_mod, "_transcribe", return_value="hi"):
                    stt_mod.STTTools().transcribe_mic(seconds=0)
                    stt_mod.STTTools().transcribe_mic(seconds=999)

        assert captured[0] == 1
        assert captured[1] == 120

    def test_mic_missing_dep_error(self):
        """If sounddevice not installed, error is returned cleanly."""
        import importlib
        import tools.tools_stt as stt_mod

        fake_fw = _make_fake_faster_whisper()

        with patch.dict("sys.modules", {
            "faster_whisper": fake_fw,
            "sounddevice": None,
            "soundfile": None,
        }):
            importlib.reload(stt_mod)
            stt_mod._model = None
            result = stt_mod.STTTools().transcribe_mic(seconds=2)

        assert result["success"] is False
        assert "error" in result


class TestSTTToolDefinitions:

    def test_definitions_returned(self):
        from tools.tools_stt import STTTools
        defs = STTTools.get_tool_definitions()
        names = {d["name"] for d in defs}
        assert "listen" in names
        assert "transcribe_file" in names

    def test_listen_schema(self):
        from tools.tools_stt import STTTools
        listen = next(d for d in STTTools.get_tool_definitions() if d["name"] == "listen")
        assert "seconds" in listen["input_schema"]["properties"]
        assert listen["input_schema"]["required"] == []

    def test_transcribe_file_schema(self):
        from tools.tools_stt import STTTools
        tf = next(d for d in STTTools.get_tool_definitions() if d["name"] == "transcribe_file")
        assert "path" in tf["input_schema"]["properties"]
        assert "path" in tf["input_schema"]["required"]


class TestAgentToolsDispatch:

    def test_listen_in_dispatch(self):
        """listen and transcribe_file are in the get_tool_definitions list."""
        import importlib
        import agent.tools as agent_tools_mod

        fake_fw = _make_fake_faster_whisper()
        with patch.dict("sys.modules", {"faster_whisper": fake_fw}):
            importlib.reload(agent_tools_mod)
            defs = agent_tools_mod.get_tool_definitions()

        names = {d["name"] for d in defs}
        assert "listen" in names
        assert "transcribe_file" in names
