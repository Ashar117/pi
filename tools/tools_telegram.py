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
import re
import tempfile
import threading
import logging
from pathlib import Path
from typing import Optional, Callable, Dict

from agent.redaction import safe_error
from agent.observability import track_silent

logger = logging.getLogger(__name__)

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # "" = allow any


def _format_for_telegram(text: str) -> str:
    """Convert Pi's markdown output to Telegram HTML (T-219).

    Only &, <, > require escaping in HTML mode — vastly simpler than MarkdownV2's
    18 special chars. Handles fenced code blocks, inline code, bold, italic, links.
    T-247: close any unclosed tags before returning so Telegram never gets a
    malformed entity (400 Bad Request).
    """
    import re
    parts = []
    for seg in re.split(r'(```(?:[a-zA-Z]*\n)?[\s\S]*?```)', text):
        if seg.startswith('```') and seg.endswith('```'):
            inner = seg[3:-3]
            if inner.startswith('\n'):
                inner = inner[1:]
            elif '\n' in inner:
                inner = inner[inner.index('\n') + 1:]
            inner = inner.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            parts.append(f'<pre>{inner}</pre>')
        else:
            seg = seg.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            seg = re.sub(r'`([^`\n]+)`', lambda m: f'<code>{m.group(1)}</code>', seg)
            seg = re.sub(r'\[([^\]]+)\]\((https?://[^\)\s]+)\)', r'<a href="\2">\1</a>', seg)
            seg = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', seg, flags=re.DOTALL)
            seg = re.sub(r'__(.+?)__', r'<b>\1</b>', seg, flags=re.DOTALL)
            seg = re.sub(r'\*([^\*\n]+)\*', r'<i>\1</i>', seg)
            seg = re.sub(r'_([^_\n]+)_', r'<i>\1</i>', seg)
            parts.append(seg)
    result = ''.join(parts)
    # T-247: balance unclosed tags — Telegram 400s on dangling opens
    for tag in ('b', 'i', 'code', 'pre'):
        opens = result.count(f'<{tag}>')
        closes = result.count(f'</{tag}>')
        if opens > closes:
            result += f'</{tag}>' * (opens - closes)
    return result


_URL_RE = re.compile(r'^(https?://\S+)$', re.IGNORECASE)

# T-258: email-triage inline buttons encode 'emailtriage:<action>:<gmail_id>'
# in callback_data (Telegram's 64-byte limit rules out embedding the subject
# line, so the button label stays short and human while this carries the
# reference). Tapping routes through the same callback_query_handler (T-220)
# as every other inline button.
_EMAIL_TRIAGE_PREFIX = "emailtriage:"


def _email_triage_instruction(chosen: str) -> Optional[str]:
    """Turn an email-triage button tap into a self-contained instruction.

    Returns None for 'ignore' or any non-triage callback — both fall back
    to the generic button-press path (ignore is handled without spending
    a turn; non-triage callbacks keep their existing behavior).
    """
    if not chosen.startswith(_EMAIL_TRIAGE_PREFIX):
        return None
    rest = chosen[len(_EMAIL_TRIAGE_PREFIX):]
    action, _, msg_id = rest.partition(":")
    if action == "reply":
        return (f"Read Gmail message {msg_id} with gmail_read, then draft a thoughtful reply "
                f"using gmail_send (this only creates a draft, it never sends automatically).")
    if action == "cal":
        return (f"Read Gmail message {msg_id} with gmail_read, then create a calendar event "
                f"for whatever it's about using calendar_create.")
    return None


def _maybe_enrich_url(text: str) -> str:
    """T-238: if message is a bare URL, return a prompt that tells Pi to fetch and summarize it."""
    stripped = text.strip()
    m = _URL_RE.match(stripped)
    if m:
        url = m.group(1)
        return (
            f"[Ash shared a link: {url}]\n"
            f"Please fetch this URL and summarize its key content. URL: {url}"
        )
    return text


def _get_bot():
    """Return a telebot.TeleBot instance or None if token is missing."""
    if not _TOKEN:
        return None
    try:
        import telebot
        return telebot.TeleBot(_TOKEN, parse_mode="HTML")
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
        bot.send_message(int(target), _format_for_telegram(text), parse_mode="HTML")
        return {"success": True}
    except Exception:
        try:
            bot.send_message(int(target), text, parse_mode=None)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


