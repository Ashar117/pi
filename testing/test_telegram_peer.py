"""Tests for T-188: Telegram peer — per-chat conversation isolation."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_agent(tmp_path=None):
    ag = MagicMock()
    ag.conversation_id = "desktop-conv-1"
    ag.mode = "root"
    ag.messages = [{"role": "user", "content": "desktop msg"}]
    ag.process_input = MagicMock(return_value="Pi response")
    mem = MagicMock()
    mem.load_conversation_turns.return_value = []
    mem.create_conversation.return_value = None
    ag.memory = mem
    return ag


def _make_tt(agent):
    from tools.tools_telegram import TelegramTools
    tt = TelegramTools.__new__(TelegramTools)
    tt._agent = agent
    tt._on_message = None
    tt._bot = MagicMock()
    tt._conv_cache = {}
    return tt


def _fake_msg(chat_id=12345, text="hello"):
    msg = MagicMock()
    msg.chat.id = chat_id
    msg.text = text
    return msg


# ── _process_as_telegram_peer ─────────────────────────────────────────────────

def test_telegram_peer_uses_telegram_conversation_id():
    """process_input is called with agent.conversation_id set to telegram:<chat_id>."""
    ag = _make_agent()
    tt = _make_tt(ag)

    captured_conv_id = []

    def _capture_input(text):
        captured_conv_id.append(ag.conversation_id)
        return "ok"

    ag.process_input.side_effect = _capture_input
    tt._process_text("hello", chat_id=12345)

    assert captured_conv_id[0] == "telegram:12345"


def test_telegram_peer_restores_desktop_context_after_turn():
    """Desktop conversation_id and messages are restored after Telegram turn."""
    ag = _make_agent()
    tt = _make_tt(ag)

    original_conv = ag.conversation_id
    original_messages = list(ag.messages)

    tt._process_text("tg message", chat_id=99)

    assert ag.conversation_id == original_conv
    assert ag.messages == original_messages


def test_telegram_peer_restores_context_on_process_input_error():
    """Context is restored even if process_input raises."""
    ag = _make_agent()
    tt = _make_tt(ag)

    original_conv = ag.conversation_id
    ag.process_input.side_effect = RuntimeError("fail")

    try:
        tt._process_text("boom", chat_id=42)
    except RuntimeError:
        pass

    assert ag.conversation_id == original_conv


def test_telegram_peer_loads_stored_turns():
    """Stored turns for the Telegram conversation are loaded before processing."""
    ag = _make_agent()
    ag.memory.load_conversation_turns.return_value = [
        {"role": "user", "content": "earlier tg msg"},
        {"role": "assistant", "content": "earlier reply"},
    ]
    tt = _make_tt(ag)

    tt._process_text("follow-up", chat_id=42)

    ag.memory.load_conversation_turns.assert_called_with("telegram:42", max_turns=40)


def test_telegram_peer_creates_conversation_if_new():
    """T-244: load_conversation_turns is called on first contact for a new chat_id."""
    ag = _make_agent()
    tt = _make_tt(ag)

    tt._process_text("first msg", chat_id=77)

    ag.memory.load_conversation_turns.assert_called_once_with("telegram:77", max_turns=40)


def test_desktop_turn_unaffected_by_telegram_turn():
    """Desktop conversation_id unchanged after Telegram turn is processed."""
    ag = _make_agent()
    tt = _make_tt(ag)

    # Simulate desktop processing
    ag.process_input("desktop question")
    desktop_conv = ag.conversation_id  # still "desktop-conv-1"

    # Telegram message comes in
    tt._process_text("telegram question", chat_id=999)

    assert ag.conversation_id == desktop_conv


def test_process_text_no_chat_id_uses_direct_process_input():
    """Without chat_id, falls back to direct process_input (old behavior)."""
    ag = _make_agent()
    tt = _make_tt(ag)

    result = tt._process_text("plain text")

    ag.process_input.assert_called_once_with("plain text")
    assert result == "Pi response"


def test_different_chat_ids_get_different_conversation_ids():
    """Two different Telegram users get distinct conversation IDs."""
    ag = _make_agent()
    tt = _make_tt(ag)

    conv_ids_seen = []

    def _capture(text):
        conv_ids_seen.append(ag.conversation_id)
        return "ok"

    ag.process_input.side_effect = _capture

    tt._process_text("msg from user A", chat_id=111)
    tt._process_text("msg from user B", chat_id=222)

    assert "telegram:111" in conv_ids_seen
    assert "telegram:222" in conv_ids_seen
    assert conv_ids_seen[0] != conv_ids_seen[1]


# ── on_message override (test mode) ──────────────────────────────────────────

def test_on_message_override_bypasses_peer_routing():
    """When _on_message is set (test mode), chat_id is ignored."""
    ag = _make_agent()
    tt = _make_tt(ag)
    tt._on_message = lambda text: f"mocked: {text}"

    result = tt._process_text("hello", chat_id=42)

    assert result == "mocked: hello"
    ag.process_input.assert_not_called()
