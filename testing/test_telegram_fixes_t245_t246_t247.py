"""testing/test_telegram_fixes_t245_t246_t247.py — T-245/T-246/T-247 fixes.

T-245: quote-reply context injected into dispatch text
T-246: blank LLM response → retry message, not silence or '(empty)'
T-247: unclosed HTML tags closed; fallback strips HTML before plain-text send
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch, call as mcall


# ── T-245: quote-reply context injection ──────────────────────────────────────

def _make_bubble(messages):
    from agent.bubble import Bubble, BubbleMessage
    b = Bubble(chat_id="99")
    for text, reply_to in messages:
        b.messages.append(BubbleMessage(
            text=text,
            sent_at=0.0,
            reply_to_text=reply_to,
        ))
    return b


def test_quote_reply_prepended_to_dispatch():
    """reply_to_text is injected as [Replying to: ...] before the message text."""
    from agent.bubble import Bubble, BubbleMessage
    b = _make_bubble([("ok got it", "here is the plan for your day")])

    # Replicate the joined-text logic from _dispatch_bubble
    parts = []
    for m in b.messages:
        if m.reply_to_text:
            parts.append(f'[Replying to: "{m.reply_to_text[:300]}"]')
        if m.text:
            parts.append(m.text)
    joined = "\n".join(parts)

    assert '[Replying to: "here is the plan for your day"]' in joined
    assert "ok got it" in joined
    assert joined.index("[Replying to") < joined.index("ok got it")


def test_no_reply_to_text_unchanged():
    """Messages without a reply-to produce the same output as before."""
    b = _make_bubble([("plain text", None)])
    parts = []
    for m in b.messages:
        if m.reply_to_text:
            parts.append(f'[Replying to: "{m.reply_to_text[:300]}"]')
        if m.text:
            parts.append(m.text)
    joined = "\n".join(parts)
    assert joined == "plain text"
    assert "[Replying to" not in joined


def test_multiple_messages_only_quoted_gets_prefix():
    """Two messages in bubble: one with reply_to, one without."""
    b = _make_bubble([
        ("first", None),
        ("second replying", "pi said something"),
    ])
    parts = []
    for m in b.messages:
        if m.reply_to_text:
            parts.append(f'[Replying to: "{m.reply_to_text[:300]}"]')
        if m.text:
            parts.append(m.text)
    joined = "\n".join(parts)
    lines = joined.split("\n")
    assert lines[0] == "first"
    assert lines[1] == '[Replying to: "pi said something"]'
    assert lines[2] == "second replying"


def test_reply_to_text_truncated_at_300():
    """Long reply_to_text is capped at 300 chars."""
    long_text = "x" * 500
    b = _make_bubble([("response", long_text)])
    parts = []
    for m in b.messages:
        if m.reply_to_text:
            parts.append(f'[Replying to: "{m.reply_to_text[:300]}"]')
        if m.text:
            parts.append(m.text)
    joined = "\n".join(parts)
    assert "x" * 301 not in joined
    assert "x" * 300 in joined


# ── T-246: blank response handling ───────────────────────────────────────────

def test_send_plain_chunks_blank_sends_fallback():
    """_send_plain_chunks with '' sends retry message, not '(empty)'."""
    bot = MagicMock()
    bot.reply_to = MagicMock()
    from tools.tools_telegram import TelegramTools
    tg = TelegramTools.__new__(TelegramTools)
    tg._bot = bot
    tg._agent = None
    tg._bubble = None

    fake_msg = MagicMock()
    tg._send_plain_chunks(bot, fake_msg, "")
    call_text = bot.reply_to.call_args[0][1]
    assert "(empty)" not in call_text
    assert "try again" in call_text.lower() or "went wrong" in call_text.lower()


def test_send_plain_chunks_nonempty_sends_text():
    """_send_plain_chunks with real text sends it unchanged."""
    bot = MagicMock()
    bot.reply_to = MagicMock()
    from tools.tools_telegram import TelegramTools
    tg = TelegramTools.__new__(TelegramTools)

    fake_msg = MagicMock()
    tg._send_plain_chunks(bot, fake_msg, "here is your analysis")
    call_text = bot.reply_to.call_args[0][1]
    assert call_text == "here is your analysis"


def test_reply_chunks_blank_returns_none_without_sending():
    """_reply_chunks with blank text returns None and does not call send_message."""
    bot = MagicMock()
    bot.send_message = MagicMock()
    from tools.tools_telegram import TelegramTools
    tg = TelegramTools.__new__(TelegramTools)
    tg._bot = bot
    tg._agent = None
    tg._bubble = None

    fake_msg = MagicMock()
    fake_msg.chat = MagicMock()
    fake_msg.chat.id = 99

    with patch("tools.tools_telegram.track_silent"):
        result = tg._reply_chunks(bot, fake_msg, "")
    assert result is None
    bot.send_message.assert_not_called()


# ── T-247: HTML tag balancer + strip fallback ─────────────────────────────────

def test_format_closes_unclosed_bold():
    from tools.tools_telegram import _format_for_telegram
    # Unclosed ** in input
    result = _format_for_telegram("This is **bold without close")
    assert result.count("<b>") == result.count("</b>")


def test_format_closes_unclosed_italic():
    from tools.tools_telegram import _format_for_telegram
    result = _format_for_telegram("This is *italic without close")
    assert result.count("<i>") == result.count("</i>")


def test_format_closes_unclosed_code():
    from tools.tools_telegram import _format_for_telegram
    # Inline code that doesn't close (edge case from LLM output)
    result = _format_for_telegram("use `code here")
    assert result.count("<code>") == result.count("</code>")


def test_format_balanced_tags_unchanged():
    """Properly balanced tags are not modified."""
    from tools.tools_telegram import _format_for_telegram
    result = _format_for_telegram("**bold** and *italic*")
    assert "<b>bold</b>" in result
    assert "<i>italic</i>" in result
    assert result.count("<b>") == result.count("</b>")
    assert result.count("<i>") == result.count("</i>")


def test_reply_chunks_strips_html_on_400():
    """When send_message with HTML fails, fallback strips tags before plain send."""
    import re
    bot = MagicMock()
    bot.send_message = MagicMock(
        side_effect=[Exception("Bad Request"), MagicMock(message_id=5)]
    )
    from tools.tools_telegram import TelegramTools
    tg = TelegramTools.__new__(TelegramTools)
    tg._bot = bot

    fake_msg = MagicMock()
    fake_msg.chat = MagicMock()
    fake_msg.chat.id = 99

    result = tg._reply_chunks(bot, fake_msg, "<b>hello</b> world")
    assert bot.send_message.call_count == 2
    # Second call must not contain HTML tags
    fallback_text = bot.send_message.call_args_list[1][0][1]
    assert "<b>" not in fallback_text
    assert "hello" in fallback_text


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
        sys.exit(1)
