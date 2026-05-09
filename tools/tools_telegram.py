"""
tools/tools_telegram.py — Telegram bot integration for Pi.

Allows Ash to message Pi through Telegram and get responses.
Uses pyTelegramBotAPI (telebot) — synchronous, no async required.

Setup:
    1. Create bot: @BotFather → /newbot → copy token
    2. Add TELEGRAM_BOT_TOKEN=<token> to .env
    3. Get your chat ID: message @userinfobot
    4. Add TELEGRAM_CHAT_ID=<id> to .env (optional — locks bot to one user)

Usage (standalone bot loop):
    from tools.tools_telegram import TelegramTools
    bot = TelegramTools(agent)
    bot.start_polling()      # blocking loop — run in a thread

Usage (send-only, from within a session):
    from tools.tools_telegram import send_message
    send_message("Done! Check the report.")
"""

import os
import threading
import logging
from pathlib import Path
from typing import Optional, Callable, Dict

logger = logging.getLogger(__name__)

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # "" = allow any


def _get_bot():
    """Return a telebot.TeleBot instance or None if token is missing."""
    if not _TOKEN:
        return None
    try:
        import telebot
        return telebot.TeleBot(_TOKEN, parse_mode="Markdown")
    except ImportError:
        return None


def send_message(text: str, chat_id: Optional[str] = None) -> Dict:
    """Send a message to Ash's Telegram.

    Args:
        text:    Message text (Markdown supported).
        chat_id: Override chat — defaults to TELEGRAM_CHAT_ID env var.

    Returns:
        {"success": bool, "error": str (if failed)}
    """
    target = chat_id or _ALLOWED_CHAT_ID
    if not target:
        return {"success": False, "error": "No chat_id — set TELEGRAM_CHAT_ID in .env"}

    bot = _get_bot()
    if bot is None:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN not set or pyTelegramBotAPI not installed"}

    try:
        bot.send_message(int(target), text)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


class TelegramTools:
    """Full Telegram bot that proxies messages through Pi's process_input()."""

    def __init__(self, agent, on_message: Optional[Callable] = None):
        """
        Args:
            agent:      PiAgent instance — messages are passed through process_input().
            on_message: Optional callback(text: str) -> str for testing without a live agent.
        """
        self._agent = agent
        self._on_message = on_message
        self._bot = _get_bot()
        self._polling_thread: Optional[threading.Thread] = None
        self._running = False

    def is_available(self) -> bool:
        return self._bot is not None

    def send(self, text: str, chat_id: Optional[str] = None) -> Dict:
        return send_message(text, chat_id)

    def start_polling(self, block: bool = True) -> None:
        """Start the Telegram polling loop.

        Args:
            block: If True, blocks the calling thread. If False, runs in a daemon thread.
        """
        if self._bot is None:
            logger.warning("TelegramTools: bot unavailable (missing token or library)")
            return

        self._running = True
        self._register_handlers()

        if block:
            self._bot.infinity_polling(timeout=10, long_polling_timeout=5)
        else:
            self._polling_thread = threading.Thread(
                target=self._bot.infinity_polling,
                kwargs={"timeout": 10, "long_polling_timeout": 5},
                daemon=True,
                name="pi-telegram-bot",
            )
            self._polling_thread.start()

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._bot:
            try:
                self._bot.stop_polling()
            except Exception:
                pass

    def _register_handlers(self) -> None:
        """Wire message handlers onto the bot."""
        bot = self._bot

        @bot.message_handler(commands=["start", "help"])
        def handle_start(message):
            cid = str(message.chat.id)
            if _ALLOWED_CHAT_ID and cid != _ALLOWED_CHAT_ID:
                bot.reply_to(message, "Unauthorized.")
                return
            bot.reply_to(message, "Pi is online. Send me a message.")

        @bot.message_handler(content_types=["text"])
        def handle_text(message):
            cid = str(message.chat.id)
            if _ALLOWED_CHAT_ID and cid != _ALLOWED_CHAT_ID:
                bot.reply_to(message, "Unauthorized.")
                return

            user_text = message.text.strip()
            if not user_text:
                return

            # Show typing indicator
            bot.send_chat_action(message.chat.id, "typing")

            try:
                if self._on_message:
                    reply = self._on_message(user_text)
                elif self._agent is not None:
                    reply = self._agent.process_input(user_text)
                else:
                    reply = "(no agent)"

                # Telegram max message length is 4096 chars — chunk if needed
                for chunk in _chunk_text(reply, 4096):
                    bot.reply_to(message, chunk)

            except Exception as e:
                logger.exception("Telegram handler error")
                bot.reply_to(message, f"[Pi error] {e}")


def _chunk_text(text: str, max_len: int):
    """Yield successive chunks of text up to max_len characters."""
    for i in range(0, len(text), max_len):
        yield text[i : i + max_len]
