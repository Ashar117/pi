"""testing/test_telegram_media_routing.py — T-138 verification.

Asserts that media handlers (photo/video/document/voice) now route through
PiAgent.process_input (via self._process_text), so vision/STT output enters
Pi's consciousness loop instead of bypassing memory and landing as raw text.

Also verifies that media replies use parse_mode=None (plain text) — Markdown
parsing on raw vision output is what was silently breaking replies.
"""
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_msg(chat_id=42, caption=None, content_type="photo"):
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = "private"
    msg.caption = caption
    msg.date = time.time()
    msg.message_id = 1
    return msg


def _build_tt_with_fake_bot(on_message_callback):
    """Build a TelegramTools with a mocked bot + on_message callback (no real agent)."""
    from tools.tools_telegram import TelegramTools
    tt = TelegramTools(agent=None, on_message=on_message_callback, use_bubble=False)
    # Replace bot with a mock — bypass _get_bot
    tt._bot = MagicMock()
    return tt


def test_send_plain_chunks_uses_parse_mode_none():
    """_send_plain_chunks must pass parse_mode=None to reply_to."""
    captured = []
    received = []
    tt = _build_tt_with_fake_bot(lambda t: received.append(t) or "ok")
    fake_bot = MagicMock()
    fake_bot.reply_to = MagicMock(side_effect=lambda m, c, **kw: captured.append(kw))
    msg = _make_msg()
    tt._send_plain_chunks(fake_bot, msg, "hello *world* _with_ [markdown]")
    assert captured, "reply_to was never called"
    assert captured[0].get("parse_mode") is None


def test_media_route_builds_framed_input_and_calls_process_text():
    """_media_route should construct a framed user_input and pass it to _process_text."""
    received = []
    tt = _build_tt_with_fake_bot(lambda t: received.append(t) or "PI_REPLY")
    msg = _make_msg(caption="what is this?")
    out = tt._media_route(msg, "A red apple on a table", kind="photo", caption="what is this?")
    assert out == "PI_REPLY"
    assert received, "_process_text was never called"
    framed = received[0]
    assert "photo" in framed.lower()
    assert "red apple" in framed
    assert "what is this?" in framed


def test_media_route_voice_passes_transcript_verbatim():
    """For voice the framing is identity — the transcript itself is the user_input."""
    received = []
    tt = _build_tt_with_fake_bot(lambda t: received.append(t) or "ok")
    msg = _make_msg()
    tt._media_route(msg, "do i have any plans tomorrow", kind="voice", caption="")
    assert received == ["do i have any plans tomorrow"]


def test_handle_photo_routes_through_process_text():
    """A photo message should: analyze → _process_text(framed_text) → _send_plain_chunks(reply)."""
    received = []
    tt = _build_tt_with_fake_bot(lambda t: received.append(t) or "PI_SAYS_HELLO")
    tt._bot.message_handler = lambda **kw: (lambda fn: fn)  # decorator passthrough
    tt._bot.send_chat_action = MagicMock()
    tt._bot.reply_to = MagicMock()
    tt._bot.get_file = MagicMock(return_value=MagicMock(file_path="x"))
    tt._bot.download_file = MagicMock(return_value=b"\x89PNG fake")

    # Build a photo message
    msg = _make_msg(caption="what's this?")
    photo_size = MagicMock()
    photo_size.file_id = "FID"
    msg.photo = [photo_size]

    # Patch MediaTools.analyze_image to return a known string
    with patch("tools.tools_media.MediaTools.analyze_image") as mock_analyze, \
         patch.object(tt, "_send_plain_chunks") as mock_send:
        mock_analyze.return_value = {"success": True, "analysis": "A *red* apple on a table"}
        # Re-register handlers to capture handle_photo
        captured_handlers = {}
        def fake_decorator(**kw):
            def wrapper(fn):
                ct = (kw.get("content_types") or [None])[0]
                captured_handlers[ct or "cmd"] = fn
                return fn
            return wrapper
        tt._bot.message_handler = fake_decorator
        tt._register_handlers()

        handle_photo = captured_handlers["photo"]
        handle_photo(msg)

    # Assertions: process_text was called with framed input containing the analysis
    assert received, "process_text not called"
    framed = received[0]
    assert "red" in framed and "apple" in framed
    assert "what's this?" in framed
    # And the reply went out via plain chunks (parse_mode None path)
    assert mock_send.called
    sent_args, _ = mock_send.call_args
    assert sent_args[2] == "PI_SAYS_HELLO"


def test_handle_photo_falls_through_when_vision_fails():
    """If vision returns an error, _media_route is still called with the error text."""
    received = []
    tt = _build_tt_with_fake_bot(lambda t: received.append(t) or "PI_ACK_ERROR")
    tt._bot.send_chat_action = MagicMock()
    tt._bot.reply_to = MagicMock()
    tt._bot.get_file = MagicMock(return_value=MagicMock(file_path="x"))
    tt._bot.download_file = MagicMock(return_value=b"\x89PNG")

    msg = _make_msg(caption=None)
    photo_size = MagicMock()
    photo_size.file_id = "FID"
    msg.photo = [photo_size]

    with patch("tools.tools_media.MediaTools.analyze_image") as mock_analyze, \
         patch.object(tt, "_send_plain_chunks"):
        mock_analyze.return_value = {"success": False, "error": "rate limited"}
        captured_handlers = {}
        def fake_decorator(**kw):
            def wrapper(fn):
                ct = (kw.get("content_types") or [None])[0]
                captured_handlers[ct or "cmd"] = fn
                return fn
            return wrapper
        tt._bot.message_handler = fake_decorator
        tt._register_handlers()
        captured_handlers["photo"](msg)

    assert received, "process_text not called on vision failure"
    assert "rate limited" in received[0] or "failed" in received[0].lower()


def test_handle_voice_uses_plain_chunks():
    """Voice handler reply must go through _send_plain_chunks, not _reply_chunks (T-138)."""
    received = []
    tt = _build_tt_with_fake_bot(lambda t: received.append(t) or "PI_REPLY")
    tt._bot.send_chat_action = MagicMock()
    tt._bot.reply_to = MagicMock()
    tt._bot.get_file = MagicMock(return_value=MagicMock(file_path="x"))
    tt._bot.download_file = MagicMock(return_value=b"OGG fake bytes")

    msg = _make_msg()
    msg.voice = MagicMock()
    msg.voice.file_id = "VID"
    msg.audio = None

    captured_handlers = {}
    def fake_decorator(**kw):
        def wrapper(fn):
            ct = (kw.get("content_types") or [None])[0]
            captured_handlers[ct or "cmd"] = fn
            return fn
        return wrapper
    tt._bot.message_handler = fake_decorator

    with patch("tools.tools_stt.STTTools") as mock_stt_cls, \
         patch.object(tt, "_send_plain_chunks") as mock_send_plain, \
         patch.object(tt, "_reply_chunks") as mock_reply_chunks:
        stt_inst = MagicMock()
        stt_inst.transcribe_file.return_value = {"success": True, "text": "what's the weather"}
        mock_stt_cls.return_value = stt_inst
        tt._register_handlers()
        captured_handlers["voice"](msg)

    # Pi must have been invoked with the transcript
    assert received == ["what's the weather"]
    # Reply must have gone through _send_plain_chunks (T-138), not the Markdown _reply_chunks
    assert mock_send_plain.called, "_send_plain_chunks was not called"
    assert not mock_reply_chunks.called, "_reply_chunks must not be used on voice path"
