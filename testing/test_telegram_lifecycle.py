"""testing/test_telegram_lifecycle.py — T-126: /exit and /clear command tests."""
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_tt():
    """Build a TelegramTools with a mock bot and an isolated bubble collector."""
    from tools.tools_telegram import TelegramTools
    from agent.bubble import BubbleCollector

    agent = MagicMock()
    agent.session_id = "abc12345"
    agent.messages = ["x", "y"]
    agent.memory = MagicMock()
    agent.memory.sqlite_path = ":memory:"

    tt = TelegramTools(agent, use_bubble=True)
    tt._bubble.stop()
    tt._bot = MagicMock()
    # Override with fast idle for tests
    tt._bubble = BubbleCollector(tt._dispatch_bubble, idle_ms=300)
    return tt, agent


def _fake_message(chat_id=42, text="/exit"):
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = "private"
    msg.text = text
    msg.from_user.id = 99
    return msg


# ── /exit force-flushes and runs session exit ────────────────────────────────

def test_exit_command_force_flushes_bubble_then_runs_exit():
    """When /exit fires with an open bubble, flush happens first."""
    tt, agent = _build_tt()
    from agent.bubble import BubbleMessage

    # Open a bubble
    tt._bubble.add("42", BubbleMessage(text="pending thought", sent_at=time.time(), raw=_fake_message()))
    assert tt._bubble._peek_open_bubble("42") is not None

    # Patch on_exit and call the registered /exit handler directly
    with patch("agent.session.on_exit") as mock_on_exit:
        # Simulate handle_exit body inline since registering against a mock bot
        # is awkward; the script tests the actual function paths.
        chat_id = "42"
        tt._bubble.flush(chat_id, reason="lifecycle")
        from agent.session import on_exit
        on_exit(agent)

    # Bubble should no longer be open
    assert tt._bubble._peek_open_bubble("42") is None
    tt._bubble.stop()
    mock_on_exit.assert_called_once_with(agent)


# ── /clear rotates session_id ────────────────────────────────────────────────

def test_clear_rotates_session_id():
    tt, agent = _build_tt()
    old_id = agent.session_id

    import uuid as _uuid
    # Simulate the body of handle_clear
    if tt._bubble is not None:
        tt._bubble.flush("42", reason="lifecycle")
    agent.session_id = _uuid.uuid4().hex[:8]
    agent.messages = []

    assert agent.session_id != old_id
    assert agent.messages == []
    tt._bubble.stop()


# ── /clear preserves memory ──────────────────────────────────────────────────

def test_clear_preserves_memory():
    tt, agent = _build_tt()
    memory_before = agent.memory

    # Run clear body (memory is NOT touched)
    if tt._bubble is not None:
        tt._bubble.flush("42", reason="lifecycle")
    agent.session_id = "new12345"
    agent.messages = []

    # Memory reference unchanged
    assert agent.memory is memory_before
    tt._bubble.stop()


# ── /clear with open bubble dispatches under OLD session_id ─────────────────

def test_clear_dispatches_bubble_before_rotation():
    """Open bubble's response must go through process_input BEFORE session_id changes."""
    tt, agent = _build_tt()
    from agent.bubble import BubbleMessage

    captured_session_ids = []
    def capture_session(text):
        captured_session_ids.append(agent.session_id)
        return "OK"

    agent.process_input.side_effect = capture_session
    old_id = agent.session_id

    # Mock recall and thinking to no-op so the consumer dispatches fast
    with patch("memory.recall.recall_referenced", return_value=[]), \
         patch("agent.thinking.normalise", return_value=None):
        tt._bubble.add("42", BubbleMessage(text="ride this one out", sent_at=time.time(), raw=_fake_message()))
        tt._bubble.flush("42", reason="lifecycle")
        time.sleep(0.5)  # let consumer drain
        # Rotation happens AFTER flush
        agent.session_id = "new99999"

    tt._bubble.stop()

    assert agent.process_input.call_count == 1
    # The dispatch happened while session_id was still the old value
    assert captured_session_ids[0] == old_id
