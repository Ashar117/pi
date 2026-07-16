"""testing/test_telegram_media_send.py — T-230: telegram_send file delivery.

Tests (offline, mock bot):
  - kind inferred from extension for each major type
  - each kind routes to the correct bot method
  - missing file returns path-fallback, not exception
  - text-only call still works (backward compat)
  - large file returns error with path
"""
import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


def _mock_bot():
    bot = MagicMock()
    bot.send_photo = MagicMock(return_value=None)
    bot.send_video = MagicMock(return_value=None)
    bot.send_document = MagicMock(return_value=None)
    bot.send_audio = MagicMock(return_value=None)
    bot.send_voice = MagicMock(return_value=None)
    bot.send_message = MagicMock(return_value=None)
    return bot


def _touch(path: str, size_bytes: int = 10) -> str:
    with open(path, "wb") as f:
        f.write(b"x" * size_bytes)
    return path


# ── Extension → kind inference ────────────────────────────────────────────────

def test_ext_jpg_infers_photo(tmp_path):
    from tools.tools_telegram import send_file, _EXT_TO_KIND
    assert _EXT_TO_KIND[".jpg"] == "photo"
    assert _EXT_TO_KIND[".png"] == "photo"


def test_ext_mp4_infers_video():
    from tools.tools_telegram import _EXT_TO_KIND
    assert _EXT_TO_KIND[".mp4"] == "video"


def test_ext_pdf_infers_document():
    from tools.tools_telegram import _EXT_TO_KIND
    assert _EXT_TO_KIND[".pdf"] == "document"


def test_ext_ogg_infers_voice():
    from tools.tools_telegram import _EXT_TO_KIND
    assert _EXT_TO_KIND[".ogg"] == "voice"


def test_ext_mp3_infers_audio():
    from tools.tools_telegram import _EXT_TO_KIND
    assert _EXT_TO_KIND[".mp3"] == "audio"


# ── Correct bot method called ─────────────────────────────────────────────────

def test_photo_calls_send_photo(tmp_path):
    f = _touch(str(tmp_path / "img.jpg"))
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        with patch("tools.tools_telegram._ALLOWED_CHAT_ID", "12345"):
            from tools.tools_telegram import send_file
            result = send_file(f, chat_id="12345")
    assert result["success"] is True
    bot.send_photo.assert_called_once()
    bot.send_video.assert_not_called()


def test_video_calls_send_video(tmp_path):
    f = _touch(str(tmp_path / "clip.mp4"))
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        with patch("tools.tools_telegram._ALLOWED_CHAT_ID", "12345"):
            from tools.tools_telegram import send_file
            result = send_file(f, chat_id="12345")
    assert result["success"] is True
    bot.send_video.assert_called_once()


def test_document_calls_send_document(tmp_path):
    f = _touch(str(tmp_path / "report.pdf"))
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        with patch("tools.tools_telegram._ALLOWED_CHAT_ID", "12345"):
            from tools.tools_telegram import send_file
            result = send_file(f, chat_id="12345")
    assert result["success"] is True
    bot.send_document.assert_called_once()


def test_audio_calls_send_audio(tmp_path):
    f = _touch(str(tmp_path / "song.mp3"))
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        with patch("tools.tools_telegram._ALLOWED_CHAT_ID", "12345"):
            from tools.tools_telegram import send_file
            result = send_file(f, chat_id="12345")
    assert result["success"] is True
    bot.send_audio.assert_called_once()


def test_explicit_kind_overrides_extension(tmp_path):
    f = _touch(str(tmp_path / "img.jpg"))  # would be photo
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        with patch("tools.tools_telegram._ALLOWED_CHAT_ID", "12345"):
            from tools.tools_telegram import send_file
            result = send_file(f, kind="document", chat_id="12345")
    assert result["success"] is True
    bot.send_document.assert_called_once()
    bot.send_photo.assert_not_called()


# ── Error cases ───────────────────────────────────────────────────────────────

def test_missing_file_returns_graceful_fallback():
    from tools.tools_telegram import send_file
    result = send_file("/tmp/does_not_exist_xyzzy.jpg", chat_id="12345")
    assert result["success"] is False
    assert "path" in result
    assert "note" in result


def test_no_chat_id_returns_graceful_fallback(tmp_path):
    f = _touch(str(tmp_path / "img.jpg"))
    with patch("tools.tools_telegram._ALLOWED_CHAT_ID", ""):
        from tools.tools_telegram import send_file
        result = send_file(f, chat_id=None)
    assert result["success"] is False
    assert "note" in result


def test_large_file_returns_error_not_exception(tmp_path):
    f = _touch(str(tmp_path / "big.mp4"), size_bytes=10)  # tiny real file
    # Simulate a file that reports 60 MB
    import tools.tools_telegram as tg_mod
    import os
    orig_getsize = os.path.getsize
    try:
        os.path.getsize = lambda p: 60 * 1024 * 1024
        bot = _mock_bot()
        with patch("tools.tools_telegram._get_bot", return_value=bot):
            result = tg_mod.send_file(f, chat_id="12345")
    finally:
        os.path.getsize = orig_getsize
    assert result["success"] is False
    assert "too large" in result.get("note", "").lower() or "MB" in result.get("note", "")


# ── Backward compat: text-only still works ────────────────────────────────────

def test_text_only_call_still_works():
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        with patch("tools.tools_telegram._ALLOWED_CHAT_ID", "12345"):
            from tools.tools_telegram import send_message
            result = send_message("Hello!", chat_id="12345")
    assert result["success"] is True
    bot.send_message.assert_called_once()


def test_handle_send_with_file_routes_to_send_file(tmp_path):
    f = _touch(str(tmp_path / "img.png"))
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        with patch("tools.tools_telegram._ALLOWED_CHAT_ID", "12345"):
            from tools.tools_telegram import _handle_telegram_send
            result = _handle_telegram_send(None, {"text": "Here's the image", "file": f, "chat_id": "12345"})
    assert result["success"] is True
    bot.send_photo.assert_called_once()


def test_handle_send_without_file_routes_to_send_message():
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        with patch("tools.tools_telegram._ALLOWED_CHAT_ID", "12345"):
            from tools.tools_telegram import _handle_telegram_send
            result = _handle_telegram_send(None, {"text": "Just text", "chat_id": "12345"})
    assert result["success"] is True
    bot.send_message.assert_called_once()
    bot.send_photo.assert_not_called()


if __name__ == "__main__":
    import inspect
    import traceback
    import pathlib
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    with tempfile.TemporaryDirectory() as td:
        tp = pathlib.Path(td)
        for name, fn in tests:
            try:
                sig = inspect.signature(fn)
                params = list(sig.parameters.keys())
                fn(tp) if "tmp_path" in params else fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as e:
                print(f"  FAIL  {name}: {e}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