def send_buttons(text: str, button_specs: list, chat_id: Optional[str] = None) -> Dict:
    """Send a message with inline buttons outside any live conversation (T-258).

    Unlike the telegram_buttons tool (bound to the agent's "current chat"
    context mid-turn), this is for background senders — watchers — that
    have an explicit chat_id but no active turn. Tapping a button routes
    through the same callback_query_handler as every other inline button
    (T-220), so whatever callback_data is supplied here reaches
    handle_callback exactly like a live-conversation button press.

    Args:
        text:         Message text shown above the buttons.
        button_specs: List of (label, callback_data) tuples, max 6.
        chat_id:      Override chat — defaults to TELEGRAM_CHAT_ID env var.

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
        import telebot
        keyboard = telebot.types.InlineKeyboardMarkup()
        for label, callback_data in button_specs[:6]:
            keyboard.add(telebot.types.InlineKeyboardButton(
                text=str(label)[:64], callback_data=str(callback_data)[:64]))
        bot.send_message(int(target), _format_for_telegram(text),
                          reply_markup=keyboard, parse_mode="HTML")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


class TelegramTools:
    """Full Telegram bot that proxies messages through Pi's process_input()."""

    def __init__(self, agent, on_message: Optional[Callable] = None, use_bubble: bool = True):
        """
        Args:
            agent:       PiAgent instance — messages are passed through process_input().
            on_message:  Optional callback(text: str) -> str for testing without a live agent.
            use_bubble:  T-122 — if True, route text messages through BubbleCollector.
                         Default True. Set False for tests that want synchronous dispatch.
        """
        # Cached bot identity — populated lazily on first group message
        self._bot_id: Optional[int] = None
        self._bot_username: str = ""
        self._agent = agent
        self._on_message = on_message
        self._bot = _get_bot()
        self._polling_thread: Optional[threading.Thread] = None
        self._running = False

        # T-122: bubble collector groups rapid messages into one dispatch
        self._bubble = None
        if use_bubble:
            from agent.bubble import BubbleCollector
            self._bubble = BubbleCollector(self._dispatch_bubble)

        # T-244: in-memory conversation cache per conv_id so context persists
        # across turns without relying on async SQLite writes being faster than
        # the next incoming message. Keyed by "telegram:{chat_id}".
        self._conv_cache: Dict[str, list] = {}

    def is_available(self) -> bool:
        return self._bot is not None

    def send(self, text: str, chat_id: Optional[str] = None) -> Dict:
        return send_message(text, chat_id)

    def send_buttons(self, text: str, button_specs: list, chat_id: Optional[str] = None) -> Dict:
        return send_buttons(text, button_specs, chat_id)

    def start_polling(self, block: bool = True) -> None:
        """Start the Telegram polling loop.

        Args:
            block: If True, blocks the calling thread. If False, runs in a daemon thread.

        Always starts the polling thread even when Telegram is temporarily unreachable
        (VPN off, network blocked). infinity_polling has its own retry loop and will
        reconnect automatically once the network comes back — so bailing early here
        would prevent that recovery.
        """
        if self._bot is None:
            logger.warning("TelegramTools: bot unavailable (missing token or library)")
            return

        if not self._reachable():
            logger.warning("TelegramTools: api.telegram.org unreachable — starting anyway, will retry")

        self._running = True
        self._register_handlers()

        # logger_level=None silences per-retry error spam; one warning above is enough
        poll_kwargs = {"timeout": 10, "long_polling_timeout": 5, "logger_level": None}

        if block:
            self._bot.infinity_polling(**poll_kwargs)
        else:
            self._polling_thread = threading.Thread(
                target=self._bot.infinity_polling,
                kwargs=poll_kwargs,
                daemon=True,
                name="pi-telegram-bot",
            )
            self._polling_thread.start()

    @staticmethod
    def _reachable(timeout: float = 3.0) -> bool:
        """Return True if api.telegram.org is reachable within timeout seconds."""
        import socket
        try:
            socket.setdefaulttimeout(timeout)
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("api.telegram.org", 443))
            return True
        except OSError:
            return False

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._bot:
            try:
                self._bot.stop_polling()
            except Exception:
                pass

    # ── internal helpers ──────────────────────────────────────────────────────

    class _TypingHeartbeat:
        """T-217: re-sends 'typing' chat action every N seconds until stopped.

        Use as a context manager around any blocking operation so the user
        always sees Pi is working, even on long tool-chain turns.
        All Telegram API calls are best-effort — a failure here never
        crashes or delays the real reply.
        """
        def __init__(self, bot, chat_id: int, interval: float = 4.0):
            self._bot = bot
            self._chat_id = chat_id
            self._interval = interval
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, daemon=True, name="typing-heartbeat")

        def _run(self) -> None:
            while not self._stop.wait(self._interval):
                try:
                    self._bot.send_chat_action(self._chat_id, "typing")
                except Exception:
                    pass

        def __enter__(self):
            self._thread.start()
            return self

        def __exit__(self, *_):
            self._stop.set()

    def _auth(self, message) -> bool:
        """Return True if message is from Ash or a bound guest profile."""
        chat_id = str(message.chat.id)
        if not _ALLOWED_CHAT_ID or chat_id == _ALLOWED_CHAT_ID:
            return True
        return self._resolve_profile(chat_id) is not None

    def _resolve_profile(self, chat_id: str):
        """T-222: Return the Profile for a chat_id, or None if not logged in.

        Ash's TELEGRAM_CHAT_ID auto-resolves to the 'ash' profile (no /login needed).
        Other chat_ids resolve via the sticky device_bindings table.
        """
        try:
            from agent.profile import get_registry
            reg = get_registry()
            if _ALLOWED_CHAT_ID and chat_id == _ALLOWED_CHAT_ID:
                return reg.get_profile("ash")
            bound_name = reg.resolve_binding(chat_id)
            if bound_name:
                return reg.get_profile(bound_name)
        except Exception as e:
            logger.debug("_resolve_profile error (non-fatal): %s", e)
        return None

    def _download_file(self, bot, file_id: str, suffix: str) -> Optional[str]:
        """Download a Telegram file to a temp path. Returns path or None on error."""
        try:
            file_info = bot.get_file(file_id)
            data = bot.download_file(file_info.file_path)
            fd, path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return path
        except Exception as e:
            logger.error("Telegram file download failed: %s", e)
            return None

    def _dispatch_bubble(self, bubble) -> None:
        """T-122: consumer callback for closed bubbles. Joins text, dispatches once.

        Runs on the BubbleCollector consumer thread. Errors are tracked but
        never crash the consumer (BubbleCollector wraps us in try/except).
        """
        if self._bot is None:
            return
        import time as _time
        _t0 = _time.monotonic()
        # T-245: build joined text with quote-reply context inline.
        # reply_to_text is the text of the message Ash long-pressed and replied to.
        # Previously it went through a semantic L3 recall that found nothing for recent
        # turns. Inject it directly so Pi always knows which message is being quoted.
        _parts = []
        for _m in bubble.messages:
            if _m.reply_to_text:
                _parts.append(f'[Replying to: "{_m.reply_to_text[:300]}"]')
            if _m.text:
                _parts.append(_m.text)
        joined = "\n".join(_parts) if _parts else ""
        if not joined and not bubble.has_media:
            return
        last_msg = bubble.messages[-1].raw if bubble.messages else None
        try:
            self._bot.send_chat_action(int(bubble.chat_id), "typing")
        except Exception:
            pass
        # T-123 + T-124: run recall (memory) and thinking (Groq/Haiku) in parallel.
        # Both are I/O-bound on different services; max latency wins.
        import threading as _t

        recall_block_holder: dict = {"text": ""}
        thinking_block_holder: dict = {"text": ""}

        def _do_recall() -> None:
            if not bubble.reply_targets or self._agent is None or not hasattr(self._agent, "memory"):
                return
            try:
                from memory.recall import recall_referenced, format_recall_context
                from pathlib import Path as _Path
                db_path = _Path(self._agent.memory.sqlite_path)
                all_hits = []
                seen_ids = set()
                for ref in bubble.reply_targets:
                    for h in recall_referenced(ref, db_path=db_path):
                        if h["id"] not in seen_ids:
                            seen_ids.add(h["id"])
                            all_hits.append(h)
                if all_hits:
                    recall_block_holder["text"] = format_recall_context(all_hits) + "\n\n"
            except Exception:
                pass

        def _do_thinking() -> None:
            if not joined:
                return
            try:
                from agent.thinking import normalise, format_thinking_block
                result = normalise(joined)
                if result is not None:
                    thinking_block_holder["text"] = format_thinking_block(result) + "\n\n"
            except Exception:
                pass

        t_recall = _t.Thread(target=_do_recall, name="bubble-recall", daemon=True)
        t_think = _t.Thread(target=_do_thinking, name="bubble-thinking", daemon=True)
        t_recall.start(); t_think.start()
        t_recall.join(timeout=5.0); t_think.join(timeout=5.0)
        _pre_ms = int((_time.monotonic() - _t0) * 1000)
        recall_block = recall_block_holder["text"]
        thinking_block = thinking_block_holder["text"]

        # T-218: inject a lightweight channel-awareness note so Pi adapts its
        # register to Telegram (concise, no markdown headers, conversational).
        # T-220: also advertise the native-action tools so Pi knows they exist.
        _channel_note = (
            "\n\n[CHANNEL: Telegram — keep reply concise and conversational, no markdown headers. "
            "Native-action tools available (use sparingly): "
            "telegram_react(emoji) — react to the user's message; "
            "telegram_buttons(text, options) — offer quick-reply inline buttons; "
            "telegram_edit_last(text) — edit your previous message in-place.]"
        )

        # T-220: expose user's message_id so telegram_react can target the right message.
        if self._agent is not None and last_msg is not None:
            self._agent._current_message_id = getattr(last_msg, "message_id", None)

        try:
            dispatched_text = (thinking_block + recall_block + joined + _channel_note) if joined else joined
            # T-217: heartbeat keeps the 'typing' indicator alive for long turns.
            _t_llm = _time.monotonic()
            with self._TypingHeartbeat(self._bot, int(bubble.chat_id)):
                reply = self._process_text(dispatched_text, chat_id=bubble.chat_id) if dispatched_text else ""
            # T-246: if LLM returned blank, send a real retry prompt rather than silence
            if not reply or not reply.strip():
                reply = "I hit a snag — send that again?"
            _llm_ms = int((_time.monotonic() - _t_llm) * 1000)
            logger.info("[bubble] chat=%s pre=%dms llm=%dms total=%dms",
                        bubble.chat_id, _pre_ms, _llm_ms,
                        int((_time.monotonic() - _t0) * 1000))
            if last_msg is not None:
                sent_id = self._reply_chunks(self._bot, last_msg, reply)
                # T-220: track last sent message_id so telegram_edit_last can target it.
                if self._agent is not None and sent_id is not None:
                    self._agent._last_sent_message_id = sent_id
            else:
                # Fallback: plain send_message
                from tools.tools_telegram import send_message
                send_message(reply, chat_id=str(bubble.chat_id))

            # T-125a: after each bubble dispatch, run caretaker.lite() to
            # recompute any derived facts that came due. Cheap (<50ms typical).
            # Failure is swallowed — caretaker logs via track_silent.
            try:
                if self._agent is not None and hasattr(self._agent, "memory"):
                    from agent.caretaker import lite as _caretaker_lite
                    from pathlib import Path as _Path
                    db_path = _Path(self._agent.memory.sqlite_path)
                    _caretaker_lite(db_path)
            except Exception:
                pass
        except Exception as e:
            try:
                track_silent("telegram.bubble_dispatch", e, context={"chat_id": bubble.chat_id})
            except Exception:
                pass
            if last_msg is not None:
                try:
                    self._bot.reply_to(last_msg, f"[Pi error] {safe_error(e, audience='telegram')}")
                except Exception:
                    pass

    def _reply_chunks(self, bot, message, text: str) -> Optional[int]:
        # T-236: plain send_message (no quote-thread). T-219: HTML mode with fallback.
        # T-220: returns the message_id of the last sent chunk for telegram_edit_last.
        formatted = _format_for_telegram(text)
        if not formatted.strip():
            # T-246: blank response — don't send an empty bubble
            track_silent("telegram.blank_response", ValueError("process_input returned blank"))
            return None
        last_sent_id: Optional[int] = None
        for chunk in _chunk_text(formatted, 4096):
            if not chunk.strip():
                continue
            try:
                sent = bot.send_message(message.chat.id, chunk, parse_mode="HTML")
            except Exception:
                # T-247: strip HTML tags before plain-text fallback so Telegram
                # doesn't try to parse leftover < > & as entities
                clean = re.sub(r"<[^>]+>", "", chunk)
                sent = bot.send_message(message.chat.id, clean, parse_mode=None)
            if sent is not None:
                last_sent_id = getattr(sent, "message_id", None)
        return last_sent_id

    def _send_plain_chunks(self, bot, message, text: str) -> None:
        """Chunked send with parse_mode=None — used by media handlers (T-138).

        Vision/STT outputs commonly contain raw *, _, [ chars that crash the
        bot's default Markdown parser and silently drop replies. Plain text
        sidesteps the whole issue.
        """
        # T-246: replace literal "(empty)" with a real retry message
        for chunk in _chunk_text(text or "Something went wrong — please try again.", 4096):
            bot.reply_to(message, chunk, parse_mode=None)

    def _store_media_to_memory(self, analysis_text: str, kind: str,
                               filename: str = "") -> None:
        """T-239: async-best-effort write media analysis to L2 memory after analysis."""
        if self._agent is None:
            return
        try:
            mem = getattr(self._agent, "memory", None)
            if mem is None:
                return
            label = filename or kind
            mem.memory_write(
                content=f"[{kind.upper()} ANALYSIS: {label}]\n{analysis_text[:1200]}",
                importance=5,
                category="media_analysis",
            )
        except Exception as e:
            track_silent("telegram.store_media_to_memory", e, context={"kind": kind})

    def _media_route(self, message, analysis_text: str, kind: str, caption: str,
                     filename: str = "") -> str:
        """T-138: build a framed user_input from media analysis + route through process_input.

        kind is one of 'photo' | 'video' | 'document' | 'voice'. caption is the
        user's caption text (or transcript for voice). Returns Pi's response.
        """
        if kind == "voice":
            # Voice already had the transcript pass; user_text is the transcript itself.
            user_text = analysis_text
        else:
            cap_part = f" with caption {caption!r}" if caption else ""
            user_text = (
                f"[Telegram {kind} received{cap_part}.]\n"
                f"Vision/document analysis:\n{analysis_text}\n\n"
                f"Respond as Pi. If the caption is a question, answer it using the analysis above."
            )
        # T-239: persist analysis to L2 in the background (non-blocking)
        import threading as _th
        _th.Thread(
            target=self._store_media_to_memory,
            args=(analysis_text, kind, filename),
            daemon=True,
        ).start()

        chat_id = getattr(getattr(message, "chat", None), "id", None)
        return self._process_text(user_text, chat_id=chat_id)

    def _process_text(self, text: str, chat_id: Optional[int] = None) -> str:
        if self._on_message:
            return self._on_message(text)
        if self._agent is not None:
            if chat_id is not None:
                return self._process_as_telegram_peer(text, chat_id)
            return self._agent.process_input(text)
        return "(no agent)"

    def _process_as_telegram_peer(self, text: str, chat_id: int) -> str:
        """T-244: route each Telegram turn through a per-chat in-memory conversation cache.

        Replaces conversation_switch (T-188) for the primary Telegram session.
        conversation_switch is a save-restore pattern for autonomous background turns;
        applying it to the live chat caused context loss because every turn restored
        agent.messages to the pre-turn state, relying on async SQLite writes landing
        before the next reload — a race that was frequently lost. After /clear it was
        permanently broken: same conv_id meant no SQLite reload, so messages stayed []
        forever.

        Fix: _conv_cache[conv_id] holds the live message list in memory. SQLite load
        happens exactly once on first contact; persist_turn writes continue as crash
        recovery but are no longer on the critical read path.
        """
        from contextlib import nullcontext
        conv_id = f"telegram:{chat_id}"

        # T-223: resolve guest profile for memory/consciousness isolation.
        # Ash's chat_id bypasses profile lookup entirely.
        guest_profile = None
        if _ALLOWED_CHAT_ID and str(chat_id) != _ALLOWED_CHAT_ID:
            guest_profile = self._resolve_profile(str(chat_id))

        # Swap REPL state out, swap Telegram conversation in
        saved_conv_id = self._agent.conversation_id
        saved_messages = list(self._agent.messages)
        self._agent._current_chat_id = str(chat_id)

        if guest_profile:
            from agent.profile import profile_switch
            ctx = profile_switch(self._agent, guest_profile)
        else:
            ctx = nullcontext()

        with ctx:
            # Hydrate from SQLite on first contact; use cache on every subsequent turn.
            # Must run inside ctx so memory points to the guest's DB, not Ash's.
            if conv_id not in self._conv_cache:
                try:
                    turns = self._agent.memory.load_conversation_turns(conv_id, max_turns=40)
                    self._conv_cache[conv_id] = list(turns) if turns else []
                except Exception:
                    self._conv_cache[conv_id] = []

            self._agent.messages = self._conv_cache[conv_id]
            self._agent.conversation_id = conv_id
            try:
                result = self._agent.process_input(text)
                self._conv_cache[conv_id] = list(self._agent.messages)
                return result
            finally:
                self._agent.conversation_id = saved_conv_id
                self._agent.messages = saved_messages
                self._agent._current_chat_id = None

    # ── handler registration ──────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        """Wire message handlers onto the bot."""
        bot = self._bot

        @bot.message_handler(commands=["start", "help"])
        def handle_start(message):
            if not self._auth(message):
                bot.reply_to(message, "To get started, send: /login <name> <password>")
                return
            bot.reply_to(
                message,
                "Pi is online.\n\n"
                "Send text, photos, videos, documents, or voice messages.\n"
                "/briefing — morning briefing\n"
                "/recall &lt;query&gt; — search past sessions\n"
                "/research &lt;topic&gt; — deep multi-source research\n"
                "/status — Pi health snapshot\n"
                "/imagine &lt;prompt&gt; — generate an image\n"
                "/generate_video &lt;prompt&gt; — generate a short video clip\n"
                "/newchat — start a fresh conversation thread\n"
                "/exit — end session (runs caretaker + saves summary)\n"
                "/clear — reset conversation context, keep memory\n"
                "/help — show this message",
            )

        # T-126: lifecycle commands. Both force-flush any open bubble first
        # so the partial bubble is not lost.
        @bot.message_handler(commands=["exit"])
        def handle_exit(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            chat_id = str(message.chat.id)
            # Force-flush any open bubble first; consumer dispatches it under
            # the current session_id before we proceed with exit work.
            if self._bubble is not None:
                self._bubble.flush(chat_id, reason="lifecycle")
            # Run agent's session-exit flow synchronously
            try:
                if self._agent is not None:
                    from agent.session import on_exit as _on_exit
                    _on_exit(self._agent)
            except Exception as e:
                try:
                    track_silent("telegram.exit_command", e)
                except Exception:
                    pass
            bot.reply_to(message, "Session ended. Send any message to start a new one.")

        @bot.message_handler(commands=["clear"])
        def handle_clear(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            chat_id = str(message.chat.id)
            # Force-flush any open bubble first so the user's last thought is
            # dispatched under the OLD session_id (not lost during rotation).
            if self._bubble is not None:
                self._bubble.flush(chat_id, reason="lifecycle")
            # Rotate session_id; memory tier (L1/L2/L3) is untouched.
            if self._agent is not None:
                import uuid as _uuid
                self._agent.session_id = _uuid.uuid4().hex[:8]
                self._agent.messages = []
            # T-244: reset the in-memory conv cache for this chat so the next
            # message starts with [] and does NOT reload old turns from SQLite.
            self._conv_cache[f"telegram:{chat_id}"] = []
            bot.reply_to(message, "Context cleared. Memory preserved. New session started.")

        # T-142: /newchat — start a fresh conversation thread (rotates
        # conversation_id, clears short-term context) while keeping L3. Delegates
        # to process_input so the reset logic lives in one place (pi_agent).
        @bot.message_handler(commands=["newchat"])
        def handle_newchat(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            chat_id = str(message.chat.id)
            if self._bubble is not None:
                self._bubble.flush(chat_id, reason="lifecycle")
            reply = "New chat started. Short-term context cleared; long-term memory kept."
            if self._agent is not None:
                try:
                    reply = self._agent.process_input("/newchat")
                except Exception as e:
                    from agent.observability import track_silent
                    track_silent("telegram.newchat_command", e)
            bot.reply_to(message, reply)

        @bot.message_handler(commands=["imagine"])
        def handle_imagine(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            prompt = message.text.partition(" ")[2].strip()
            if not prompt:
                bot.reply_to(message, "Usage: /imagine <description>")
                return
            bot.send_chat_action(message.chat.id, "upload_photo")
            try:
                from tools.tools_image import generate_image
                result = generate_image(prompt)
                if result.get("success") and result.get("path"):
                    path = result["path"]
                    with open(path, "rb") as img:
                        bot.send_photo(message.chat.id, img, caption=f"_{prompt[:200]}_")
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                else:
                    bot.reply_to(message, f"Image gen failed: {result.get('error', 'unknown')}")
            except Exception as e:
                logger.exception("Telegram /imagine error")
                track_silent("telegram.imagine", e)
                bot.reply_to(message, f"[Pi] Image gen error: {safe_error(e, audience='telegram')}")

        @bot.message_handler(commands=["generate_video"])
        def handle_generate_video(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            prompt = message.text.partition(" ")[2].strip()
            if not prompt:
                bot.reply_to(message, "Usage: /generate_video <description>")
                return
            bot.send_chat_action(message.chat.id, "upload_video")
            bot.reply_to(message, "Generating video... this may take 30-120s.")
            try:
                from tools.tools_video_gen import generate_video
                result = generate_video(prompt)
                if result.get("success") and result.get("path"):
                    path = result["path"]
                    with open(path, "rb") as vid:
                        bot.send_video(message.chat.id, vid, caption=f"_{prompt[:200]}_")
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                else:
                    bot.reply_to(message, f"Video gen failed: {result.get('error', 'unknown')}")
            except Exception as e:
                logger.exception("Telegram /generate_video error")
                track_silent("telegram.generate_video", e)
                bot.reply_to(message, f"[Pi] Video gen error: {safe_error(e, audience='telegram')}")

        @bot.message_handler(content_types=["photo"])
        def handle_photo(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            bot.send_chat_action(message.chat.id, "typing")
            if not message.photo:
                bot.reply_to(message, "Couldn't read the photo — try resending or send a direct link instead.")
                return
            caption = (message.caption or "").strip()
            question = caption if caption else "Describe this image in detail."
            file_id = message.photo[-1].file_id
            path = self._download_file(bot, file_id, ".jpg")
            if not path:
                bot.reply_to(message, "[Pi] Couldn't download the photo.")
                return
            try:
                from tools.tools_media import MediaTools
                result = MediaTools.analyze_image(path, question=question)
                if result.get("analysis"):
                    analysis = result["analysis"]
                elif result.get("description"):
                    analysis = result["description"]
                else:
                    err = result.get("error") or "(no result)"
                    track_silent("telegram.photo_empty_result", ValueError(f"shape: {list(result.keys())}"))
                    analysis = f"Vision API failed: {err}"
                # T-138: route through Pi so the photo enters L1/L2 + consciousness loop
                reply = self._media_route(message, analysis, kind="photo", caption=caption)
                self._send_plain_chunks(bot, message, reply)
            except Exception as e:
                logger.exception("Telegram photo analysis error")
                track_silent("telegram.photo", e)
                self._send_plain_chunks(bot, message, f"[Pi] Analysis error: {safe_error(e, audience='telegram')}")
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        @bot.message_handler(content_types=["video", "video_note"])
        def handle_video(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            bot.send_chat_action(message.chat.id, "typing")
            video = message.video or message.video_note
            caption = (getattr(message, "caption", None) or "").strip()
            question = caption if caption else "Describe what happens in this video."
            path = self._download_file(bot, video.file_id, ".mp4")
            if not path:
                bot.reply_to(message, "[Pi] Couldn't download the video.")
                return
            try:
                from tools.tools_media import MediaTools
                result = MediaTools.analyze_video(path, question=question)
                if result.get("analysis"):
                    analysis = result["analysis"]
                elif result.get("description"):
                    analysis = result["description"]
                else:
                    err = result.get("error") or "(no result)"
                    track_silent("telegram.video_empty_result", ValueError(f"shape: {list(result.keys())}"))
                    analysis = f"Video analysis failed: {err}"
                # T-138: route through Pi
                reply = self._media_route(message, analysis, kind="video", caption=caption)
                self._send_plain_chunks(bot, message, reply)
            except Exception as e:
                logger.exception("Telegram video analysis error")
                track_silent("telegram.video", e)
                self._send_plain_chunks(bot, message, f"[Pi] Video analysis error: {safe_error(e, audience='telegram')}")
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        @bot.message_handler(content_types=["document"])
        def handle_document(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            bot.send_chat_action(message.chat.id, "typing")
            doc = message.document
            caption = (message.caption or "").strip()
            question = caption if caption else ""
            suffix = Path(doc.file_name or "file").suffix or ".bin"
            path = self._download_file(bot, doc.file_id, suffix)
            if not path:
                bot.reply_to(message, "[Pi] Couldn't download the document.")
                return
            try:
                from tools.tools_media import MediaTools
                result = MediaTools.analyze_document_smart(path, question=question)
                if result.get("answer"):
                    analysis = result["answer"]
                elif result.get("summary"):
                    analysis = result["summary"]
                else:
                    err = result.get("error") or "(no result)"
                    track_silent("telegram.document_empty_result", ValueError(f"shape: {list(result.keys())}"))
                    analysis = f"Document analysis failed: {err}"
                # T-138: route through Pi; T-239: stores analysis to L2 via filename
                reply = self._media_route(message, analysis, kind="document",
                                          caption=caption, filename=doc.file_name or "")
                self._send_plain_chunks(bot, message, reply)
            except Exception as e:
                logger.exception("Telegram document analysis error")
                track_silent("telegram.document", e)
                self._send_plain_chunks(bot, message, f"[Pi] Document analysis error: {safe_error(e, audience='telegram')}")
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        @bot.message_handler(content_types=["voice", "audio"])
        def handle_voice(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            bot.send_chat_action(message.chat.id, "typing")
            voice = message.voice or message.audio
            suffix = ".ogg" if message.voice else ".mp3"
            path = self._download_file(bot, voice.file_id, suffix)
            if not path:
                bot.reply_to(message, "[Pi] Couldn't download the voice message.")
                return
            try:
                from tools.tools_stt import STTTools
                stt = STTTools()
                stt_result = stt.transcribe_file(path)
                if not stt_result.get("success"):
                    bot.reply_to(message, f"[Pi] Transcription failed: {stt_result.get('error')}")
                    return
                transcript = stt_result["text"]
                if not transcript.strip():
                    bot.send_message(message.chat.id, "Couldn't make out what you said — try again?")
                    return
                # Route transcript through the bubble system so it gets the full
                # thinking + recall + memory context path (same as typed text).
                if self._bubble is not None:
                    from agent.bubble import BubbleMessage
                    self._bubble.add(
                        str(message.chat.id),
                        BubbleMessage(
                            text=transcript,
                            sent_at=message.date if isinstance(message.date, (int, float)) else 0.0,
                            message_id=getattr(message, "message_id", None),
                            reply_to_text=None,
                            raw=message,
                        ),
                    )
                    # Quick ack so Ash knows Pi heard him — reply is coming via bubble
                    try:
                        bot.send_message(message.chat.id, f"🎙 \"{transcript}\"")
                    except Exception:
                        pass
                else:
                    # Fallback: direct path (use_bubble=False)
                    reply = self._process_text(transcript, chat_id=message.chat.id)
                    self._send_plain_chunks(bot, message, reply)
            except Exception as e:
                logger.exception("Telegram voice handler error")
                track_silent("telegram.voice", e)
                self._send_plain_chunks(bot, message, f"[Pi error] {safe_error(e, audience='telegram')}")
            finally:
                try:
                    import os as _os
                    _os.unlink(path)
                except OSError:
                    pass

        # ── T-222: Profile auth commands ─────────────────────────────────────
        # /login <name> <password>   — one-time device bind (best-effort delete msg)
        # /logout                    — unbind this device
        # /whoami                    — show current profile
        # /profile create|list|delete|revoke — ash-only management

        @bot.message_handler(commands=["login"])
        def handle_login(message):
            chat_id = str(message.chat.id)
            try:
                from agent.profile import get_registry, verify_password
                reg = get_registry()
                # Always delete the login message to avoid exposing passwords in chat.
                try:
                    bot.delete_message(message.chat.id, message.message_id)
                except Exception as e:
                    track_silent("telegram.login_message_delete", e,
                                 context={"chat_id": chat_id})

                parts = (message.text or "").split(maxsplit=2)  # /login name password
                if len(parts) < 3:
                    bot.send_message(message.chat.id, "Usage: /login <name> <password>")
                    return

                name, password = parts[1].strip().lower(), parts[2].strip()

                # Ash is authenticated by chat_id, not by password.
                if name == "ash":
                    bot.send_message(message.chat.id, "Ash authenticates automatically by device — /login is not needed.")
                    return

                # Lockout check
                if reg.is_locked_out(name, chat_id):
                    bot.send_message(message.chat.id, "Too many failed attempts. Please wait 10 minutes.")
                    return

                profile = reg.get_profile(name)
                success = profile is not None and verify_password(password, profile.password_hash, profile.salt)
                reg.record_attempt(name, chat_id, success=success)

                if not success:
                    bot.send_message(message.chat.id, "Login failed.")
                    return

                reg.bind_device(chat_id, name)
                reg.update_last_login(name)
                bot.send_message(message.chat.id, f"Logged in as {name}. This device is now permanently linked.")
            except Exception as e:
                logger.exception("Telegram /login error")
                bot.send_message(message.chat.id, "Login error — please try again.")

        @bot.message_handler(commands=["logout"])
        def handle_logout(message):
            chat_id = str(message.chat.id)
            try:
                from agent.profile import get_registry
                reg = get_registry()
                removed = reg.unbind_device(chat_id)
                if removed:
                    bot.send_message(message.chat.id, "Logged out. This device is unlinked.")
                else:
                    bot.send_message(message.chat.id, "No active session to log out from.")
            except Exception as e:
                logger.exception("Telegram /logout error")
                bot.send_message(message.chat.id, "Logout error.")

        @bot.message_handler(commands=["whoami"])
        def handle_whoami(message):
            chat_id = str(message.chat.id)
            try:
                profile = self._resolve_profile(chat_id)
                if profile is None:
                    bot.send_message(message.chat.id, "Not logged in. Use /login <name> <password>.")
                else:
                    guest_tag = " (guest)" if profile.is_guest else ""
                    bot.send_message(message.chat.id, f"You are: {profile.name}{guest_tag}")
            except Exception as e:
                logger.exception("Telegram /whoami error")
                bot.send_message(message.chat.id, "Error.")

        @bot.message_handler(commands=["profile"])
        def handle_profile_mgmt(message):
            chat_id = str(message.chat.id)
            # Ash-only: authenticated by trusted chat_id
            if not self._auth(message):
                bot.send_message(message.chat.id, "Profile management requires Ash access.")
                return
            try:
                from agent.profile import get_registry
                reg = get_registry()
                parts = (message.text or "").split(maxsplit=3)
                sub = parts[1].lower() if len(parts) > 1 else ""

                if sub == "create":
                    if len(parts) < 4:
                        bot.send_message(message.chat.id, "Usage: /profile create <name> <password>")
                        return
                    try:
                        bot.delete_message(message.chat.id, message.message_id)
                    except Exception as e:
                        track_silent("telegram.profile_create_message_delete", e,
                                     context={"chat_id": message.chat.id})
                    display_name, password = parts[2].strip(), parts[3].strip()
                    p = reg.create_profile(display_name, password, is_guest=True)
                    bot.send_message(message.chat.id, f"Profile '{p.display_name}' created (login: {p.name}).")

                elif sub == "list":
                    profiles = reg.list_profiles()
                    lines = [f"  {p.name} ({'guest' if p.is_guest else 'owner'})" for p in profiles]
                    bot.send_message(message.chat.id, "Profiles:\n" + "\n".join(lines))

                elif sub == "delete":
                    if len(parts) < 3:
                        bot.send_message(message.chat.id, "Usage: /profile delete <name>")
                        return
                    name = parts[2].strip().lower()
                    reg.delete_profile(name)
                    bot.send_message(message.chat.id, f"Profile '{name}' deleted.")

                elif sub == "revoke":
                    if len(parts) < 3:
                        bot.send_message(message.chat.id, "Usage: /profile revoke <name>")
                        return
                    name = parts[2].strip().lower()
                    n = reg.revoke_profile_devices(name)
                    bot.send_message(message.chat.id, f"Revoked {n} device binding(s) for '{name}'.")

                else:
                    bot.send_message(message.chat.id,
                        "/profile create <name> <password>\n"
                        "/profile list\n"
                        "/profile delete <name>\n"
                        "/profile revoke <name>")
            except ValueError as ve:
                bot.send_message(message.chat.id, f"Error: {ve}")
            except Exception as e:
                logger.exception("Telegram /profile error")
                bot.send_message(message.chat.id, "Profile command error.")

        # T-225: execution approval workflow — ash-only
        @bot.message_handler(commands=["approve", "deny", "approvals"])
        def handle_approvals(message):
            if not self._auth(message):
                bot.send_message(message.chat.id, "Unauthorized.")
                return
            try:
                from agent.profile import get_registry
                import json as _json
                from datetime import datetime as _dt, timezone as _tz
                reg = get_registry()
                cmd = (message.text or "").split()[0].lstrip("/").lower()

                _pm = {"parse_mode": None}  # plain text — bot defaults to HTML globally
                if cmd == "approvals":
                    with reg._connect() as conn:
                        conn.execute(
                            "CREATE TABLE IF NOT EXISTS approvals "
                            "(token TEXT PRIMARY KEY, profile_name TEXT, tool TEXT, args_json TEXT, "
                            "status TEXT DEFAULT 'pending', created_at TEXT, expires_at TEXT, "
                            "requester_chat_id TEXT DEFAULT '')"
                        )
                        rows = conn.execute(
                            "SELECT token, profile_name, tool, status, expires_at FROM approvals "
                            "WHERE status='pending' ORDER BY created_at DESC LIMIT 10"
                        ).fetchall()
                    if not rows:
                        bot.send_message(message.chat.id, "No pending approvals.", **_pm)
                    else:
                        lines = ["Pending approvals:"]
                        for r in rows:
                            lines.append(f"  [{r[1]}] {r[2]}  token={r[0]}  expires={r[4]}")
                        bot.send_message(message.chat.id, "\n".join(lines), **_pm)
                    return

                parts = (message.text or "").split(maxsplit=1)
                if len(parts) < 2:
                    bot.send_message(message.chat.id, f"Usage: /{cmd} TOKEN", **_pm)
                    return
                token = parts[1].strip()

                with reg._connect() as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS approvals "
                        "(token TEXT PRIMARY KEY, profile_name TEXT, tool TEXT, args_json TEXT, "
                        "status TEXT DEFAULT 'pending', created_at TEXT, expires_at TEXT, "
                        "requester_chat_id TEXT DEFAULT '')"
                    )
                    row = conn.execute(
                        "SELECT * FROM approvals WHERE token=?", [token]
                    ).fetchone()

                if row is None:
                    bot.send_message(message.chat.id, f"Token not found: {token}", **_pm)
                    return

                row_dict = dict(row)
                if row_dict.get("status") != "pending":
                    bot.send_message(message.chat.id, f"Token already resolved: status={row_dict['status']}", **_pm)
                    return
                expires_at = row_dict.get("expires_at", "")
                if expires_at and _dt.now(_tz.utc).isoformat() > expires_at:
                    with reg._connect() as conn:
                        conn.execute("UPDATE approvals SET status='expired' WHERE token=?", [token])
                        conn.commit()
                    bot.send_message(message.chat.id, f"Token expired: {token}", **_pm)
                    return

                if cmd == "deny":
                    with reg._connect() as conn:
                        conn.execute("UPDATE approvals SET status='denied' WHERE token=?", [token])
                        conn.commit()
                    bot.send_message(message.chat.id, f"Denied: {token}", **_pm)
                    # Notify the guest
                    guest_chat = row_dict.get("requester_chat_id", "")
                    if guest_chat:
                        try:
                            bot.send_message(int(guest_chat),
                                f"Your request to run '{row_dict['tool']}' was denied.",
                                parse_mode=None)
                        except Exception as e:
                            track_silent("telegram.approval_deny_notify", e,
                                         context={"guest_chat": guest_chat})
                    return

                # /approve — execute the tool under the guest's profile
                profile_name = row_dict["profile_name"]
                tool_name = row_dict["tool"]
                tool_input = _json.loads(row_dict.get("args_json") or "{}")
                guest_chat = row_dict.get("requester_chat_id", "")

                try:
                    guest_profile = reg.get_profile(profile_name)
                    if guest_profile is None:
                        bot.send_message(message.chat.id, f"Profile not found: {profile_name}")
                        return

                    from agent.profile import profile_switch
                    from agent.tools import execute_tool as _exec_tool

                    with profile_switch(self._agent, guest_profile):
                        result = _exec_tool(self._agent, tool_name, tool_input)

                    with reg._connect() as conn:
                        conn.execute("UPDATE approvals SET status='approved' WHERE token=?", [token])
                        conn.commit()

                    result_text = _json.dumps(result, indent=2)[:800]
                    bot.send_message(message.chat.id, f"Approved & executed: {token}\n{result_text}", **_pm)
                    if guest_chat:
                        try:
                            bot.send_message(int(guest_chat),
                                f"Your request to run '{tool_name}' was approved.\nResult:\n{result_text}",
                                parse_mode=None)
                        except Exception as e:
                            track_silent("telegram.approval_grant_notify", e,
                                         context={"guest_chat": guest_chat})
                except Exception as e:
                    logger.exception("Telegram /approve execution error")
                    bot.send_message(message.chat.id, f"Execution error: {e}", **_pm)
            except Exception as e:
                logger.exception("Telegram approvals handler error")
                bot.send_message(message.chat.id, f"Error: {e}", **_pm)

        # Convenience shortcut commands — translate to natural-language requests so
        # Pi's full tool system handles them with proper context and formatting.

        @bot.message_handler(commands=["briefing"])
        def handle_briefing(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            reply = self._process_text("Give me my daily briefing.", chat_id=str(message.chat.id))
            formatted = _format_for_telegram(reply)
            for chunk in _chunk_text(formatted, 4096):
                try:
                    bot.send_message(message.chat.id, chunk, parse_mode="HTML")
                except Exception:
                    bot.send_message(message.chat.id, chunk, parse_mode=None)

        @bot.message_handler(commands=["recall"])
        def handle_recall(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            parts = (message.text or "").split(maxsplit=1)
            query = parts[1].strip() if len(parts) > 1 else ""
            if not query:
                bot.reply_to(message, "Usage: /recall <what to look for>")
                return
            reply = self._process_text(f"Search my episodic memory for: {query}", chat_id=str(message.chat.id))
            formatted = _format_for_telegram(reply)
            for chunk in _chunk_text(formatted, 4096):
                try:
                    bot.send_message(message.chat.id, chunk, parse_mode="HTML")
                except Exception:
                    bot.send_message(message.chat.id, chunk, parse_mode=None)

        @bot.message_handler(commands=["research"])
        def handle_research(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            parts = (message.text or "").split(maxsplit=1)
            query = parts[1].strip() if len(parts) > 1 else ""
            if not query:
                bot.reply_to(message, "Usage: /research <topic>")
                return
            reply = self._process_text(f"Deep research: {query}", chat_id=str(message.chat.id))
            formatted = _format_for_telegram(reply)
            for chunk in _chunk_text(formatted, 4096):
                try:
                    bot.send_message(message.chat.id, chunk, parse_mode="HTML")
                except Exception:
                    bot.send_message(message.chat.id, chunk, parse_mode=None)

        @bot.message_handler(commands=["status"])
        def handle_status(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            try:
                from pathlib import Path as _Path
                import sqlite3 as _sq
                root = _Path(__file__).parent.parent
                status_md = root / "docs" / "STATUS.md"
                db = root / "data" / "pi.db"
                lines = ["<b>Pi Status</b>"]
                if status_md.exists():
                    for ln in status_md.read_text(encoding="utf-8").splitlines()[:5]:
                        if ln.strip():
                            lines.append(ln.replace("**", "<b>", 1).replace("**", "</b>", 1))
                if db.exists():
                    try:
                        conn = _sq.connect(str(db))
                        l3 = conn.execute("SELECT count(*) FROM l3_cache WHERE invalid_at IS NULL").fetchone()[0]
                        conn.close()
                        lines.append(f"L3 active facts: {l3}")
                    except Exception:
                        pass
                bot.reply_to(message, "\n".join(lines), parse_mode="HTML")
            except Exception as e:
                bot.reply_to(message, f"Status unavailable: {e}")

        # T-220: inline-button callback handler — routes the chosen value back through
        # process_input as a normal turn so Pi can follow up naturally.
        @bot.callback_query_handler(func=lambda call: True)
        def handle_callback(call):
            chat_id = call.message.chat.id
            chosen = call.data or ""

            # T-278: same gate as handle_text — a tap in an unauthorized chat
            # must not trigger an agent turn.
            if not self._auth(call.message) and not self._resolve_profile(str(chat_id)):
                try:
                    bot.answer_callback_query(call.id, "Unauthorized")
                except Exception:
                    pass
                return

            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass

            # T-258: "ignore" needs no turn at all — acknowledge and stop.
            if chosen.startswith(_EMAIL_TRIAGE_PREFIX) and chosen[len(_EMAIL_TRIAGE_PREFIX):].split(":", 1)[0] == "ignore":
                try:
                    bot.send_message(chat_id, "Ignored.")
                except Exception:
                    pass
                return

            instruction = _email_triage_instruction(chosen)
            prompt = instruction if instruction is not None else f"[Button selected: {chosen}]"
            try:
                reply = self._process_text(prompt, chat_id=chat_id)
                formatted = _format_for_telegram(reply)
                for chunk in _chunk_text(formatted, 4096):
                    try:
                        bot.send_message(chat_id, chunk, parse_mode="HTML")
                    except Exception:
                        bot.send_message(chat_id, chunk, parse_mode=None)
            except Exception as e:
                try:
                    bot.send_message(chat_id, f"[Pi error] {safe_error(e, audience='telegram')}")
                except Exception:
                    pass

        @bot.message_handler(content_types=["text"])
        def handle_text(message):
            if not self._auth(message) and not self._resolve_profile(str(message.chat.id)):
                bot.reply_to(message, "Unauthorized.")
                return

            # In group chats respond only when @mentioned or replying to Pi
            if message.chat.type in ("group", "supergroup"):
                if self._bot_id is None:
                    me = bot.get_me()
                    self._bot_id = me.id
                    self._bot_username = (me.username or "").lower()
                tagged = (
                    self._bot_username
                    and f"@{self._bot_username}" in (message.text or "").lower()
                )
                replied_to_pi = (
                    message.reply_to_message is not None
                    and message.reply_to_message.from_user is not None
                    and message.reply_to_message.from_user.id == self._bot_id
                )
                if not tagged and not replied_to_pi:
                    return

            user_text = message.text.strip()
            # Strip leading @mention so Pi doesn't echo it back
            if self._bot_username and user_text.lower().startswith(f"@{self._bot_username}"):
                user_text = user_text[len(self._bot_username) + 1:].strip()
            if not user_text:
                return

            # T-238: if message is a bare URL (no other text), enrich with a fetch hint.
            user_text = _maybe_enrich_url(user_text)

            # T-122: enqueue into bubble; consumer thread dispatches.
            # Show typing indicator immediately so the user sees Pi is listening.
            try:
                bot.send_chat_action(message.chat.id, "typing")
            except Exception:
                pass

            if self._bubble is not None:
                from agent.bubble import BubbleMessage
                reply_to_text = None
                if message.reply_to_message is not None:
                    reply_to_text = getattr(message.reply_to_message, "text", None)
                self._bubble.add(
                    str(message.chat.id),
                    BubbleMessage(
                        text=user_text,
                        sent_at=message.date if isinstance(message.date, (int, float)) else 0.0,
                        message_id=getattr(message, "message_id", None),
                        reply_to_text=reply_to_text,
                        raw=message,
                    ),
                )
                return

            # Legacy path (use_bubble=False) — kept for tests and emergency disable
            try:
                reply = self._process_text(user_text, chat_id=message.chat.id)
                self._reply_chunks(bot, message, reply)
            except Exception as e:
                logger.exception("Telegram handler error")
                track_silent("telegram.handler", e)
                bot.reply_to(message, f"[Pi error] {safe_error(e, audience='telegram')}")


def _chunk_text(text: str, max_len: int):
    """Yield successive chunks of text up to max_len characters."""
    for i in range(0, len(text), max_len):
        yield text[i : i + max_len]


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


_EXT_TO_KIND: Dict[str, str] = {
    ".jpg": "photo", ".jpeg": "photo", ".png": "photo", ".gif": "photo", ".webp": "photo",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
    ".mp3": "audio", ".m4a": "audio", ".aac": "audio", ".flac": "audio", ".wav": "audio",
    ".ogg": "voice",
    ".pdf": "document", ".pptx": "document", ".docx": "document",
    ".txt": "document", ".csv": "document", ".json": "document", ".zip": "document",
}


def send_file(
    path: str,
    kind: Optional[str] = None,
    caption: str = "",
    chat_id: Optional[str] = None,
) -> Dict:
    """T-230: Send a file (photo/video/document/audio/voice) to Ash's Telegram.

    kind is inferred from the file extension when not provided.
    Falls back to returning {success:False, path, note} on any error rather than
    raising, so callers can still tell Ash where the artifact is.
    """
    import os as _os
    target = chat_id or _ALLOWED_CHAT_ID
    if not target:
        return {"success": False, "path": path, "note": "No chat_id — set TELEGRAM_CHAT_ID"}

    bot = _get_bot()
    if bot is None:
        return {"success": False, "path": path,
                "note": "TELEGRAM_BOT_TOKEN not set or pyTelegramBotAPI not installed"}

    if not path or not _os.path.isfile(path):
        return {"success": False, "path": path, "note": f"File not found: {path}"}

    ext = _os.path.splitext(path)[1].lower()
    resolved_kind = kind or _EXT_TO_KIND.get(ext, "document")
    caption_text = _format_for_telegram(caption) if caption else ""

    _MAX_TG_BYTES = 50 * 1024 * 1024
    file_size = _os.path.getsize(path)
    if file_size > _MAX_TG_BYTES:
        return {
            "success": False,
            "path": path,
            "note": f"File too large for Telegram ({file_size // (1024*1024)} MB > 50 MB)",
        }

    try:
        with open(path, "rb") as f:
            if resolved_kind == "photo":
                bot.send_photo(int(target), f, caption=caption_text, parse_mode="HTML")
            elif resolved_kind == "video":
                bot.send_video(int(target), f, caption=caption_text, parse_mode="HTML")
            elif resolved_kind == "audio":
                bot.send_audio(int(target), f, caption=caption_text, parse_mode="HTML")
            elif resolved_kind == "voice":
                bot.send_voice(int(target), f, caption=caption_text, parse_mode="HTML")
            else:
                bot.send_document(int(target), f, caption=caption_text, parse_mode="HTML")
        # Best-effort temp cleanup (mirrors /imagine pattern)
        try:
            if _os.path.dirname(path) in (
                _os.environ.get("TEMP", ""), _os.environ.get("TMP", ""),
                "/tmp",
            ) or "tmp" in path.lower():
                _os.unlink(path)
        except Exception:
            pass
        return {"success": True, "kind": resolved_kind}
    except Exception as e:
        return {"success": False, "path": path, "note": str(e)}


def _handle_telegram_send(agent, tool_input, *, memory_override=None):
    file_path = tool_input.get("file")
    if file_path:
        return send_file(
            path=file_path,
            kind=tool_input.get("kind"),
            caption=tool_input.get("text", ""),
            chat_id=tool_input.get("chat_id"),
        )
    return send_message(
        text=tool_input["text"],
        chat_id=tool_input.get("chat_id"),
    )


# ── T-220: Telegram native actions ────────────────────────────────────────────

def _handle_telegram_react(agent, tool_input, *, memory_override=None):
    """React to the user's current message with an emoji."""
    emoji = tool_input.get("emoji", "👍")
    chat_id = getattr(agent, "_current_chat_id", None)
    message_id = getattr(agent, "_current_message_id", None)
    if not chat_id or message_id is None:
        return {"success": False, "note": "telegram_react is only available on the Telegram channel"}
    bot = _get_bot()
    if bot is None:
        return {"success": False, "note": "Bot unavailable"}
    try:
        import telebot
        react_fn = getattr(bot, "set_message_reaction", None)
        if react_fn is None:
            return {"success": False, "note": "set_message_reaction not available — upgrade pyTelegramBotAPI to >=4.14"}
        react_fn(int(chat_id), message_id, [telebot.types.ReactionTypeEmoji(emoji)])
        return {"success": True, "emoji": emoji}
    except Exception as e:
        return {"success": False, "note": str(e)}


def _handle_telegram_buttons(agent, tool_input, *, memory_override=None):
    """Send a message with inline quick-reply buttons."""
    text = tool_input.get("text", "")
    options = tool_input.get("options", [])
    chat_id = getattr(agent, "_current_chat_id", None)
    if not chat_id:
        return {"success": False, "note": "telegram_buttons is only available on the Telegram channel"}
    bot = _get_bot()
    if bot is None:
        return {"success": False, "note": "Bot unavailable"}
    try:
        import telebot
        keyboard = telebot.types.InlineKeyboardMarkup()
        for opt in options[:6]:
            label = str(opt)[:64]
            keyboard.add(telebot.types.InlineKeyboardButton(text=label, callback_data=label))
        sent = bot.send_message(
            int(chat_id),
            _format_for_telegram(text),
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        msg_id = getattr(sent, "message_id", None) if sent else None
        return {"success": True, "message_id": msg_id}
    except Exception as e:
        return {"success": False, "note": str(e)}


def _handle_telegram_edit_last(agent, tool_input, *, memory_override=None):
    """Edit Pi's most recently sent Telegram message in-place."""
    text = tool_input.get("text", "")
    chat_id = getattr(agent, "_current_chat_id", None)
    message_id = getattr(agent, "_last_sent_message_id", None)
    if not chat_id or message_id is None:
        return {"success": False, "note": "telegram_edit_last is only available on Telegram when a prior message exists"}
    bot = _get_bot()
    if bot is None:
        return {"success": False, "note": "Bot unavailable"}
    try:
        bot.edit_message_text(
            _format_for_telegram(text),
            int(chat_id),
            message_id,
            parse_mode="HTML",
        )
        return {"success": True}
    except Exception as e:
        return {"success": False, "note": str(e)}


TOOLS = [
    ToolSpec(
        name="telegram_react",
        description=(
            "React to the user's current Telegram message with an emoji. "
            "Only works on the Telegram channel — returns a declined note otherwise. "
            "Use sparingly: one reaction per turn at most, only when it adds clear value."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "emoji": {
                    "type": "string",
                    "description": "Emoji character to react with (e.g. '👍', '🔥', '✅')",
                },
            },
            "required": ["emoji"],
        },
        handler=_handle_telegram_react,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="telegram_buttons",
        description=(
            "Send a message with inline quick-reply buttons. "
            "Only works on the Telegram channel. "
            "Use when offering the user a small set of clear choices (max 6 options). "
            "The user's button press is routed back as a normal turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Message text to display above the buttons",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of button labels (max 6, each max 64 chars)",
                },
            },
            "required": ["text", "options"],
        },
        handler=_handle_telegram_buttons,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="telegram_edit_last",
        description=(
            "Edit Pi's most recently sent Telegram message in-place. "
            "Only works on the Telegram channel. "
            "Use to correct a mistake or add progressive detail to the last reply."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Full replacement text for the last message",
                },
            },
            "required": ["text"],
        },
        handler=_handle_telegram_edit_last,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="telegram_send",
        description=(
            "Send a message or file to Ash's Telegram. For text: set 'text'. "
            "For files (images, video, PDF, audio) produced mid-turn: set 'file' to the "
            "local path; 'kind' (photo/video/document/audio/voice) is inferred from the "
            "extension when omitted. Use 'text' as the caption for files. "
            "Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text":    {"type": "string",
                            "description": "Message text or file caption"},
                "file":    {"type": "string",
                            "description": "Local path to a file to send (photo/video/pdf/audio/etc.)"},
                "kind":    {"type": "string",
                            "enum": ["photo", "video", "document", "audio", "voice"],
                            "description": "File type (inferred from extension when omitted)"},
                "chat_id": {"type": "string",
                            "description": "Override chat ID (uses TELEGRAM_CHAT_ID env var by default)"},
            },
            "required": ["text"],
        },
        handler=_handle_telegram_send,
        success_predicate=lambda r: r.get("success", False),
    ),
]
