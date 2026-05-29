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

    def is_available(self) -> bool:
        return self._bot is not None

    def send(self, text: str, chat_id: Optional[str] = None) -> Dict:
        return send_message(text, chat_id)

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

    def _auth(self, message) -> bool:
        """Return True if message is from an allowed chat."""
        return not _ALLOWED_CHAT_ID or str(message.chat.id) == _ALLOWED_CHAT_ID

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
        joined = bubble.joined_text()
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
        recall_block = recall_block_holder["text"]
        thinking_block = thinking_block_holder["text"]

        try:
            dispatched_text = (thinking_block + recall_block + joined) if joined else joined
            reply = self._process_text(dispatched_text) if dispatched_text else "(media-only bubble — handler not yet wired)"
            if last_msg is not None:
                self._reply_chunks(self._bot, last_msg, reply)
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

    def _reply_chunks(self, bot, message, text: str) -> None:
        for chunk in _chunk_text(text, 4096):
            bot.reply_to(message, chunk)

    def _send_plain_chunks(self, bot, message, text: str) -> None:
        """Chunked send with parse_mode=None — used by media handlers (T-138).

        Vision/STT outputs commonly contain raw *, _, [ chars that crash the
        bot's default Markdown parser and silently drop replies. Plain text
        sidesteps the whole issue.
        """
        for chunk in _chunk_text(text or "(empty)", 4096):
            bot.reply_to(message, chunk, parse_mode=None)

    def _media_route(self, message, analysis_text: str, kind: str, caption: str) -> str:
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
        return self._process_text(user_text)

    def _process_text(self, text: str) -> str:
        if self._on_message:
            return self._on_message(text)
        if self._agent is not None:
            return self._agent.process_input(text)
        return "(no agent)"

    # ── handler registration ──────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        """Wire message handlers onto the bot."""
        bot = self._bot

        @bot.message_handler(commands=["start", "help"])
        def handle_start(message):
            if not self._auth(message):
                bot.reply_to(message, "Unauthorized.")
                return
            bot.reply_to(
                message,
                "Pi is online.\n\n"
                "Send text, photos, videos, documents, or voice messages.\n"
                "/imagine <prompt> — generate an image\n"
                "/generate_video <prompt> — generate a short video clip\n"
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
                if hasattr(self._agent, "history"):
                    self._agent.history = []
            bot.reply_to(message, "Context cleared. Memory preserved. New session started.")

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
                # T-138: route through Pi
                reply = self._media_route(message, analysis, kind="document", caption=caption)
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
                    bot.reply_to(message, "[Pi] Couldn't make out what you said.")
                    return
                # Get Pi's response to the transcribed text (T-138: already routes through process_input)
                reply = self._process_text(transcript)
                # Send transcription + reply as PLAIN text (T-138: vision/STT outputs break Markdown)
                self._send_plain_chunks(bot, message, f'You said: "{transcript}"\n\n{reply}')
                # Try to send TTS audio reply
                try:
                    from tools.tools_tts import TTSTools
                    import tempfile as _tf
                    tts = TTSTools()
                    fd, audio_path = _tf.mkstemp(suffix=".mp3")
                    import os as _os
                    _os.close(fd)
                    tts_result = tts.save(reply[:2000], audio_path)
                    if tts_result.get("success") and Path(audio_path).exists():
                        with open(audio_path, "rb") as af:
                            bot.send_audio(message.chat.id, af, title="Pi")
                except Exception:
                    pass  # TTS is best-effort — text reply already sent
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

        @bot.message_handler(content_types=["text"])
        def handle_text(message):
            if not self._auth(message):
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
                reply = self._process_text(user_text)
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


def _handle_telegram_send(agent, tool_input, *, memory_override=None):
    return send_message(
        text=tool_input["text"],
        chat_id=tool_input.get("chat_id"),
    )


TOOLS = [
    ToolSpec(
        name="telegram_send",
        description=(
            "Send a message to Ash's Telegram. Use to push important updates, "
            "completed task notifications, or alerts when Ash is away from the "
            "computer. Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text":    {"type": "string",
                            "description": "Message text (Markdown supported)"},
                "chat_id": {"type": "string",
                            "description": "Override chat ID (uses TELEGRAM_CHAT_ID env var by default)"},
            },
            "required": ["text"],
        },
        handler=_handle_telegram_send,
        success_predicate=lambda r: r.get("success", False),
    ),
]
