"""testing/test_bubble.py — T-122: BubbleCollector tests."""
import os
import sys
import threading
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _msg(text, **kw):
    from agent.bubble import BubbleMessage
    return BubbleMessage(text=text, sent_at=time.time(), **kw)


def _make_collector(dispatched, **kwargs):
    """Create a collector that appends bubbles to a list. Returns (collector, list)."""
    from agent.bubble import BubbleCollector

    def dispatch(bubble):
        dispatched.append(bubble)

    return BubbleCollector(dispatch, **kwargs)


# ── happy path: three rapid messages → one bubble ─────────────────────────────

def test_three_rapid_messages_one_bubble():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=200)

    for txt in ["Hey", "Supppp", "Hey"]:
        c.add("chat1", _msg(txt))
        time.sleep(0.05)

    # Wait for idle timer to fire
    time.sleep(0.5)
    c.stop()

    assert len(dispatched) == 1
    b = dispatched[0]
    assert len(b.messages) == 3
    assert b.joined_text() == "Hey\nSupppp\nHey"
    assert b.closed_reason == "idle"


# ── messages with gap > idle → two bubbles ────────────────────────────────────

def test_idle_timeout_flushes_bubble():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=150)

    c.add("chat1", _msg("first"))
    time.sleep(0.3)  # > idle_ms
    c.add("chat1", _msg("second"))
    time.sleep(0.3)
    c.stop()

    assert len(dispatched) == 2
    assert dispatched[0].messages[0].text == "first"
    assert dispatched[1].messages[0].text == "second"


# ── message cap force-flush ──────────────────────────────────────────────────

def test_max_messages_force_flush():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=10000, max_messages=3)

    for i in range(3):
        c.add("chat1", _msg(f"m{i}"))
    # Should have flushed immediately on hitting the 3rd
    time.sleep(0.1)
    c.stop()

    assert len(dispatched) == 1
    assert dispatched[0].closed_reason == "max_messages"
    assert len(dispatched[0].messages) == 3


# ── max bubble ms ─────────────────────────────────────────────────────────────

def test_max_duration_force_flush():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=10000, max_bubble_ms=200, max_messages=100)

    c.add("chat1", _msg("start"))
    time.sleep(0.3)  # exceed max_bubble_ms
    c.add("chat1", _msg("over"))  # this triggers the max_ms check
    time.sleep(0.1)
    c.stop()

    assert len(dispatched) >= 1
    # First bubble (or only) should be closed due to max_ms
    assert any(b.closed_reason == "max_ms" for b in dispatched)


# ── media closes immediately ──────────────────────────────────────────────────

def test_media_message_immediate_flush():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=5000)

    c.add("chat1", _msg("look at this"))
    c.add("chat1", _msg("", media_type="photo", media_path="/tmp/p.jpg"))
    time.sleep(0.1)
    c.stop()

    assert len(dispatched) == 1
    assert dispatched[0].closed_reason == "media"
    assert dispatched[0].has_media


# ── reply_to_message preserved ───────────────────────────────────────────────

def test_reply_to_message_preserved():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=150)

    c.add("chat1", _msg("reply!", reply_to_text="earlier Pi message"))
    time.sleep(0.3)
    c.stop()

    assert len(dispatched) == 1
    assert dispatched[0].reply_targets == ["earlier Pi message"]


# ── concurrent chats isolated ─────────────────────────────────────────────────

def test_concurrent_chats_isolated_bubbles():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=150)

    c.add("chatA", _msg("A1"))
    c.add("chatB", _msg("B1"))
    c.add("chatA", _msg("A2"))
    c.add("chatB", _msg("B2"))

    time.sleep(0.3)
    c.stop()

    chats = {b.chat_id for b in dispatched}
    assert chats == {"chatA", "chatB"}
    for b in dispatched:
        # Each bubble should have only its own chat's messages
        assert all(m.text.startswith(b.chat_id[-1]) for m in b.messages)


# ── lifecycle flush ──────────────────────────────────────────────────────────

def test_flush_force_closes_open_bubble():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=10000)

    c.add("chat1", _msg("hi"))
    c.add("chat1", _msg("there"))
    flushed = c.flush("chat1", reason="lifecycle")
    time.sleep(0.1)
    c.stop()

    assert flushed is not None
    assert flushed.closed_reason == "lifecycle"
    assert len(dispatched) == 1
    assert dispatched[0].closed_reason == "lifecycle"


def test_flush_on_no_open_bubble_returns_none():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=150)
    result = c.flush("chat_with_nothing")
    c.stop()
    assert result is None


# ── new message during flush starts fresh bubble ─────────────────────────────

def test_new_message_after_flush_starts_fresh_bubble():
    dispatched = []
    c = _make_collector(dispatched, idle_ms=150)

    c.add("chat1", _msg("first"))
    c.flush("chat1", reason="lifecycle")
    # Add another message after flush — should start a new bubble
    time.sleep(0.05)
    c.add("chat1", _msg("second"))
    time.sleep(0.3)
    c.stop()

    assert len(dispatched) == 2
    assert dispatched[0].messages[0].text == "first"
    assert dispatched[1].messages[0].text == "second"


# ── dispatch exception does not crash consumer ────────────────────────────────

def test_dispatch_exception_swallowed():
    from agent.bubble import BubbleCollector, BubbleMessage

    def bad_dispatch(b):
        raise RuntimeError("boom")

    c = BubbleCollector(bad_dispatch, idle_ms=150)
    c.add("chat1", _msg("crash test"))
    time.sleep(0.3)
    c.add("chat1", _msg("still alive"))
    time.sleep(0.3)
    c.stop()
    # No assertion needed — test passes if it didn't deadlock


# ── env var override ──────────────────────────────────────────────────────────

def test_env_var_overrides_idle_ms():
    from agent.bubble import BubbleCollector
    with patch.dict(os.environ, {"PI_BUBBLE_IDLE_MS": "100"}):
        c = BubbleCollector(lambda b: None)
        assert c.idle_ms == 100
        c.stop()


# ── joined_text format ────────────────────────────────────────────────────────

def test_joined_text_default_newline():
    from agent.bubble import Bubble, BubbleMessage
    b = Bubble(chat_id="x")
    b.messages = [
        BubbleMessage(text="a", sent_at=0),
        BubbleMessage(text="b", sent_at=0),
        BubbleMessage(text="c", sent_at=0),
    ]
    assert b.joined_text() == "a\nb\nc"
