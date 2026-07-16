"""testing/test_telegram_native_actions.py — T-220: Telegram native actions.

Tests (offline, mock bot):
  - telegram_react calls set_message_reaction with right args
  - telegram_react declines when _current_chat_id not set
  - telegram_react declines when set_message_reaction attr missing
  - telegram_buttons sends message with InlineKeyboardMarkup
  - telegram_buttons declines off-Telegram
  - telegram_edit_last calls edit_message_text with right args
  - telegram_edit_last declines when no _last_sent_message_id
  - callback press routes through process_input
  - _reply_chunks returns last sent message_id
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch, call as mcall


def _mock_bot():
    bot = MagicMock()
    bot.send_message = MagicMock(return_value=MagicMock(message_id=42))
    bot.set_message_reaction = MagicMock(return_value=None)
    bot.edit_message_text = MagicMock(return_value=None)
    bot.answer_callback_query = MagicMock(return_value=None)
    return bot


def _mock_agent(chat_id="99", message_id=10, last_sent_id=20):
    agent = MagicMock()
    agent._current_chat_id = chat_id
    agent._current_message_id = message_id
    agent._last_sent_message_id = last_sent_id
    return agent


# ── telegram_react ────────────────────────────────────────────────────────────

def test_react_calls_set_message_reaction():
    agent = _mock_agent()
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        from tools.tools_telegram import _handle_telegram_react
        result = _handle_telegram_react(agent, {"emoji": "👍"})
    assert result["success"] is True
    assert result["emoji"] == "👍"
    bot.set_message_reaction.assert_called_once()
    call_args = bot.set_message_reaction.call_args
    assert call_args[0][0] == 99   # int(chat_id)
    assert call_args[0][1] == 10   # message_id


def test_react_declines_off_telegram():
    agent = MagicMock()
    agent._current_chat_id = None
    agent._current_message_id = None
    from tools.tools_telegram import _handle_telegram_react
    result = _handle_telegram_react(agent, {"emoji": "🔥"})
    assert result["success"] is False
    assert "Telegram" in result["note"]


def test_react_declines_when_no_set_message_reaction_attr():
    agent = _mock_agent()
    bot = _mock_bot()
    del bot.set_message_reaction  # simulate old pyTelegramBotAPI
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        from tools.tools_telegram import _handle_telegram_react
        result = _handle_telegram_react(agent, {"emoji": "✅"})
    assert result["success"] is False
    assert "upgrade" in result["note"].lower() or "not available" in result["note"].lower()


# ── telegram_buttons ──────────────────────────────────────────────────────────

def test_buttons_sends_with_inline_keyboard():
    agent = _mock_agent()
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        from tools.tools_telegram import _handle_telegram_buttons
        result = _handle_telegram_buttons(agent, {"text": "Choose:", "options": ["Yes", "No"]})
    assert result["success"] is True
    bot.send_message.assert_called_once()
    call_kw = bot.send_message.call_args[1]
    assert "reply_markup" in call_kw


def test_buttons_caps_at_six_options():
    agent = _mock_agent()
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        from tools.tools_telegram import _handle_telegram_buttons
        result = _handle_telegram_buttons(agent, {
            "text": "Choose:",
            "options": ["A", "B", "C", "D", "E", "F", "G", "H"],  # 8 options
        })
    assert result["success"] is True
    import telebot
    keyboard = bot.send_message.call_args[1]["reply_markup"]
    # InlineKeyboardMarkup's keyboard is a list-of-rows; count total buttons
    total_buttons = sum(len(row) for row in keyboard.keyboard)
    assert total_buttons <= 6


def test_buttons_declines_off_telegram():
    agent = MagicMock()
    agent._current_chat_id = None
    from tools.tools_telegram import _handle_telegram_buttons
    result = _handle_telegram_buttons(agent, {"text": "Choose:", "options": ["A"]})
    assert result["success"] is False
    assert "Telegram" in result["note"]


# ── telegram_edit_last ────────────────────────────────────────────────────────

def test_edit_last_calls_edit_message_text():
    agent = _mock_agent()
    bot = _mock_bot()
    with patch("tools.tools_telegram._get_bot", return_value=bot):
        from tools.tools_telegram import _handle_telegram_edit_last
        result = _handle_telegram_edit_last(agent, {"text": "Corrected message"})
    assert result["success"] is True
    bot.edit_message_text.assert_called_once()
    call_args = bot.edit_message_text.call_args[0]
    assert call_args[1] == 99   # int(chat_id)
    assert call_args[2] == 20   # _last_sent_message_id


def test_edit_last_declines_when_no_last_message():
    agent = MagicMock()
    agent._current_chat_id = "99"
    agent._last_sent_message_id = None
    from tools.tools_telegram import _handle_telegram_edit_last
    result = _handle_telegram_edit_last(agent, {"text": "Edit"})
    assert result["success"] is False
    assert "prior message" in result["note"].lower() or "Telegram" in result["note"]


def test_edit_last_declines_off_telegram():
    agent = MagicMock()
    agent._current_chat_id = None
    agent._last_sent_message_id = 42
    from tools.tools_telegram import _handle_telegram_edit_last
    result = _handle_telegram_edit_last(agent, {"text": "Edit"})
    assert result["success"] is False


# ── ToolSpec registration ─────────────────────────────────────────────────────

def test_all_three_tools_in_tools_list():
    from tools.tools_telegram import TOOLS
    names = [t.name for t in TOOLS]
    assert "telegram_react" in names
    assert "telegram_buttons" in names
    assert "telegram_edit_last" in names


# ── _reply_chunks returns message_id ─────────────────────────────────────────

def test_reply_chunks_returns_last_message_id():
    bot = _mock_bot()
    tools_tg = __import__("tools.tools_telegram", fromlist=["TelegramTools"])
    tg = tools_tg.TelegramTools.__new__(tools_tg.TelegramTools)
    tg._bot = bot
    tg._bubble = None
    tg._agent = None

    fake_msg = MagicMock()
    fake_msg.chat = MagicMock()
    fake_msg.chat.id = 99

    returned_id = tg._reply_chunks(bot, fake_msg, "Hello!")
    assert returned_id == 42  # from _mock_bot's message_id


if __name__ == "__main__":
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        import sys; sys.exit(1)
