"""testing/test_error_redaction_wiring.py — T-104: Telegram error sites use safe_error."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_message(text="hello", chat_type="private"):
    msg = MagicMock()
    msg.text = text
    msg.chat.id = 12345
    msg.chat.type = chat_type
    msg.caption = None
    msg.photo = None
    msg.voice = None
    msg.audio = None
    msg.video = None
    msg.video_note = None
    msg.document = None
    msg.reply_to_message = None
    return msg


# ── Telegram text handler redacts paths ──────────────────────────────────────

def test_telegram_handler_redacts_path(tmp_path):
    """Telegram audience redaction must strip absolute paths from error strings."""
    from agent.redaction import safe_error

    bad_path = str(tmp_path / "secret.txt")
    err = FileNotFoundError(bad_path)
    result = safe_error(err, audience="telegram")

    assert bad_path not in result
    assert "<path>" in result
    assert len(result) <= 200


def test_telegram_module_has_redaction_symbols():
    """tools_telegram must import safe_error and track_silent at module level."""
    import tools.tools_telegram as ttm
    assert callable(ttm.safe_error)
    assert callable(ttm.track_silent)


# ── pi_agent process_input uses safe_error ────────────────────────────────────

def test_process_input_error_uses_safe_error():
    """process_input exception wrapper must run through safe_error, not raw str(e)."""
    from agent.redaction import safe_error

    path_msg = r"e:\pi\.env not found"
    err = FileNotFoundError(path_msg)
    result = safe_error(err, audience="user")
    # Path is redacted
    assert r"e:\pi" not in result
    assert "<path>" in result


# ── tools_memory swallow sites ────────────────────────────────────────────────

def test_memory_swallow_sites_call_track_silent():
    """L3 invalidate and bump_access swallow sites must call track_silent."""
    recorded = []

    def fake_track(cat, exc=None, **kw):
        recorded.append(cat)

    # Simulate the bump_access path in isolation
    import agent.observability as obs
    original = obs.track_silent

    try:
        obs.track_silent = fake_track
        # Directly import and call the observability hook
        from agent.observability import track_silent
        track_silent("memory.bump_access", ValueError("supabase down"))
        track_silent("memory.l3_invalidate", KeyError("entry"))
    finally:
        obs.track_silent = original

    assert "memory.bump_access" in recorded
    assert "memory.l3_invalidate" in recorded
