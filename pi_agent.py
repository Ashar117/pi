"""
Pi Agent - Complete System
Claude as consciousness, tools as capabilities, self-evolution enabled
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# T-022: force UTF-8 stdout on Windows so box-drawing chars in the mode block
# don't crash on a default cp1252 shell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import json
import queue
import re
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

from app.config import (
    ANTHROPIC_API_KEY,
    GROQ_API_KEY,
    GEMINI_API_KEY,
    CEREBRAS_API_KEY,
    OPENROUTER_API_KEY,
    SUPABASE_URL,
    SUPABASE_KEY,
    GOD_SUPABASE_URL,
    GOD_SUPABASE_KEY,
    DEFAULT_MODE,
    OPENWEATHER_API_KEY,
    ALPHA_VANTAGE_KEY,
    NEWS_API_KEY,
)

import anthropic
from groq import Groq

# T-084 (R3): Groq exception imports removed — _respond_normie no longer
# catches them directly; LLMRouter raises a generic RuntimeError that the
# error-string classification handles.

from core.llm_router import LLMRouter, LLMResponse

from tools.tools_memory import MemoryTools
from tools.tools_execution import ExecutionTools
from tools.tools_awareness import AwarenessTools
from evolution import EvolutionTracker
from agent.health import run_health_check
from agent.review import check_monthly_review
from agent.truncation import (
    truncate_messages_safely, extract_text_from_messages,
    compress_messages_with_groq, CompressionFailed,
)
from agent.session import generate_session_summary, on_exit
from agent.tools import get_tool_definitions, execute_tool
from agent.prompt import build_system_prompt, build_system_prompt_split, minimal_consciousness
from agent.modes import detect_mode_switch, ModeConfig, get_mode_config
from agent.awareness_shortcut import try_answer_from_awareness
from agent.redaction import safe_error as _safe_error
from agent.observability import track_silent as _track_silent
from agent.cost_footer import emit_if_enabled as _emit_cost_footer

# T-082 step 9: agent/god.py was archived to docs/_archive/_private/agent_god_v1.py.
# God mode now flows through the unified _respond_via_config path with the
# ModeConfig from agent.modes; no separate import needed.
GOD_AVAILABLE = True  # availability is determined per-call by the LLM router

# F-007 TTS / F-006 Telegram / F-008 Scheduler — lazy, graceful no-op on missing deps
try:
    from tools.tools_tts import TTSTools as _TTSTools
    _tts_inst = _TTSTools()
except Exception:
    _tts_inst = None

try:
    from tools.tools_telegram import TelegramTools as _TelegramTools
except Exception:
    _TelegramTools = None

try:
    from tools.tools_scheduler import PiScheduler as _PiScheduler
except Exception:
    _PiScheduler = None

try:
    from agent.watchers import WatcherManager as _WatcherManager
except Exception:
    _WatcherManager = None


class PiAgent:
    """
    Pi as autonomous agent.
    
    Architecture:
    - Consciousness: System prompt defining intelligence
    - Tools: Memory, Execution, Web, Self-modification
    - Evolution: Learns from performance, improves self
    """
    
    def __init__(self):
        # Load consciousness
        consciousness_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "consciousness.txt")
        try:
            with open(consciousness_path, 'r') as f:
                self.consciousness = f.read()
        except FileNotFoundError:
            print(f"[Pi] WARNING: Consciousness file not found at {consciousness_path}")
            print("[Pi] Using minimal consciousness")
            self.consciousness = self._minimal_consciousness()
        
        # State — initialised early so subsystem setup can reference them
        self.mode = DEFAULT_MODE
        self.messages = []   # Persistent API message list (raw content blocks preserved)
        self.history = []    # Simplified string-only history for research mode context
        self.session_start = datetime.now(timezone.utc)
        self.session_id = uuid.uuid4().hex[:8]  # T-013: short ID for log correlation
        # T-037: populated when switching normie→root; injected once into first root prompt
        self._normie_handoff_context: str = ""

        # Initialize systems
        self.memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)
        # T-082: per-namespace MemoryTools cache. Public memory is the default
        # entry; private namespaces (god) are built on first access via
        # _get_memory_for_config(). Cached so a multi-turn god session reuses
        # one SQLite connection.
        self._memory_by_namespace: Dict[str, MemoryTools] = {"pi": self.memory}
        self.execution = ExecutionTools()
        self.evolution = EvolutionTracker()
        check_monthly_review(self.evolution)

        # Initialize LLM clients (legacy direct clients kept for compress_messages_with_groq)
        self.claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.groq = Groq(api_key=GROQ_API_KEY)

        # T-084 (R3): self.cerebras direct client removed. Normie + distillation
        # + briefing now go through self.router with tier='cheap', which routes
        # Cerebras → Groq → Gemini → OpenRouter — same Cerebras-primary
        # behavior, one failover code path, TPD-budget-aware brownout.

        # T-048: Multi-provider router — Claude primary, Groq fallback, Gemini tertiary
        self.router = LLMRouter(
            anthropic_key=ANTHROPIC_API_KEY or "",
            groq_key=GROQ_API_KEY or "",
            gemini_key=GEMINI_API_KEY or "",
            cerebras_key=CEREBRAS_API_KEY or "",
            openrouter_key=OPENROUTER_API_KEY or "",
        )

        # Awareness — fetch live world state once at startup, cache 30 min
        self.awareness = AwarenessTools(
            openweather_key=OPENWEATHER_API_KEY or "",
            alpha_vantage_key=ALPHA_VANTAGE_KEY or "",
            news_api_key=NEWS_API_KEY or "",
        )
        # T-041: Lazy awareness — snapshot loads on first access.
        # T-067: Background refresh — TTL expiry triggers a daemon thread refresh
        # so the next turn is never blocked waiting for weather/news/stocks APIs.
        self._awareness_snapshot_cache: Optional[str] = None
        self._awareness_refresh_ttl = 1500  # 25 min (< 30 min TTL so refresh races ahead)
        self._awareness_last_refresh: Optional[datetime] = None
        self._awareness_refreshing = False  # True while bg refresh is in flight
        self._awareness_refresh_lock = threading.Lock()
        self._awareness_refresh_failures = 0  # consecutive failure count for telemetry
        if "--eager-awareness" in sys.argv:
            self._awareness_snapshot_cache = self.awareness.get_awareness_snapshot()
            self._awareness_last_refresh = datetime.now(timezone.utc)

        # T-024: L1 thread UUID — deterministic from session_id; shared by auto-log and tool-path writes
        self.l1_thread_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, self.session_id))
        self.turn_number = 0
        # T-072: mid-session distillation — fires every N turns so memory doesn't
        # depend on a clean exit. Tracks the last turn we distilled up to so each
        # batch only sees new L1 rows.
        self._last_distilled_turn = 0
        self._distill_every_n_turns = 10

        # F-007 TTS — offline speech output
        self.tts = _tts_inst

        # F-006 Telegram bot — proxies messages to process_input(); None if token missing
        self.telegram = None
        if _TelegramTools is not None:
            self.telegram = _TelegramTools(agent=self)

        # F-008 Scheduler — background cron (daily briefing, L3 prune)
        self.scheduler = None
        if _PiScheduler is not None:
            self.scheduler = _PiScheduler(
                agent=self,
                tts=self.tts,
                telegram=self.telegram,
            )

        # Background watchers — Telegram alerts on file/schedule/url/keyword/price events
        self.watchers = None
        if _WatcherManager is not None:
            _tg_send = getattr(self.telegram, "send_message", None) if self.telegram else None
            self.watchers = _WatcherManager(telegram_send_fn=_tg_send)
            self.watchers.start()
        
        # T-062: Cache tool definitions once at construction — avoid re-serialising
        # 60KB of JSON on every root-mode turn.
        self._tool_defs_cache: Optional[List[Dict]] = None

        # T-068: Async logging — background thread drains the queue so turn
        # logging (Supabase + file writes) never blocks the response path.
        # Bounded so a Supabase outage can't grow the queue unbounded; on
        # overflow we drop the OLDEST entry (loss-tolerant: logging is
        # observability, not correctness).
        self._log_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._log_queue_dropped = 0  # telemetry: count of dropped log entries
        self._log_stop = threading.Event()
        self._log_thread = threading.Thread(target=self._log_worker, daemon=True)
        self._log_thread.start()

        # T-041: Silent init — only health-check failures surface. Pass
        # --verbose-init for the legacy multi-line startup.
        # T-075: Background the health check so the Supabase import chain
        # (~3-5s on cold Win+Py3.13) doesn't block the startup banner.
        # Failures still print to stderr — they just don't gate first-paint.
        _verbose_init = "--verbose-init" in sys.argv
        def _bg_health_check():
            try:
                run_health_check(
                    self.memory.supabase, self.memory.sqlite_path,
                    ANTHROPIC_API_KEY, GROQ_API_KEY, SUPABASE_KEY,
                    verbose=_verbose_init,
                )
            except Exception as e:
                print(f"[Pi] health check error: {e}", file=sys.stderr, flush=True)
        if _verbose_init:
            _bg_health_check()  # verbose mode keeps synchronous output ordering
        else:
            threading.Thread(target=_bg_health_check, daemon=True).start()
        if "--verbose-init" in sys.argv:
            print(f"[Pi] Agent initialized - {self.session_start.strftime('%Y-%m-%d %H:%M')}")
            print(f"[Pi] Session ID: {self.session_id}")
            print(f"[Pi] Mode: {self.mode}")
            print(f"[Pi] Consciousness loaded: {len(self.consciousness)} chars")

    @property
    def awareness_snapshot(self) -> str:
        """Lazy + background-refreshed awareness snapshot (T-041, T-067).

        First call: loads synchronously (unavoidable; guarded by --eager-awareness).
        Subsequent calls: serves cached value; triggers a background refresh if
        the TTL is approaching so the NEXT call is never blocked.
        """
        now = datetime.now(timezone.utc)

        if self._awareness_snapshot_cache is None:
            # First ever load — synchronous, unavoidable
            self._awareness_snapshot_cache = self.awareness.get_awareness_snapshot()
            self._awareness_last_refresh = now
            return self._awareness_snapshot_cache

        # Check if TTL is due; if so, kick off a background refresh (non-blocking).
        # Lock guards the check-and-set so two callers can't both spawn refresh threads.
        age_s = (now - self._awareness_last_refresh).total_seconds() if self._awareness_last_refresh else 9999
        if age_s >= self._awareness_refresh_ttl:
            def _refresh():
                try:
                    new_snap = self.awareness.get_awareness_snapshot(force=True)
                    self._awareness_snapshot_cache = new_snap
                    self._awareness_last_refresh = datetime.now(timezone.utc)
                    self._awareness_refresh_failures = 0
                except Exception as e:
                    # Don't silently rot — track failure count + log to stderr
                    # (daemon redirects stderr to logs/daemon.log).
                    self._awareness_refresh_failures += 1
                    if self._awareness_refresh_failures in (1, 3, 10):
                        print(
                            f"[Pi] awareness bg refresh failed ({self._awareness_refresh_failures}x): {e}",
                            file=sys.stderr, flush=True,
                        )
                finally:
                    self._awareness_refreshing = False

            with self._awareness_refresh_lock:
                if not self._awareness_refreshing:
                    self._awareness_refreshing = True
                    threading.Thread(target=_refresh, daemon=True).start()

        return self._awareness_snapshot_cache

    @awareness_snapshot.setter
    def awareness_snapshot(self, value: str) -> None:
        """Allow tools (refresh_awareness) to overwrite the cache."""
        self._awareness_snapshot_cache = value
        self._awareness_last_refresh = datetime.now(timezone.utc)

    def _minimal_consciousness(self) -> str:
        """Thin wrapper preserving the method API; logic in agent.prompt."""
        return minimal_consciousness()
    
    def _get_system_prompt(self) -> str:
        """Single-string system prompt — used by normie and god modes."""
        base = build_system_prompt(self.consciousness, self.mode, self.memory)
        if self.awareness_snapshot:
            return base + "\n\n" + self.awareness_snapshot
        return base

    def _get_system_prompt_split(self) -> tuple:
        """Return (static, warm, dynamic) for Anthropic prompt caching (T-091).

        static  — consciousness + mode block; cached for hours.
        warm    — L3 ambient context; cached for minutes.
        dynamic — timestamp + awareness; changes each turn.
        """
        static, warm, dynamic = build_system_prompt_split(self.consciousness, self.mode, self.memory)
        if self.awareness_snapshot:
            dynamic = dynamic + "\n\n" + self.awareness_snapshot
        return static, warm, dynamic

    def _get_tool_definitions(self) -> List[Dict]:
        """Return tool definitions, cached after first build (T-062)."""
        if self._tool_defs_cache is None:
            self._tool_defs_cache = get_tool_definitions()
        return self._tool_defs_cache
    
    def _log_worker(self) -> None:
        """Background thread — drains _log_queue and executes each log call (T-068)."""
        while not self._log_stop.is_set():
            try:
                fn, args, kwargs = self._log_queue.get(timeout=1.0)
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    _track_silent("logs.async_worker", e)
                finally:
                    self._log_queue.task_done()
            except queue.Empty:
                continue

    def _save_dropped_log(self, fn, _args, kwargs) -> None:
        """T-090: best-effort save of a dropped log_turn entry to logs/dropped_turns.jsonl.

        Only persists mem.log_turn calls — those are the L1 rows that feed
        distillation. evolution.log_interaction drops are observable but not
        recoverable via distillation, so skipped here.
        """
        try:
            if getattr(fn, "__name__", "") != "log_turn":
                return
            entry = {"ts": datetime.now(timezone.utc).isoformat(), "fn": "log_turn", **kwargs}
            path = Path(__file__).parent / "logs" / "dropped_turns.jsonl"
            with open(str(path), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass

    def _async_log(self, fn, *args, **kwargs) -> None:
        """Enqueue a logging call so it never blocks the response path (T-068).

        On overflow, drop the OLDEST item and enqueue the new one. Logging is
        observability — losing the stalest entries is better than blocking the
        producer or growing memory without bound.

        T-090: dropped log_turn entries are saved to logs/dropped_turns.jsonl
        as a local fallback so distillation can recover them after Supabase reconnect.
        """
        try:
            self._log_queue.put_nowait((fn, args, kwargs))
        except queue.Full:
            try:
                dropped_fn, dropped_args, dropped_kwargs = self._log_queue.get_nowait()
                self._log_queue.task_done()
                self._log_queue_dropped += 1
                self._save_dropped_log(dropped_fn, dropped_args, dropped_kwargs)
            except queue.Empty:
                pass
            try:
                self._log_queue.put_nowait((fn, args, kwargs))
            except queue.Full:
                self._log_queue_dropped += 1
                self._save_dropped_log(fn, args, kwargs)  # new entry also lost

    def flush_logs(self, timeout: float = 5.0) -> bool:
        """Drain the async log queue. Call on clean shutdown so pending Supabase
        writes complete before the process exits. Returns True if drained cleanly."""
        try:
            deadline = time.monotonic() + timeout
            while not self._log_queue.empty() and time.monotonic() < deadline:
                time.sleep(0.05)
            return self._log_queue.empty()
        except Exception:
            return False

    def _calculate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Calculate Claude Sonnet 4.6 API cost"""
        return (tokens_in / 1_000_000 * 0.80) + (tokens_out / 1_000_000 * 4.00)

    def _execute_tool(self, tool_name: str, tool_input: Dict, *, memory_override=None) -> Any:
        """Thin wrapper preserving the method API; logic in agent.tools."""
        return execute_tool(self, tool_name, tool_input, memory_override=memory_override)
    
    def process_input(self, user_input: str) -> str:
        """Main entry point — wraps inner dispatch with universal turn logging.

        T-039: Every turn (both modes, all return paths) is appended to
        logs/turns.jsonl as a durable local record. Never gates on mode.
        """
        from agent.turn_log import append_turn

        start_ts = datetime.now(timezone.utc)
        error_str = None

        try:
            response = self._process_input_inner(user_input)
        except Exception as e:
            _track_silent("agent.process_input", e)
            error_str = str(e)
            response = f"[Pi] Internal error: {_safe_error(e, audience='user')}"

        duration_ms = int((datetime.now(timezone.utc) - start_ts).total_seconds() * 1000)

        try:
            append_turn(
                session_id=self.session_id,
                mode=self.mode,
                user_input=user_input,
                response=response,
                duration_ms=duration_ms,
                tools_used=[],
                cost=0.0,
                model="",
                error=error_str,
            )
        except Exception as e:
            _track_silent("logs.turn_append", e)

        # T-072: mid-session L1->L2 distillation. Fires every N turns so memory
        # survives a crash or rate-limit at exit. Runs in background so latency
        # is invisible to the user.
        self._maybe_mid_session_distill()

        return response

    def _maybe_mid_session_distill(self) -> None:
        """T-072: Trigger a background distill if enough new turns accumulated.

        Only processes L1 rows from turns AFTER ``_last_distilled_turn`` so each
        batch is a strict delta. L2 dedup in memory_write handles the rare case
        where a fact appears in two overlapping batches.
        """
        new_turns = self.turn_number - self._last_distilled_turn
        if new_turns < self._distill_every_n_turns:
            return

        from_turn = self._last_distilled_turn
        to_turn = self.turn_number
        self._last_distilled_turn = to_turn  # claim the batch before launching

        def _run():
            try:
                from memory.pipeline import distill_session
                # Fetch full thread, then filter to the new range
                all_rows = self.memory.get_l1_thread(self.l1_thread_id)
                batch = [
                    r for r in all_rows
                    if from_turn < (r.get("metadata") or {}).get("turn", 0) <= to_turn
                ]
                if not batch:
                    return
                distill_session(
                    thread_id=self.l1_thread_id,
                    session_id=self.session_id,
                    memory_tools=self.memory,
                    router=self.router,  # T-084: tier='cheap' routing inside distill_session
                    rows=batch,
                )
                # T-085 R4 step 5: promote L2 facts written by the distill above
                # into L3 ambient context immediately. Was exit-only; mid-session
                # promotion means a high-importance fact becomes visible to the
                # planner within ~10 turns instead of next-session. S-054
                # invalid_at makes re-promotion idempotent.
                try:
                    self.memory.promote_l2_to_l3(importance_threshold=8)
                except Exception as e:
                    print(f"[Memory] mid-session L2->L3 promote failed (non-fatal): {e}")
                # T-085 R4 step 6: refresh the Foam-visible vault projection so
                # entity hubs + per-fact .md files reflect this batch's writes
                # without waiting for exit. Cost is bounded (L2 row count is
                # small); sync_vault is rewrite-the-files idempotent.
                try:
                    from tools.tools_obsidian import sync_vault
                    sync_vault(self.memory)
                except Exception as e:
                    print(f"[Vault] mid-session sync failed (non-fatal): {e}")
            except Exception as e:
                print(f"[Distill] Mid-session distill failed (non-fatal): {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _process_input_inner(self, user_input: str) -> str:
        """Inner dispatch — Claude decides, tools execute, evolution tracks.

        Wrapped by ``process_input`` so every turn is logged. Do not call this
        directly from outside the class — go through ``process_input``.
        """

        # Mode-switch detection (loose matcher, S-010/T-015) — never clear self.messages,
        # session context must survive mode changes (L-001).
        switch = detect_mode_switch(user_input)
        if switch is not None:
            prev_mode = self.mode
            self.mode, response = switch
            # T-037 / R8 (ADR-004): on mode switch, if the OUTGOING mode is
            # marked builds_handoff_on_exit AND the INCOMING mode is marked
            # consumes_handoff_on_first_turn, snapshot the conversation so the
            # new mode gets framed context instead of starting cold. Today
            # that's only normie→root; the config-driven check makes the
            # pattern generalize to any future mode pair.
            prev_cfg = get_mode_config(prev_mode)
            new_cfg = get_mode_config(self.mode)
            if (prev_cfg.builds_handoff_on_exit
                    and new_cfg.consumes_handoff_on_first_turn
                    and self.messages):
                self._normie_handoff_context = self._build_normie_handoff()
                self._archive_normie_session_to_vault()
            # T-082: god→other transitions used to call GodMemory.sync_to_vault().
            # That god-specific vault writer lived inside the archived agent/god.py.
            # The auto-sync was dropped intentionally — god_memory.db remains
            # the source of truth, vault/.god/ is a stale projection. If a
            # ModeConfig-aware vault writer is wanted, file a new ticket.
            return response

        # God mode activation (handled separately — not in detect_mode_switch so
        # the trigger stays out of committed modes.py).
        if user_input.lower().strip() in ("god mode", "god"):
            ok, reason = self._check_god_mode_available()
            if not ok:
                return f"[Pi] God mode unavailable: {reason}"
            self.mode = "god"
            return "God mode active (private, no restrictions)"

        cmd = user_input.lower().strip()

        if cmd == "analyze performance":
            return self._performance_report()
        elif cmd in ("voice", "voice ptt", "voice vad", "voice wake"):
            voice_mode = "ptt"
            if "vad" in cmd:
                voice_mode = "vad"
            elif "wake" in cmd:
                voice_mode = "wake"
            try:
                from agent.voice_loop import VoiceLoop
                vl = VoiceLoop(agent=self, mode=voice_mode)
                vl.run()
            except Exception as e:
                return f"[Voice] Failed to start: {e}"
            return ""
        elif cmd == "help":
            return (
                "Commands:\n"
                "  root mode        — switch to Claude (full tools)\n"
                "  normie mode      — switch to Groq (fast, no tools)\n"
                "  god mode         — private uncensored layer (gitignored)\n"
                "  research mode    — 3-agent debate on a question\n"
                "  voice            — start voice mode (PTT by default)\n"
                "  voice vad        — voice mode with voice-activity detection\n"
                "  voice wake       — voice mode with wake-word detection\n"
                "  briefing         — full daily briefing (weather/news/markets/HN/research)\n"
                "  analyze performance  — last 7-day performance report\n"
                "  exit             — save session and quit"
            )
        elif cmd == "research mode":
            print("\n" + "="*60)
            print("  PI RESEARCH MODE - 3-Agent Debate")
            print("="*60)
            question = input("Research question: ").strip()
            if question:
                from core.research_mode import run_research_mode
                context_str = "\n".join([
                    f"{h['role'].upper()}: {h['content'][:200]}"
                    for h in self.history[-10:]
                    if h["role"] in ("user", "assistant")
                ])
                synthesis = run_research_mode(question, rounds=2, context=context_str)
                if synthesis:
                    self.memory.memory_write(
                        content=f"Research ({datetime.now(timezone.utc).strftime('%Y-%m-%d')}): {question[:80]} | {synthesis[:300]}",
                        tier="l3",
                        importance=5,
                        category="research_results"
                    )
                    print("[Research] Results saved to memory")
                return "[Research complete. Continue conversation or 'exit']"
            return "[No research question provided]"
        elif cmd == "exit":
            return "EXIT"

        elif any(kw in cmd for kw in ("briefing", "morning briefing", "daily briefing", "what's today", "whats today")):
            from tools.tools_briefing import BriefingTools
            from tools.tools_obsidian import ObsidianTools
            from tools.tools_calendar import CalendarTools
            briefing = BriefingTools(
                awareness=self.awareness,
                memory=self.memory,
                obsidian=ObsidianTools(),
                calendar=CalendarTools(),
            )
            return briefing.generate(save_to_obsidian=True)

        # File attachment detection — if message contains a file path, inject context
        user_input = self._preprocess_file_refs(user_input)

        # Daily budget enforcement
        from app.config import DAILY_COST_LIMIT
        daily_cost = self.evolution.get_daily_cost()
        if daily_cost >= DAILY_COST_LIMIT and self.mode == "root":
            print(f"[Pi] Daily cost limit reached (${daily_cost:.4f}). Switching to normie.")
            self.mode = "normie"

        # T-089 R8 Stage C: all modes route through _respond_via_config.
        if os.environ.get("PI_GOD_LEGACY") == "1":
            print(
                "[Pi] PI_GOD_LEGACY=1 is deprecated since T-082. Unset it.",
                file=sys.stderr, flush=True,
            )

        interaction_start = datetime.now(timezone.utc)

        try:
            return self._respond_via_config(
                user_input, interaction_start, get_mode_config(self.mode)
            )
        except Exception as e:
            self.evolution.log_interaction(
                user_input=user_input,
                pi_response=f"Error: {str(e)}",
                tool_calls=[],
                success=False,
                mode=self.mode,
                metadata={"error": str(e)},
            )
            return f"[Pi] Error: {str(e)}"

    def _prefetch_memory(self, user_input: str) -> str:
        """Extract a keyword from user_input and search L3+L2 memory.

        Only fires on recall questions — statements of fact and small talk are
        skipped entirely to avoid spurious searches ("followed", "planning", etc.).
        Returns a formatted block to append to the system prompt, or "".
        Best-effort — never raises.
        """
        try:
            lower = user_input.lower().rstrip(" ?.")

            # Step 1: only prefetch when the message is a recall question.
            RECALL_SIGNALS = {
                "what", "which", "remind", "tell me", "do i", "have i",
                "show me", "when is", "where is", "who is", "my ",
            }
            is_recall = (
                user_input.rstrip().endswith("?")
                or any(sig in lower for sig in RECALL_SIGNALS)
            )
            if not is_recall:
                return ""

            # Step 2: extract the most specific non-filler keyword.
            stop_words = {
                "the", "a", "an", "is", "are", "was", "were", "be", "been",
                "have", "has", "had", "do", "does", "did", "will", "would",
                "can", "could", "should", "may", "might", "shall", "must",
                "and", "or", "but", "if", "in", "on", "at", "to", "for",
                "of", "with", "by", "from", "up", "about", "into", "what",
                "how", "when", "where", "who", "why", "that", "this", "it",
                "i", "you", "we", "me", "my", "your", "our", "please", "just",
                "like", "so", "then", "now", "its", "as", "not", "no", "all",
                # T-027: reject generic nouns that never produce useful hits
                "location", "things", "stuff", "planning", "going", "good",
                "great", "okay", "sure", "yeah", "tell", "remind", "show",
                "have", "give", "know", "need", "want", "think", "said",
            }
            words = re.findall(r"\b[a-zA-Z]{4,}\b", user_input.lower())
            keywords = [w for w in words if w not in stop_words]
            if not keywords:
                return ""

            # Step 3 (T-151): semantic search first — it handles paraphrase and
            # multi-concept questions far better than a single-keyword lexical
            # lookup. Query the whole keyword phrase, not just keywords[0].
            phrase = " ".join(keywords[:6])
            label = phrase
            try:
                hits = self.memory.memory_search_semantic(query=phrase, limit=4) or []
            except Exception:
                hits = []

            # Fallback: lexical lookup on the top few keywords, merged + deduped.
            # Covers the cases semantic can't (no GEMINI key / no embeddings /
            # private namespace), which all return [] gracefully.
            if not hits:
                seen = set()
                merged: List[Dict] = []
                for kw in keywords[:3]:
                    q = kw[:-1] if (kw.endswith("s") and len(kw) > 4) else kw
                    for h in (self.memory.memory_read(query=q, limit=4) or []):
                        key = h.get("id") or (h.get("content") or "")[:80]
                        if key in seen:
                            continue
                        seen.add(key)
                        merged.append(h)
                hits = merged[:4]
                label = ", ".join(keywords[:3])

            if not hits:
                return ""
            lines = [f"[PREFETCH: '{label}']"]
            for h in hits[:4]:
                tier_label = (h.get("tier") or "L2").upper()
                content = (h.get("content") or "")[:200]
                lines.append(f"  [{tier_label}] {content}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _build_assistant_content(self, resp: LLMResponse) -> list:
        """Convert LLMResponse into Anthropic canonical assistant content list."""
        content = []
        if resp.text:
            content.append({"type": "text", "text": resp.text})
        for tc in resp.tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        return content or [{"type": "text", "text": ""}]

    def _truncate_messages_safely(self, max_messages: int = 20):
        """Compress or hard-truncate message history depending on length.

        At 30+ messages, Groq summarises the oldest half into a context block
        (free, zero Claude cost) so long sessions don't lose earlier context.
        On CompressionFailed (both LLMs down), hard-truncate to guarantee progress.
        Below the threshold, use safe hard-truncation logic.
        """
        if len(self.messages) >= 30:
            try:
                self.messages = compress_messages_with_groq(
                    self.messages, self.groq, threshold=30, keep_recent=12,
                    anthropic_client=self.claude,
                )
            except CompressionFailed as cf:
                _track_silent("compression.both_llms_failed", cf)
                self.messages = truncate_messages_safely(cf.original_messages, max_messages)
        else:
            self.messages = truncate_messages_safely(self.messages, max_messages)

    def _extract_text_from_messages(self, n: int = 10) -> str:
        """Thin wrapper preserving the method API; logic in agent.truncation."""
        return extract_text_from_messages(self.messages, n)

    def _archive_normie_session_to_vault(self) -> None:
        """T-037: Write the current normie messages to vault/notes/sessions/ as markdown.
        Non-fatal — vault archival failure must not block the mode switch.
        """
        try:
            from tools.tools_obsidian import _atomic_write, _project_root
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
            vault = os.path.join(_project_root(), "vault", "notes", "sessions")
            os.makedirs(vault, exist_ok=True)
            path = os.path.join(vault, f"{ts}-normie.md")
            lines = [f"# Groq Session — {ts}", ""]
            for msg in self.messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    content = " ".join(text_parts).strip()
                if content:
                    label = "**Ash**" if role == "user" else "**Pi (Groq)**"
                    lines.append(f"{label}: {content}")
                    lines.append("")
            _atomic_write(path, "\n".join(lines))
        except Exception as e:
            print(f"[Vault] normie session archive failed (non-fatal): {e}")

    def _build_normie_handoff(self) -> str:
        """T-037: Build a GROQ SESSION HANDOFF block from self.messages for injection
        into the first root-mode system prompt after a normie→root switch.

        Extracts the last ≤12 message turns (user+assistant pairs), skips tool-use
        content blocks, and formats them as a plain-text summary so Claude understands
        the prior Groq conversation without replaying it message-by-message.
        """
        turns = []
        for msg in self.messages[-24:]:  # up to 12 pairs
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Extract text blocks only; skip tool_use/tool_result blocks
                text_parts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = " ".join(text_parts).strip()
            if not content:
                continue
            label = "Ash" if role == "user" else "Pi (Groq)"
            turns.append(f"{label}: {content[:300]}")

        if not turns:
            return ""

        lines = [
            "=== GROQ SESSION HANDOFF ===",
            "The following conversation happened in normie mode (Groq) before you took over.",
            "You are now Pi in root mode (Claude). Continue naturally — no need to re-introduce yourself.",
            "",
        ] + turns + ["=== END HANDOFF ==="]
        return "\n".join(lines)

    # ── T-089 R8: unified response path — all modes ────────────────────────────

    def _check_god_mode_available(self) -> tuple:
        """T-108: preflight before switching to god mode.

        Returns (True, "") if all conditions pass, or (False, reason) on the
        first failing condition. Order: DB file, URL set, URL inequality,
        module importable.
        """
        from agent.modes import get_mode_config
        cfg = get_mode_config("god")
        db_rel = cfg.memory_db  # "data/god_memory.db"
        db_path = Path(__file__).parent / db_rel if db_rel else None
        if db_path is None or not db_path.exists():
            return False, f"god_memory.db not found at {db_path}"

        god_url = os.environ.get("GOD_SUPABASE_URL", "")
        if not god_url:
            return False, "GOD_SUPABASE_URL not set"

        pub_url = os.environ.get("SUPABASE_URL", "")
        if god_url == pub_url:
            return False, "GOD_SUPABASE_URL == SUPABASE_URL (would leak)"

        try:
            import importlib
            importlib.import_module("agent.god")
        except ImportError:
            pass  # module was archived — not required for god mode to function

        return True, ""

    def _get_memory_for_config(self, cfg: ModeConfig):
        """Return the MemoryTools instance for cfg.memory_namespace.

        Public namespace returns self.memory. Private namespaces lazily build
        a MemoryTools(supabase_url="", db_path=cfg.memory_db, namespace=ns)
        instance and cache it so repeated turns share one SQLite connection.
        """
        ns = cfg.memory_namespace
        cached = self._memory_by_namespace.get(ns)
        if cached is not None:
            return cached
        if cfg.memory_db is None:
            mem = self.memory
        else:
            # T-095: use separate private Supabase project when creds are set.
            # Fail loud if someone accidentally points god at the public project.
            god_url = GOD_SUPABASE_URL or ""
            god_key = GOD_SUPABASE_KEY or ""
            if god_url and god_url == (SUPABASE_URL or ""):
                raise RuntimeError(
                    "GOD_SUPABASE_URL must not equal SUPABASE_URL — "
                    "god memory requires a separate private Supabase project."
                )
            mem = MemoryTools(
                supabase_url=god_url,
                supabase_key=god_key,
                db_path=cfg.memory_db,
                namespace=ns,
            )
        self._memory_by_namespace[ns] = mem
        return mem

    def _load_mode_prompt(self, cfg: ModeConfig) -> str:
        """Load cfg.prompt_path; fall back to self.consciousness if missing."""
        path = cfg.prompt_path
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return self.consciousness

    def _filtered_tool_defs(self, cfg: ModeConfig) -> List[Dict]:
        """Return tool defs filtered by cfg.tool_allowlist.

        None = all tools; () = no tools; non-empty tuple = whitelist.
        """
        if not cfg.supports_tools:
            return []
        defs = self._get_tool_definitions()
        if cfg.tool_allowlist is None:
            return defs
        allowed = set(cfg.tool_allowlist)
        return [d for d in defs if d.get("name") in allowed]

    def _respond_via_config(
        self,
        user_input: str,
        interaction_start,
        cfg: ModeConfig,
    ) -> str:
        """Single response path for all modes (T-089 R8 Stage B+C).

        Dispatched for root, normie, and god. Behavior is driven entirely by
        ModeConfig flags — no per-mode branches inside this method.

        Privacy invariant: god (public_logging=False) skips both the public
        evolution log and the public raw_wiki write. Explicit memory_write
        tool calls during the turn already persist to god_memory.db via the
        memory_override, matching agent/god.py parity ("god is a sink").
        """
        mem = self._get_memory_for_config(cfg)
        allowed_tools = self._filtered_tool_defs(cfg)

        # ── 1. Awareness shortcut ──────────────────────────────────────
        if cfg.awareness_shortcut:
            shortcut = try_answer_from_awareness(user_input, self.awareness_snapshot)
            if shortcut:
                duration = (datetime.now(timezone.utc) - interaction_start).total_seconds()
                self.messages.append({"role": "user", "content": user_input})
                self.messages.append({"role": "assistant", "content": shortcut})
                self.history.append({"role": "user", "content": user_input})
                self.history.append({"role": "assistant", "content": shortcut})
                self.turn_number += 1
                if cfg.public_logging:
                    self._async_log(
                        self.evolution.log_interaction,
                        user_input=user_input, pi_response=shortcut, tool_calls=[],
                        success=True, mode=cfg.name, cost=0.0, model="shortcut",
                        tokens_in=0, tokens_out=0,
                        metadata={"duration_seconds": duration,
                                  "session_id": self.session_id, "shortcircuit": True},
                    )
                    self._async_log(
                        mem.log_turn,
                        thread_id=self.l1_thread_id, session_id=self.session_id,
                        turn_number=self.turn_number, user_content=user_input,
                        assistant_content=shortcut, mode=cfg.name,
                    )
                return shortcut

        # ── 2. System prompt ───────────────────────────────────────────
        if cfg.use_split_prompt:
            # Root + normie: static/dynamic split (T-061). Non-Anthropic providers
            # receive the flattened string; only Anthropic gets the cache tuple.
            static_p, warm_p, dynamic_p = self._get_system_prompt_split()
            if cfg.consumes_handoff_on_first_turn and self._normie_handoff_context:
                dynamic_p = dynamic_p + "\n\n" + self._normie_handoff_context
                self._normie_handoff_context = ""
            if cfg.prefetch_memory:
                prefetch = self._prefetch_memory(user_input)
                if prefetch:
                    dynamic_p = dynamic_p + "\n\n" + prefetch
            if cfg.session_ctx_inject:
                session_ctx = self._extract_text_from_messages(n=10)
                if session_ctx:
                    dynamic_p += (
                        f"\n\nSESSION CONTEXT (read-only, from this conversation):\n{session_ctx}"
                    )
            system_prompt = (static_p, warm_p, dynamic_p)
        else:
            # Normie / god: single string prompt
            system_prompt = self._load_mode_prompt(cfg)
            l3_ctx = mem.get_l3_context(max_tokens=600)
            if l3_ctx:
                system_prompt = system_prompt + "\n\n" + l3_ctx
            if cfg.session_ctx_inject:
                session_ctx = self._extract_text_from_messages(n=10)
                if session_ctx:
                    system_prompt += (
                        f"\n\nSESSION CONTEXT (read-only, from this conversation):\n{session_ctx}"
                    )

        # ── 3. Message history & truncation ───────────────────────────
        self.messages.append({"role": "user", "content": user_input})
        self._truncate_messages_safely(20)
        if cfg.ctx_message_window is not None:
            # T-149: normie sends a real bounded multi-turn history (both sides),
            # safe-sliced so the window never starts mid tool-pair. Providers
            # flatten dict content via anthropic_messages_to_openai.
            llm_messages = truncate_messages_safely(
                self.messages, max_messages=cfg.ctx_message_window
            )
        elif cfg.single_message_ctx:  # DEPRECATED path, kept for back-compat
            llm_messages = [{"role": "user", "content": user_input}]
        else:
            llm_messages = self.messages

        # ── 4. First LLM call ─────────────────────────────────────────
        tool_calls_made: List[Dict] = []
        l1_tool_records: List[Dict] = []

        try:
            resp = self.router.chat(
                messages=llm_messages,
                system=system_prompt,
                tools=allowed_tools,
                max_tokens=cfg.max_tokens,
                tier=cfg.router_tier,
            )
        except RuntimeError as e:
            err_str = str(e).lower()
            if "rate" in err_str or "429" in err_str:
                content = (
                    "Hit the daily free-tier limit on normie mode. "
                    "Switch to root mode, or check back in an hour."
                )
            elif "api" in err_str or "status" in err_str:
                content = "Something went wrong on my end — try again in a moment."
            else:
                content = "Couldn't reach my language model — try again in a moment."
            print(f"[Pi] {cfg.name} router error: {e}", flush=True)
            self.messages.append({"role": "assistant", "content": content})
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": content})
            self.turn_number += 1
            duration = (datetime.now(timezone.utc) - interaction_start).total_seconds()
            if cfg.public_logging:
                self._async_log(
                    self.evolution.log_interaction,
                    user_input=user_input, pi_response=content, tool_calls=[],
                    success=False, mode=cfg.name, cost=0.0, model="error",
                    tokens_in=0, tokens_out=0,
                    metadata={"duration_seconds": duration,
                              "session_id": self.session_id,
                              "error": _safe_error(e, audience="public_log")},
                )
            return content

        self.messages.append({"role": "assistant",
                              "content": self._build_assistant_content(resp)})
        t_in = resp.tokens_in
        t_out = resp.tokens_out

        # ── 5. Agentic tool loop ───────────────────────────────────────
        while resp.stop_reason == "tool_use" and cfg.supports_tools:
            tool_results = []
            for tc in resp.tool_calls:
                result = self._execute_tool(tc.name, tc.input, memory_override=mem)
                tool_calls_made.append({"id": tc.id, "name": tc.name, "input": tc.input})
                l1_tool_records.append({
                    "name": tc.name,
                    "input": dict(tc.input),
                    "result_summary": str(result)[:500],
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(result) if not isinstance(result, str) else result,
                })

            self.messages.append({"role": "user", "content": tool_results})

            resp = self.router.chat(
                messages=self.messages,
                system=system_prompt,
                tools=allowed_tools,
                max_tokens=cfg.max_tokens,
                tier=cfg.router_tier,
            )

            self.messages.append({"role": "assistant",
                                  "content": self._build_assistant_content(resp)})
            t_in += resp.tokens_in
            t_out += resp.tokens_out

        # ── 6. Finalise turn ───────────────────────────────────────────
        final_text = resp.text
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": final_text})

        duration_s = (datetime.now(timezone.utc) - interaction_start).total_seconds()
        self.turn_number += 1

        if cfg.public_logging:
            total_cost = self._calculate_cost(t_in, t_out)
            # T-130: optional inline cost footer (PI_SHOW_COST=on). stderr only;
            # never enters final_text so Telegram/voice paths stay clean.
            # Gated by cfg.public_logging — god mode (privacy invariant 2) never emits.
            _emit_cost_footer(
                total_cost, t_in, t_out,
                f"{resp.provider}/{resp.model}", duration_s,
            )
            self._async_log(
                self.evolution.log_interaction,
                user_input=user_input,
                pi_response=final_text,
                tool_calls=tool_calls_made,
                success=True,
                mode=cfg.name,
                cost=total_cost,
                model=f"{resp.provider}/{resp.model}",
                tokens_in=t_in,
                tokens_out=t_out,
                metadata={"duration_seconds": duration_s, "session_id": self.session_id},
            )
            self._async_log(
                mem.log_turn,
                thread_id=self.l1_thread_id,
                session_id=self.session_id,
                turn_number=self.turn_number,
                user_content=user_input,
                assistant_content=final_text,
                mode=cfg.name,
                tool_calls=l1_tool_records,
                tokens_in=t_in,
                tokens_out=t_out,
                cost=total_cost,
            )

        return final_text

    def _performance_report(self) -> str:
        """Generate performance report"""
        
        analysis = self.evolution.analyze_performance(days=7)
        
        if "error" in analysis:
            return f"[Pi] {analysis['error']}"
        
        report = f"""
=== PI PERFORMANCE REPORT (Last 7 Days) ===

Total interactions: {analysis['total_interactions']}
Success rate: {analysis['success_rate']:.1%}
Successful: {analysis['successful']}
Failed: {analysis['failed']}

Mode usage:
{json.dumps(analysis['mode_usage'], indent=2)}

Tool usage:
{json.dumps(analysis['tool_usage'], indent=2)}

Tool success rates:
{json.dumps(analysis['tool_success_rates'], indent=2)}

Failed by model:
{json.dumps(analysis.get('failed_by_model', {}), indent=2)}
"""
        
        # Identify improvements
        improvements = self.evolution.identify_improvements(analysis)
        
        if improvements:
            report += "\n=== SUGGESTED IMPROVEMENTS ===\n"
            for imp in improvements:
                report += f"\n{imp['severity'].upper()}: {imp['issue']}\n"
                report += f"Suggestion: {imp['suggestion']}\n"
        
        return report
    
    def _generate_session_summary(self) -> str:
        """Thin wrapper preserving the method API; logic in agent.session."""
        return generate_session_summary(self.groq, self.messages, self.history, n=12)

    def _daily_briefing(self) -> str:
        """Generate a startup briefing from L3 context + recent session stats.

        Runs once at startup via run(). Best-effort — never raises.
        """
        try:
            today = datetime.now(timezone.utc).strftime("%A, %Y-%m-%d")
            recent = self.evolution.get_recent_interactions(hours=24)
            sessions_today = len({
                i.get("metadata", {}).get("session_id", "")
                for i in recent if i.get("metadata", {}).get("session_id")
            })
            cost_today = sum(i.get("cost", 0) for i in recent)

            # Pull top L3 facts (already cached locally — no network)
            l3_ctx = self.memory.get_l3_context(max_tokens=400)

            # Count open tickets
            from pathlib import Path
            open_tickets = len(list(
                (Path(__file__).parent / "tickets" / "open").glob("*.json")
            ))

            lines = [
                f"=== Pi Briefing — {today} ===",
                f"Sessions today: {sessions_today}  |  Cost today: ${cost_today:.4f}  |  Open tickets: {open_tickets}",
                f"Mode: {self.mode}",
            ]
            if l3_ctx.strip():
                lines.append("")
                lines.append(l3_ctx.strip())
            lines.append("=" * 40)
            return "\n".join(lines)
        except Exception:
            return ""

    def _preprocess_file_refs(self, user_input: str) -> str:
        """
        Detect file paths in the user message and append a media-awareness hint
        so the LLM knows which tools to call. Does NOT process the file itself —
        just signals presence so Claude knows to use read_document / analyze_image etc.

        Supports:
          - Windows: C:\\...\\file.ext or E:\\...\\file.ext
          - Unix:    /path/to/file.ext
          - Relative: ./file.ext or just file.ext if it exists
        """
        import re as _re
        # Match quoted or unquoted paths with a known extension
        _EXTS = r"\.(pdf|docx|pptx|doc|ppt|txt|jpg|jpeg|png|gif|webp|bmp|mp4|mov|avi|mkv|csv|json|py)"
        patterns = [
            r'"([A-Za-z]:[^"]+' + _EXTS + r')"',    # Windows quoted double
            r"'([A-Za-z]:[^']+" + _EXTS + r")'",    # Windows quoted single
            r'([A-Za-z]:\\[^\s,;"\']+' + _EXTS + r')',  # Windows unquoted
            r'([/~][^\s,;"\']+' + _EXTS + r')',     # Unix absolute
            r'(\./[^\s,;"\']+' + _EXTS + r')',      # Relative ./
        ]
        found = []
        for pat in patterns:
            for m in _re.finditer(pat, user_input, _re.IGNORECASE):
                p = m.group(1)
                if Path(p).exists():
                    found.append(p)

        if not found:
            return user_input

        hint_lines = ["[FILES ATTACHED — use appropriate tool to process each]"]
        from tools.tools_media import MediaTools as _MT
        _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        _VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv"}
        _DOC_EXTS   = {".pdf", ".docx", ".pptx", ".doc", ".ppt", ".txt", ".csv"}

        for p in found:
            ext = Path(p).suffix.lower()
            if ext in _IMAGE_EXTS:
                hint_lines.append(f"  {p} → use analyze_image or ocr_image")
            elif ext in _VIDEO_EXTS:
                hint_lines.append(f"  {p} → use analyze_video")
            elif ext in _DOC_EXTS:
                hint_lines.append(f"  {p} → use read_document or analyze_document_smart")
            else:
                hint_lines.append(f"  {p} → use read_document")

        return user_input + "\n\n" + "\n".join(hint_lines)

    def _check_reminders(self) -> list:
        """
        Scan L3 for entries that are expiring today (due reminders).
        Returns list of reminder strings to show Ash on startup.
        Best-effort — never raises.
        """
        reminders = []
        try:
            import sqlite3 as _sq
            today = datetime.now(timezone.utc).date().isoformat()
            with closing(_sq.connect(str(self.memory.sqlite_path))) as conn:
                conn.row_factory = _sq.Row
                rows = conn.execute(
                    """SELECT content, category, active_until
                       FROM l3_cache
                       WHERE active_until IS NOT NULL
                         AND active_until >= ? AND active_until <= ?
                         AND (archived = 0 OR archived IS NULL)
                       ORDER BY active_until""",
                    (today, today + "T23:59:59"),
                ).fetchall()
            for r in rows:
                due = r["active_until"][:10]
                reminders.append(f"[REMINDER due {due}] {r['content']}")
        except Exception:
            pass
        return reminders

    def run(self):
        """Main loop — compact startup banner (T-041)."""

        # Start background services (non-blocking)
        if self.scheduler is not None:
            self.scheduler.start()
        if self.telegram is not None and self.telegram.is_available():
            self.telegram.start_polling(block=False)

        from agent.turn_log import count_today
        from agent.startup_banner import format_banner
        from agent.status_line import emit_if_enabled as _emit_status_line

        banner = format_banner(
            mode=self.mode,
            session_id=self.session_id,
            tool_count=len(self._get_tool_definitions()),
            telegram_online=bool(self.telegram and self.telegram.is_available()),
            scheduler_running=bool(self.scheduler),
            turns_today=count_today(),
            reminders=self._check_reminders(),
        )
        print(banner)

        while True:
            try:
                user_input = input("Ash: ").strip()

                if not user_input:
                    continue

                response = self.process_input(user_input)

                if response == "EXIT":
                    on_exit(self)
                    break

                if response:
                    print(f"\nPi: {response}\n")
                    _emit_status_line(self)

            except KeyboardInterrupt:
                # T-082 audit-bug-2: even on Ctrl+C, drain the async log queue
                # so pending L1 turn writes aren't lost. Skip full on_exit
                # (Groq distillation might be unwanted on abort) but always flush.
                print("\n[Pi] Interrupted — flushing logs...")
                try:
                    self.flush_logs(timeout=3.0)
                except Exception:
                    pass
                break
            except Exception as e:
                print(f"\n[Pi] Fatal error: {e}")
                import traceback
                traceback.print_exc()


def main():
    """Entry point"""
    try:
        agent = PiAgent()
        agent.run()
    except Exception as e:
        print(f"[Pi] Initialization failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()