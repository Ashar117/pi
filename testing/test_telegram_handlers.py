"""testing/test_telegram_handlers.py — T-261: real handler-level Telegram tests.

Unlike existing telegram tests (which replicate handler *logic* in the test
itself), these drive the actual registered handler closures via a fake bot
object — closing the gap that let the T-244..T-248 bug cluster ship blind.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeBot:
    """Captures registered handlers + sent messages. No network."""

    def __init__(self):
        self.message_handlers = []   # [(kwargs, fn), ...]
        self.callback_handlers = []  # [(kwargs, fn), ...]
        self.sent = []               # [(chat_id, text, kwargs), ...]
        self.answered = []           # [(call_id, text), ...] from answer_callback_query
        self._send_message_side_effects = []  # queued exceptions/results

    def message_handler(self, **kwargs):
        def deco(fn):
            self.message_handlers.append((kwargs, fn))
            return fn
        return deco

    def callback_query_handler(self, **kwargs):
        def deco(fn):
            self.callback_handlers.append((kwargs, fn))
            return fn
        return deco

    def send_message(self, chat_id, text, **kwargs):
        if self._send_message_side_effects:
            effect = self._send_message_side_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
        self.sent.append((chat_id, text, kwargs))
        return type("Msg", (), {"message_id": len(self.sent)})()

    def reply_to(self, message, text, **kwargs):
        return self.send_message(message.chat.id, text, **kwargs)

    def send_chat_action(self, chat_id, action):
        pass

    def answer_callback_query(self, call_id, text=None):
        self.answered.append((call_id, text))

    def get_me(self):
        return type("Me", (), {"id": 999, "username": "pibot"})()

    def delete_message(self, chat_id, message_id):
        pass


def _make_tools(agent=None):
    from tools.tools_telegram import TelegramTools
    tt = TelegramTools(agent=agent or MagicMock(), use_bubble=False)
    fake_bot = _FakeBot()
    tt._bot = fake_bot
    tt._register_handlers()
    return tt, fake_bot


def _get_handler(fake_bot, handlers_attr, **match):
    handlers = getattr(fake_bot, handlers_attr)
    for kwargs, fn in handlers:
        if all(kwargs.get(k) == v for k, v in match.items()):
            return fn
    raise AssertionError(f"No handler registered matching {match} in {handlers_attr}")


def _make_message(text, chat_id=100, chat_type="private"):
    return type("Message", (), {
        "text": text,
        "chat": type("Chat", (), {"id": chat_id, "type": chat_type})(),
        "date": 0.0,
        "message_id": 1,
        "reply_to_message": None,
    })()


# ── T-247 regression: malformed HTML falls back to stripped plain text ───────

def test_handle_text_falls_back_to_plain_on_html_400(monkeypatch):
    tt, fake_bot = _make_tools()
    monkeypatch.setattr(tt, "_process_text", lambda text, chat_id=None: "reply with <token> in it")

    handle_text = _get_handler(fake_bot, "message_handlers", content_types=["text"])
    fake_bot._send_message_side_effects = [RuntimeError("400 Bad Request: unsupported tag")]

    handle_text(_make_message("hello"))

    assert len(fake_bot.sent) == 1
    chat_id, text, kwargs = fake_bot.sent[0]
    assert kwargs.get("parse_mode") is None
    assert "<token>" not in text  # T-247: HTML stripped before plain-text fallback


def test_handle_text_sends_html_when_it_succeeds(monkeypatch):
    tt, fake_bot = _make_tools()
    monkeypatch.setattr(tt, "_process_text", lambda text, chat_id=None: "plain reply")

    handle_text = _get_handler(fake_bot, "message_handlers", content_types=["text"])
    handle_text(_make_message("hello"))

    assert len(fake_bot.sent) == 1
    _, text, kwargs = fake_bot.sent[0]
    assert kwargs.get("parse_mode") == "HTML"
    assert "plain reply" in text


# ── T-220 regression: button tap reaches _process_text as a normal turn ─────

def test_handle_callback_routes_to_process_text(monkeypatch):
    tt, fake_bot = _make_tools()
    calls = []

    def fake_process_text(text, chat_id=None):
        calls.append((text, chat_id))
        return "acknowledged"

    monkeypatch.setattr(tt, "_process_text", fake_process_text)
    handle_callback = _get_handler(fake_bot, "callback_handlers")

    fake_call = type("Call", (), {
        "id": "cb1",
        "data": "Yes please",
        "message": type("Msg", (), {"chat": type("Chat", (), {"id": 200})()})(),
    })()
    handle_callback(fake_call)

    assert calls == [("[Button selected: Yes please]", 200)]
    assert fake_bot.sent and "acknowledged" in fake_bot.sent[0][1]


# ── T-278: callback taps run the same auth gate as handle_text ───────────────

def _make_call(chat_id):
    return type("Call", (), {
        "id": "cb-auth",
        "data": "emailtriage:reply:abc123",
        "message": type("Msg", (), {"chat": type("Chat", (), {"id": chat_id})()})(),
    })()


def test_handle_callback_blocks_unauthorized_chat(monkeypatch):
    tt, fake_bot = _make_tools()
    monkeypatch.setattr("tools.tools_telegram._ALLOWED_CHAT_ID", "100")
    monkeypatch.setattr(tt, "_resolve_profile", lambda chat_id: None)
    process_calls = []
    monkeypatch.setattr(tt, "_process_text",
                        lambda text, chat_id=None: process_calls.append(text) or "x")

    handle_callback = _get_handler(fake_bot, "callback_handlers")
    handle_callback(_make_call(chat_id=200))  # not the allowed chat, no profile

    assert process_calls == [], "unauthorized tap must not trigger an agent turn"
    assert fake_bot.sent == [], "unauthorized tap must not send any message"
    assert ("cb-auth", "Unauthorized") in fake_bot.answered


def test_handle_callback_allows_authorized_chat(monkeypatch):
    tt, fake_bot = _make_tools()
    monkeypatch.setattr("tools.tools_telegram._ALLOWED_CHAT_ID", "100")
    process_calls = []
    monkeypatch.setattr(tt, "_process_text",
                        lambda text, chat_id=None: process_calls.append(text) or "drafted")

    handle_callback = _get_handler(fake_bot, "callback_handlers")
    handle_callback(_make_call(chat_id=100))  # the allowed chat

    assert len(process_calls) == 1
    assert "gmail_read" in process_calls[0]  # triage instruction routed as the turn
