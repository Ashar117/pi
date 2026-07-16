"""T-236: Pi uses plain send_message for normal turns instead of reply_to
(which quote-threads every reply back at the user, making Pi feel robotic).

reply_to is kept only for error reporting in _dispatch_bubble.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_mock_message(chat_id=12345):
    msg = types.SimpleNamespace(
        chat=types.SimpleNamespace(id=chat_id),
        message_id=1,
        text="hello",
    )
    return msg


def test_reply_chunks_uses_send_message_not_reply_to():
    """_reply_chunks must call bot.send_message, not bot.reply_to."""
    from tools.tools_telegram import TelegramTools

    calls = {"send_message": 0, "reply_to": 0}

    class FakeBot:
        def send_message(self, chat_id, text, **k):
            calls["send_message"] += 1

        def reply_to(self, msg, text, **k):
            calls["reply_to"] += 1

    tools = TelegramTools.__new__(TelegramTools)
    bot = FakeBot()
    msg = _make_mock_message()

    tools._reply_chunks(bot, msg, "Hello from Pi")

    assert calls["send_message"] >= 1, "send_message must be called for normal replies"
    assert calls["reply_to"] == 0, "reply_to must NOT be used for normal replies (T-236)"


def test_reply_chunks_chunked_all_plain_send():
    """Multi-chunk replies all use send_message, none use reply_to."""
    from tools.tools_telegram import TelegramTools

    send_calls = []
    reply_calls = []

    class FakeBot:
        def send_message(self, chat_id, text, **k):
            send_calls.append(text)

        def reply_to(self, msg, text, **k):
            reply_calls.append(text)

    tools = TelegramTools.__new__(TelegramTools)
    bot = FakeBot()
    msg = _make_mock_message()

    # Text longer than 4096 chars produces multiple chunks
    long_text = "x" * 9000
    tools._reply_chunks(bot, msg, long_text)

    assert len(send_calls) == 3  # 9000 / 4096 = 3 chunks
    assert len(reply_calls) == 0
