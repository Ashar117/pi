"""agent/bubble.py — T-122: message bubble collector for Telegram.

A Bubble is one unit of conversation: messages that arrive within IDLE_MS of
each other are merged into a single Bubble. The first message starts the
bubble; each new message resets the idle timer. When idle expires (or a
lifecycle event fires), the bubble closes and is dispatched to a consumer.

This module is Telegram-only. Terminal mode dispatches each line immediately
and is unaffected.

Bubble close triggers:
  1. Idle timeout (PI_BUBBLE_IDLE_MS, default 6000ms)
  2. Hard upper bound (PI_BUBBLE_MAX_MS, default 120000ms)
  3. Message cap (PI_BUBBLE_MAX_MESSAGES, default 20)
  4. Media message (any image/video closes the bubble immediately — vision
     should not wait for more text)
  5. Lifecycle event (BubbleCollector.flush(chat_id) called by /exit /clear)

Concurrency model:
  - One BubbleCollector instance per process
  - Per-chat_id lock guards bubble state
  - Idle-flush runs on a daemon timer thread per chat_id
  - Closed bubbles are pushed to a consumer queue (queue.Queue) drained by
    a single consumer thread that calls dispatch_fn(bubble)
"""
from __future__ import annotations

import os
import queue
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ── Bubble dataclass ──────────────────────────────────────────────────────────

@dataclass
class BubbleMessage:
    """One message inside a bubble. Carries enough to rebuild dispatch context."""
    text: str
    sent_at: float
    message_id: Optional[int] = None
    reply_to_text: Optional[str] = None
    media_type: Optional[str] = None  # "photo" | "video" | "voice" | "document"
    media_path: Optional[str] = None  # local path after download
    raw: Any = None  # original telebot.Message for handlers that need it


@dataclass
class Bubble:
    chat_id: str
    messages: List[BubbleMessage] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float = 0.0
    closed_reason: str = ""  # "idle" | "max_ms" | "max_messages" | "media" | "lifecycle"

    @property
    def has_media(self) -> bool:
        return any(m.media_type for m in self.messages)

    @property
    def reply_targets(self) -> List[str]:
        return [m.reply_to_text for m in self.messages if m.reply_to_text]

    def joined_text(self, joiner: str = "\n") -> str:
        """Concatenate the bubble's text messages for downstream dispatch."""
        return joiner.join(m.text for m in self.messages if m.text)


# ── BubbleCollector ───────────────────────────────────────────────────────────

class BubbleCollector:
    """Collects messages into bubbles per chat_id; dispatches via dispatch_fn.

    dispatch_fn(bubble) is called from the consumer thread for each closed
    bubble. Failures inside dispatch_fn are caught and logged via track_silent.
    """

    def __init__(
        self,
        dispatch_fn: Callable[[Bubble], None],
        idle_ms: Optional[int] = None,
        max_bubble_ms: Optional[int] = None,
        max_messages: Optional[int] = None,
    ):
        self.dispatch_fn = dispatch_fn
        self.idle_ms = idle_ms if idle_ms is not None else _env_int("PI_BUBBLE_IDLE_MS", 6000)
        self.max_bubble_ms = max_bubble_ms if max_bubble_ms is not None else _env_int("PI_BUBBLE_MAX_MS", 120000)
        self.max_messages = max_messages if max_messages is not None else _env_int("PI_BUBBLE_MAX_MESSAGES", 20)

        # Per-chat_id state
        self._bubbles: Dict[str, Bubble] = {}
        self._locks: defaultdict = defaultdict(threading.Lock)
        self._timers: Dict[str, threading.Timer] = {}

        # Consumer queue + thread
        self._queue: queue.Queue = queue.Queue()
        self._consumer_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._start_consumer()

    # ── public API ─────────────────────────────────────────────────────────────

    def add(self, chat_id: str, msg: BubbleMessage) -> None:
        """Add a message to the chat's bubble. Schedules idle flush."""
        with self._lock_for(chat_id):
            bubble = self._bubbles.get(chat_id)
            if bubble is None:
                bubble = Bubble(chat_id=chat_id, started_at=time.time())
                self._bubbles[chat_id] = bubble

            bubble.messages.append(msg)

            # Media closes immediately — vision shouldn't wait for more text
            if msg.media_type:
                self._close_locked(chat_id, "media")
                return

            # Message cap
            if len(bubble.messages) >= self.max_messages:
                self._close_locked(chat_id, "max_messages")
                return

            # Max duration cap
            if (time.time() - bubble.started_at) * 1000 >= self.max_bubble_ms:
                self._close_locked(chat_id, "max_ms")
                return

            self._reschedule_idle_timer_locked(chat_id)

    def flush(self, chat_id: str, reason: str = "lifecycle") -> Optional[Bubble]:
        """Force-flush the open bubble for chat_id, if any. Returns the bubble
        for synchronous handlers that need it inline (e.g. /exit). Bubble is
        also enqueued to the consumer (which is idempotent for empty drain)."""
        with self._lock_for(chat_id):
            bubble = self._bubbles.get(chat_id)
            if bubble is None or not bubble.messages:
                return None
            return self._close_locked(chat_id, reason)

    def stop(self) -> None:
        """Stop consumer thread (test cleanup; not used in production)."""
        self._stop_event.set()
        for chat_id in list(self._timers.keys()):
            t = self._timers.pop(chat_id, None)
            if t is not None:
                t.cancel()
        if self._consumer_thread is not None:
            self._consumer_thread.join(timeout=2)

    # ── internals ──────────────────────────────────────────────────────────────

    def _lock_for(self, chat_id: str) -> threading.Lock:
        return self._locks[chat_id]

    def _reschedule_idle_timer_locked(self, chat_id: str) -> None:
        old = self._timers.pop(chat_id, None)
        if old is not None:
            old.cancel()
        t = threading.Timer(self.idle_ms / 1000.0, self._on_idle, args=(chat_id,))
        t.daemon = True
        self._timers[chat_id] = t
        t.start()

    def _on_idle(self, chat_id: str) -> None:
        with self._lock_for(chat_id):
            bubble = self._bubbles.get(chat_id)
            if bubble is None or not bubble.messages:
                return
            self._close_locked(chat_id, "idle")

    def _close_locked(self, chat_id: str, reason: str) -> Optional[Bubble]:
        """MUST be called while holding self._lock_for(chat_id)."""
        bubble = self._bubbles.pop(chat_id, None)
        timer = self._timers.pop(chat_id, None)
        if timer is not None:
            timer.cancel()
        if bubble is None or not bubble.messages:
            return None
        bubble.ended_at = time.time()
        bubble.closed_reason = reason
        self._queue.put(bubble)
        return bubble

    def _start_consumer(self) -> None:
        def _consume() -> None:
            while not self._stop_event.is_set():
                try:
                    bubble = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self.dispatch_fn(bubble)
                except Exception as exc:
                    try:
                        from agent.observability import track_silent
                        track_silent("bubble.dispatch_failed", exc, context={"chat_id": bubble.chat_id})
                    except Exception:
                        pass

        self._consumer_thread = threading.Thread(target=_consume, daemon=True, name="bubble-consumer")
        self._consumer_thread.start()

    # ── test helpers ───────────────────────────────────────────────────────────

    def _peek_open_bubble(self, chat_id: str) -> Optional[Bubble]:
        """Read-only inspection used by tests."""
        return self._bubbles.get(chat_id)
