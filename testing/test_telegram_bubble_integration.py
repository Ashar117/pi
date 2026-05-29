"""testing/test_telegram_bubble_integration.py — T-122 wiring tests.

Verifies that TelegramTools routes text through BubbleCollector and that
three rapid messages produce one dispatch.
"""
import os
import sys
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_fake_message(chat_id=42, text="hi", reply_to_text=None, message_id=1):
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = "private"
    msg.text = text
    msg.date = time.time()
    msg.message_id = message_id
    if reply_to_text:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.text = reply_to_text
    else:
        msg.reply_to_message = None
    return msg


def test_telegram_tools_creates_bubble_collector():
    from tools.tools_telegram import TelegramTools
    agent = MagicMock()
    tt = TelegramTools(agent, use_bubble=True)
    assert tt._bubble is not None
    tt._bubble.stop()


def test_telegram_tools_can_disable_bubble():
    from tools.tools_telegram import TelegramTools
    agent = MagicMock()
    tt = TelegramTools(agent, use_bubble=False)
    assert tt._bubble is None


def test_dispatch_bubble_calls_process_text():
    """Three messages added via bubble → one _process_text call with joined text."""
    from tools.tools_telegram import TelegramTools
    from agent.bubble import BubbleMessage, BubbleCollector

    agent = MagicMock()
    agent.process_input.return_value = "OK"

    tt = TelegramTools(agent, use_bubble=True)
    tt._bubble.stop()
    tt._bot = MagicMock()
    tt._bubble = BubbleCollector(tt._dispatch_bubble, idle_ms=150)

    fake_msg = _make_fake_message(chat_id=42, text="Hey")

    # Mock recall + thinking so the parallel block doesn't add latency
    with patch("memory.recall.recall_referenced", return_value=[]), \
         patch("agent.thinking.normalise", return_value=None):
        for text in ["Hey", "Supppp", "Hey"]:
            tt._bubble.add("42", BubbleMessage(text=text, sent_at=time.time(), raw=fake_msg))
            time.sleep(0.03)
        time.sleep(0.6)

    tt._bubble.stop()

    assert agent.process_input.call_count == 1
    call_arg = agent.process_input.call_args[0][0]
    assert "Hey\nSupppp\nHey" in call_arg


def test_bubble_dispatch_handles_process_exception():
    """If process_input raises, the consumer thread does not crash."""
    from tools.tools_telegram import TelegramTools
    from agent.bubble import BubbleCollector, BubbleMessage

    agent = MagicMock()
    agent.process_input.side_effect = RuntimeError("boom")

    tt = TelegramTools(agent, use_bubble=True)
    tt._bubble.stop()
    tt._bot = MagicMock()
    tt._bubble = BubbleCollector(tt._dispatch_bubble, idle_ms=100)

    fake_msg = _make_fake_message(chat_id=42, text="x")
    with patch("memory.recall.recall_referenced", return_value=[]), \
         patch("agent.thinking.normalise", return_value=None):
        tt._bubble.add("42", BubbleMessage(text="x", sent_at=time.time(), raw=fake_msg))
        time.sleep(0.5)

    tt._bubble.stop()
    assert agent.process_input.call_count == 1
