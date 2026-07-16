"""testing/test_telegram_formatting.py — T-219: HTML formatting for Telegram.

Tests that _format_for_telegram converts Pi's markdown to safe Telegram HTML
and that stray special chars never cause silent message drops.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_telegram import _format_for_telegram


def test_html_escape_ampersand():
    out = _format_for_telegram("A & B")
    assert "&amp;" in out, f"Expected &amp; in: {out}"
    assert "&" not in out.replace("&amp;", "").replace("&lt;", "").replace("&gt;", ""), \
        f"Unescaped & remains: {out}"


def test_html_escape_angle_brackets():
    out = _format_for_telegram("a < b > c")
    assert "&lt;" in out
    assert "&gt;" in out
    assert "<b>" not in out or out.count("<b>") == 0  # no stray <b> from input


def test_stray_markdown_star_survives():
    out = _format_for_telegram("price is *not* set")
    # stray unbalanced * should not crash and the text should appear
    assert "not" in out


def test_stray_underscore_survives():
    out = _format_for_telegram("file_name_here is ok")
    assert "file" in out and "name" in out


def test_bold_conversion():
    out = _format_for_telegram("This is **bold** text")
    assert "<b>bold</b>" in out, f"Expected bold tag: {out}"


def test_italic_conversion():
    out = _format_for_telegram("This is _italic_ text")
    assert "<i>italic</i>" in out, f"Expected italic tag: {out}"


def test_inline_code_conversion():
    out = _format_for_telegram("Use `git status` here")
    assert "<code>git status</code>" in out, f"Expected code tag: {out}"


def test_code_block_conversion():
    text = "Here:\n```python\nprint('hello')\n```\nDone."
    out = _format_for_telegram(text)
    assert "<pre>" in out
    assert "print" in out
    assert "```" not in out


def test_code_block_escapes_content():
    text = "```\nfoo < bar & baz\n```"
    out = _format_for_telegram(text)
    assert "&lt;" in out
    assert "&amp;" in out


def test_link_conversion():
    out = _format_for_telegram("See [Google](https://google.com) now")
    assert '<a href="https://google.com">Google</a>' in out, f"Expected link: {out}"


def test_no_double_escaping():
    out = _format_for_telegram("a & b < c")
    assert "&amp;amp;" not in out, "Double-escaped &"
    assert "&amp;lt;" not in out, "Double-escaped <"


def test_plain_text_unchanged_modulo_escaping():
    text = "Just a normal sentence with no markdown."
    out = _format_for_telegram(text)
    assert out == text


def test_chunk_boundary_safety():
    """_format_for_telegram returns a string; chunking at 4096 does not split mid-tag
    for normal-length output. Here we just confirm no truncation on a normal reply."""
    text = "**Hello** world! This is a `code` sample."
    out = _format_for_telegram(text)
    # All tags should be closed
    assert out.count("<b>") == out.count("</b>")
    assert out.count("<code>") == out.count("</code>")


def test_reply_chunks_uses_html_mode():
    """_reply_chunks calls bot.send_message with parse_mode='HTML'."""
    from unittest.mock import MagicMock, patch
    import tools.tools_telegram as tg

    bot = MagicMock()
    message = MagicMock()
    message.chat.id = 12345

    dummy_agent = MagicMock()
    tt = tg.TelegramTools.__new__(tg.TelegramTools)
    tt._bot = bot

    tt._reply_chunks(bot, message, "Hello **world**")

    assert bot.send_message.called
    call_kwargs = bot.send_message.call_args
    # parse_mode should be HTML or None (fallback), but primary call is HTML
    args, kwargs = call_kwargs
    assert kwargs.get("parse_mode") == "HTML" or args[2:3] == ("HTML",)


def test_fallback_on_html_error():
    """If HTML send raises, _reply_chunks retries with parse_mode=None."""
    from unittest.mock import MagicMock, call
    import tools.tools_telegram as tg

    bot = MagicMock()
    bot.send_message.side_effect = [Exception("Bad HTML"), None]

    message = MagicMock()
    message.chat.id = 12345

    tt = tg.TelegramTools.__new__(tg.TelegramTools)
    tt._bot = bot

    tt._reply_chunks(bot, message, "test text")

    # Should have been called twice: first with HTML (fails), then plain
    assert bot.send_message.call_count == 2
    second_call_args, second_call_kwargs = bot.send_message.call_args
    assert second_call_kwargs.get("parse_mode") is None


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
