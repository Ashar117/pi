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
    CEREBRAS_MODEL,
    OPENROUTER_API_KEY,
    Z_AI_API_KEY,
    QWEN_API_KEY,
    QWEN_MODEL,
    SUPABASE_URL,
    SUPABASE_KEY,
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
from agent.conversation import message_text as _message_text
from agent.session import generate_session_summary, on_exit
from agent.tools import get_tool_definitions, execute_tool
from agent.prompt import (
    build_session_state_block,
    build_system_prompt,
    build_system_prompt_split,
    minimal_consciousness,
)
from agent.modes import detect_mode_switch, ModeConfig, get_mode_config
from agent.awareness_shortcut import try_answer_from_awareness
from agent.awareness_cache import AwarenessCache
from agent.redaction import safe_error as _safe_error
from agent.observability import track_silent as _track_silent
from agent.cost_footer import emit_if_enabled as _emit_cost_footer


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
        # Load consciousness: private prompt → public default (shipped in repo) → minimal
        prompts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
        self.consciousness = None
        for fname in ("consciousness.txt", "consciousness.default.txt"):
            try:
                with open(os.path.join(prompts_dir, fname), 'r', encoding='utf-8') as f:
                    self.consciousness = f.read()
                break
            except FileNotFoundError:
                continue
        if self.consciousness is None:
            print(f"[Pi] WARNING: No consciousness file found in {prompts_dir}")
            print("[Pi] Using minimal consciousness")
            self.consciousness = self._minimal_consciousness()
        
        # State — initialised early so subsystem setup can reference them
        self.mode = DEFAULT_MODE
        self.messages = []   # Persistent API message list (raw content blocks preserved)
        self.session_start = datetime.now(timezone.utc)
        self.session_id = uuid.uuid4().hex[:8]  # T-013: short ID for log correlation
        # T-142: a conversation is a thread of short-term context. /newchat starts
        # a fresh one (clears self.messages/history) without wiping L3 long-term
        # memory. conversation_id is stamped on L3 writes + boosts same-conversation recall.
        self.conversation_id = uuid.uuid4().hex[:8]
        # T-137: optional project/ticket scope for context-cued recall. No UI sets
        # it yet (stays None → no scope boost); infrastructure is ready for when it does.
        self.current_scope = None
        # T-037: populated when switching normie→root; injected once into first root prompt
        self._normie_handoff_context: str = ""
        # T-185: compact repo map cache; None = dirty/needs rebuild
        self._repo_map_cache: Optional[str] = None
        # T-183: per-session plan state (set_plan / update_plan tools)
        from agent.plan_state import PlanState
        self.plan_state: PlanState = PlanState()

        # Initialize systems
        self.memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)
        # T-082: per-namespace MemoryTools cache. Public memory is the default
        # entry; a non-default namespace is built on first access via
        # _get_memory_for_config() and cached so repeated turns share one
        # SQLite connection.
        self._memory_by_namespace: Dict[str, MemoryTools] = {"pi": self.memory}
        self.execution = ExecutionTools()
        self.evolution = EvolutionTracker()
        check_monthly_review(self.evolution)

        # Initialize LLM clients (legacy direct clients kept for compress_messages_with_groq)
        self.claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        # Unlike anthropic.Anthropic, Groq's client raises eagerly at construction
        # when api_key is None (not just on first call) — so a fresh checkout
        # with no .env (CI, a judge's clone) crashed on PiAgent() itself, before
        # any test ever tried to actually call Groq. Match the router's own
        # `key or ""` convention a few lines down.
        self.groq = Groq(api_key=GROQ_API_KEY or "")

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
            cerebras_model=CEREBRAS_MODEL or "gpt-oss-120b",
            openrouter_key=OPENROUTER_API_KEY or "",
            z_ai_key=Z_AI_API_KEY or "",
            qwen_key=QWEN_API_KEY or "",
            qwen_model=QWEN_MODEL or "qwen3.7-max",
        )

        # Awareness — fetch live world state once at startup, cache 30 min
        self.awareness = AwarenessTools(
            openweather_key=OPENWEATHER_API_KEY or "",
            alpha_vantage_key=ALPHA_VANTAGE_KEY or "",
            news_api_key=NEWS_API_KEY or "",
        )
        # T-041 / T-067 / T-173: Awareness cache extracted to agent/awareness_cache.py.
        self._awareness_cache = AwarenessCache(self.awareness)

        # T-024: L1 thread UUID — deterministic from session_id; shared by auto-log and tool-path writes
        self.l1_thread_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, self.session_id))
        self.turn_number = 0

        # T-136: idle replay (sleep-consolidation analogue). Default OFF
        # (PI_IDLE_REPLAY); high cost-risk per the ticket. Built only when
        # enabled so there is zero overhead/risk by default.
        self.idle_replay = None
        try:
            self._init_idle_replay()
        except Exception as e:
            print(f"[Pi] idle-replay init skipped (non-fatal): {e}")
        # T-072: mid-session distillation — fires every N turns so memory doesn't
        # depend on a clean exit. Tracks the last turn we distilled up to so each
        # batch only sees new L1 rows.
        self._last_distilled_turn = 0
        self._distill_every_n_turns = 10

        # T-198: stash turn metadata so process_input can write real values to turns.jsonl
        # Set at every return path in _respond_via_config (shortcut / error / normal).
        # TODO(T-162): replace with a proper TurnMeta when Turn object exists.
        self._last_turn_meta: dict = {"tools_used": [], "cost": 0.0, "model": ""}

        # T-178: True when the last turn was streamed live (run() skips re-printing).
        self._last_turn_streamed: bool = False

        # T-196: pending research question flag — set by 'research mode' command,
        # consumed on the next turn so input() is never called mid-turn.
        self._pending_research: bool = False

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

        # Background watchers — Telegram alerts on file/schedule/url/keyword/price/email events
        self.watchers = None
        if _WatcherManager is not None:
            # T-272 (was T-249): TelegramTools has no send_message attribute (only
            # .send()) — this getattr always returned None, so watcher alerts have
            # silently never reached Telegram in production.
            _tg_send = getattr(self.telegram, "send", None) if self.telegram else None
            _tg_buttons = getattr(self.telegram, "send_buttons", None) if self.telegram else None
            self.watchers = _WatcherManager(telegram_send_fn=_tg_send, telegram_buttons_fn=_tg_buttons)
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

        # T-186: register the initial conversation in SQLite so resume/chats work.
        try:
            self.memory.create_conversation(
                conversation_id=self.conversation_id,
                mode=self.mode,
                created_at=self.session_start.isoformat(),
            )
        except Exception:
            pass

    @property
    def awareness_snapshot(self) -> str:
        """Delegates to AwarenessCache (T-173). See agent/awareness_cache.py."""
        return self._awareness_cache.snapshot

    @awareness_snapshot.setter
    def awareness_snapshot(self, value: str) -> None:
        self._awareness_cache.snapshot = value

    def _minimal_consciousness(self) -> str:
        """Thin wrapper preserving the method API; logic in agent.prompt."""
        return minimal_consciousness()
    
    def _get_system_prompt(self) -> str:
        """Single-string system prompt — used by normie mode."""
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
        result = execute_tool(self, tool_name, tool_input, memory_override=memory_override)
        # T-185: invalidate repo map cache when files change
        if tool_name in {"modify_file", "create_file", "execute_bash", "execute_python"}:
            self._repo_map_cache = None
        # T-229: collect citations for the post-step Sources injection.
        if isinstance(result, dict):
            if tool_name in ("grounded_search",):
                for c in result.get("citations") or []:
                    if c.get("url"):
                        self._turn_citations.append(c)
            elif tool_name in ("deep_research",):
                for s in result.get("sources") or []:
                    if s.get("url"):
                        self._turn_citations.append(s)
            # Auto-deliver generated images to Telegram so the model doesn't need
            # to chain a telegram_send call (which it often forgets, outputting
            # markdown image syntax that Telegram renders as raw text instead).
            elif tool_name == "image_gen" and result.get("success") and result.get("path"):
                chat_id = getattr(self, "_current_chat_id", None)
                if chat_id:
                    try:
                        from tools.tools_telegram import send_file
                        send_file(result["path"], chat_id=chat_id)
                        result["_tg_sent"] = True
                    except Exception:
                        pass
        return result
    
    def process_input(self, user_input: str) -> str:
        """Main entry point — wraps inner dispatch with universal turn logging.

        T-039: Every turn (both modes, all return paths) is appended to
        logs/turns.jsonl as a durable local record. Never gates on mode.
        """
        from agent.turn_log import append_turn

        # T-136: user activity halts any in-flight idle replay (no-op when the
        # default-off idle-replay daemon isn't running).
        if getattr(self, "idle_replay", None) is not None:
            self.idle_replay.notify_activity()

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
            _meta = self._last_turn_meta  # T-198: real values set by _respond_via_config
            self._last_turn_meta = {"tools_used": [], "cost": 0.0, "model": ""}  # reset
            _current_profile = getattr(self, "current_profile", None)
            _profile_name: Optional[str] = None
            if _current_profile is not None and getattr(_current_profile, "is_guest", False):
                _profile_name = getattr(_current_profile, "name", None)
            append_turn(
                session_id=self.session_id,
                mode=self.mode,
                user_input=user_input,
                response=response,
                duration_ms=duration_ms,
                tools_used=_meta.get("tools_used", []),
                cost=_meta.get("cost", 0.0),
                model=_meta.get("model", ""),
                error=error_str,
                profile_name=_profile_name,
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

    def _init_idle_replay(self) -> None:
        """T-136: build + start the idle-replay daemon iff PI_IDLE_REPLAY is on.

        Collaborators are bound to this agent's (namespace-correct) memory +
        router. detect_patterns is a conservative stub for now — the full
        cross-session entity-count query is the soak-phase deliverable; the
        manager, caps, halt-on-activity, and meta-fact write path are all
        tested in test_idle_replay.py.
        """
        from agent.idle_replay import IdleReplayManager, _env_on
        if not _env_on("PI_IDLE_REPLAY"):
            return

        def fetch_episodes():
            try:
                return self.memory.memory_read(query="", tier="l1", limit=20) or []
            except Exception:
                return []

        def replay_episode(ep):
            # Rehearsal: re-surface the episode through recall so it can be
            # re-consolidated by the existing distill path. Best-effort.
            try:
                self.memory.memory_read(query=(ep.get("content") or "")[:60], limit=3)
            except Exception:
                pass

        def detect_patterns():
            # T-136: entities recurring across >=3 distinct conversations (last 7d).
            try:
                return self.memory.detect_cross_session_patterns()
            except Exception:
                return []

        def write_meta_fact(p):
            try:
                self.memory.memory_write(
                    content=p.get("content", ""), tier="l2",
                    category="pattern_observation", source="replay",
                    mode=self.mode, conversation_id=self.conversation_id,
                )
            except Exception:
                pass

        tpd = getattr(self.router, "tpd_budget_remaining", None)
        self.idle_replay = IdleReplayManager(
            fetch_episodes=fetch_episodes,
            replay_episode=replay_episode,
            detect_patterns=detect_patterns,
            write_meta_fact=write_meta_fact,
            tpd_remaining=tpd if callable(tpd) else None,
            enabled=True,
        )
        self.idle_replay.start()

    def _process_input_inner(self, user_input: str) -> str:
        """Inner dispatch — Claude decides, tools execute, evolution tracks.

        Wrapped by ``process_input`` so every turn is logged. Do not call this
        directly from outside the class — go through ``process_input``.
        """

        # T-196: two-step research mode — consume pending question from next turn
        # so no input() call blocks non-REPL channels (Telegram, voice, daemon).
        if getattr(self, "_pending_research", False):
            self._pending_research = False
            question = user_input.strip()
            if question:
                from core.research_mode import run_research_mode
                context_str = extract_text_from_messages(self.messages, n=10)
                synthesis = run_research_mode(question, rounds=2, context=context_str)
                if synthesis:
                    self.memory.memory_write(
                        content=f"Research ({datetime.now(timezone.utc).strftime('%Y-%m-%d')}): {question[:80]} | {synthesis[:300]}",
                        tier="l3", importance=5, category="research_results",
                    )
                    print("[Research] Results saved to memory")
                return "[Research complete. Continue conversation or 'exit']"
            return "[No research question provided — send your question first]"

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
            return response

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
                "  research mode    — 3-agent debate on a question\n"
                "  deliberate: <q> — careful planner→drafter→critic pipeline\n"
                "  voice            — start voice mode (PTT by default)\n"
                "  voice vad        — voice mode with voice-activity detection\n"
                "  voice wake       — voice mode with wake-word detection\n"
                "  briefing         — full daily briefing (weather/news/markets/HN/research)\n"
                "  new chat         — start a fresh conversation (keeps long-term memory)\n"
                "  chats            — list recent 10 conversations\n"
                "  resume <id>      — restore a previous conversation by id\n"
                "  analyze performance  — last 7-day performance report\n"
                "  exit             — save session and quit"
            )
        elif cmd == "research mode":
            # T-196: channel-agnostic two-step — set pending flag, consume next message
            # as the question. Also accept inline: 'research <question>'.
            # No input() calls — works from Telegram, voice, daemon, brain server.
            self._pending_research = True
            print("\n" + "="*60)
            print("  PI RESEARCH MODE - 3-Agent Debate")
            print("="*60)
            return "Ready for research. Reply with your question."
        elif cmd.startswith("research ") and len(cmd) > 9:
            question = user_input[len("research "):].strip()
            if question:
                from core.research_mode import run_research_mode
                context_str = extract_text_from_messages(self.messages, n=10)
                synthesis = run_research_mode(question, rounds=2, context=context_str)
                if synthesis:
                    self.memory.memory_write(
                        content=f"Research ({datetime.now(timezone.utc).strftime('%Y-%m-%d')}): {question[:80]} | {synthesis[:300]}",
                        tier="l3", importance=5, category="research_results",
                    )
                    print("[Research] Results saved to memory")
                return "[Research complete. Continue conversation or 'exit']"
            return "[No research question provided]"
        elif cmd in ("new chat", "newchat", "/newchat", "/new"):
            # T-142: reset short-term conversation context without touching L3
            # (durable facts) — like opening a fresh chat in ChatGPT/Claude.
            self.messages = []
            self._normie_handoff_context = ""
            self.conversation_id = uuid.uuid4().hex[:8]
            self.plan_state.clear()  # T-183: plan belongs to the conversation, not the session
            # T-186: register the new conversation so chats/resume can find it.
            try:
                self.memory.create_conversation(
                    conversation_id=self.conversation_id,
                    mode=self.mode,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                pass
            return (
                f"New chat started (conversation {self.conversation_id}). "
                "Short-term context cleared; long-term memory kept."
            )

        elif cmd == "chats":
            # T-186: list recent conversations
            convs = self.memory.list_conversations(limit=10)
            if not convs:
                return "No conversations on record yet."
            lines = ["Recent conversations (newest first):"]
            for c in convs:
                active = " ← current" if c["id"] == self.conversation_id else ""
                lines.append(f"  {c['id']}  {c['title'][:50]}  [{c['mode']}]  {c['last_active_at'][:16]}{active}")
            return "\n".join(lines)

        elif cmd.startswith("resume "):
            # T-186: restore a previous conversation from SQLite
            target_id = user_input[len("resume "):].strip()
            if not target_id:
                return "Usage: resume <conversation-id>"
            turns = self.memory.load_conversation_turns(target_id, max_turns=40)
            if not turns:
                return f"No turns found for conversation '{target_id}'."
            # Apply budget truncation to stay within context window
            from agent.truncation import truncate_messages_safely as _tms
            self.messages = _tms(turns, max_messages=20)
            self.conversation_id = target_id
            self.plan_state.clear()
            return (
                f"Resumed conversation {target_id} "
                f"({len(self.messages)} messages loaded). Continue where you left off."
            )

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

        # T-207: 'deliberate:' prefix — careful-answer pipeline (planner→drafter→critic).
        # Higher-quality than a single pass; uses cheap tiers for planner/critic.
        if cmd.startswith("deliberate:"):
            question = user_input[len("deliberate:"):].strip()
            if question:
                from core.roles import CAREFUL_ANSWER_PIPELINE
                print("[Pi] Deliberate mode: planner → drafter → critic…", flush=True)
                result = CAREFUL_ANSWER_PIPELINE.run(question, self.router)
                return result["final"]
            return "[deliberate: prefix requires a question — e.g. 'deliberate: explain X']"

        # File attachment detection — if message contains a file path, inject context
        user_input = self._preprocess_file_refs(user_input)

        # Daily budget enforcement
        from app.config import DAILY_COST_LIMIT
        daily_cost = self.evolution.get_daily_cost()
        if daily_cost >= DAILY_COST_LIMIT and self.mode == "root":
            print(f"[Pi] Daily cost limit reached (${daily_cost:.4f}). Switching to normie.")
            self.mode = "normie"

        # T-089 R8 Stage C: all modes route through _respond_via_config.
        interaction_start = datetime.now(timezone.utc)

        try:
            return self._respond_via_config(
                user_input, interaction_start, get_mode_config(self.mode)
            )
        except Exception as e:
            # T-195: redact before surfacing to user — raw str(e) can carry keys/URLs/paths.
            self.evolution.log_interaction(
                user_input=user_input,
                pi_response=f"Error: {_safe_error(e, audience='user')}",
                tool_calls=[],
                success=False,
                mode=self.mode,
                metadata={"error": _safe_error(e, audience="public_log")},
            )
            return f"[Pi] Error: {_safe_error(e, audience='user')}"

    # T-180: tools that MUST run sequentially (side-effectful or non-thread-safe)
    _SERIAL_TOOL_NAMES: frozenset = frozenset([
        # File writes
        "modify_file", "create_file",
        # Code execution (state-mutating, process-spawning)
        "execute_bash", "execute_python",
        # Computer + browser (single-window singletons)
        "computer_screenshot", "computer_click", "computer_type", "computer_key",
        "computer_scroll", "computer_move", "computer_drag", "computer_action",
        "browser_open", "browser_click", "browser_type", "browser_scroll",
        "browser_navigate", "browser_close", "browser_screenshot",
        # Outbound comms (fire-and-forget, no idempotency)
        "gmail_send", "telegram_send",
        # Calendar + memory writes (ordering matters)
        "calendar_create", "calendar_update", "calendar_delete",
        "memory_write", "memory_delete",
        # Media gen (expensive, provider-rate-limited)
        "image_gen", "generate_video",
        # Obsidian writes
        "obsidian_write", "obsidian_append",
    ])

    # T-191: escalation keywords that signal premium-tier routing
    _PREMIUM_KEYWORDS = frozenset([
        "think hard", "think deeply", "ultra", "best model", "opus",
        "use your full capacity", "architect", "design review",
    ])

    def _is_premium_turn(self, user_input: str) -> bool:
        """True when the user input signals a task worth premium-tier routing."""
        lower = user_input.lower()
        return any(kw in lower for kw in self._PREMIUM_KEYWORDS)

    # T-185: code-shape detection + compact repo map injection
    _CODE_VERBS = frozenset([
        "fix", "refactor", "implement", "test", "debug", "edit", "modify",
        "create", "write", "rename", "break", "error", "bug", "add", "update",
        "change", "remove", "delete", "import", "function", "class", "method",
    ])
    _CODE_MODULES = frozenset([
        "agent/", "tools/", "scripts/", "testing/", "core/", "pi_agent",
        "verify", "sprint", "retro", "truncation", "memory", "router",
    ])

    @staticmethod
    def _is_code_shaped(user_input: str) -> bool:
        """True when the message is likely about code — warrants repo map injection."""
        lower = user_input.lower()
        # .py path or known module path mentioned
        if ".py" in lower or any(m in lower for m in PiAgent._CODE_MODULES):
            return True
        # code action verb in a short-ish message (long prose is less likely code)
        words = lower.split()
        if len(words) <= 30 and any(w in PiAgent._CODE_VERBS for w in words):
            return True
        return False

    def _build_compact_repo_map(self, user_input: str) -> str:
        """Return a ≤1600-char compact repo map block, using a session-level cache."""
        if self._repo_map_cache is not None:
            return self._repo_map_cache
        try:
            from tools.tools_project import ProjectTools
            data = ProjectTools().repo_map(
                query=user_input, max_files=15, symbols_per_file=6
            )
            lines = ["── REPO MAP (top files by relevance) ──────────────────────────"]
            for entry in data.get("files", []):
                path = entry["path"]
                syms = ", ".join(entry["symbols"][:6])
                lines.append(f"  {path}: {syms}")
            lines.append(f"  ({data.get('total_files', 0)} total files, {data.get('method', '?')})")
            lines.append("────────────────────────────────────────────────────────────────")
            text = "\n".join(lines)
            # Hard cap at ~400 tokens (1600 chars)
            if len(text) > 1600:
                text = text[:1597] + "…"
            self._repo_map_cache = text
        except Exception:
            self._repo_map_cache = ""
        return self._repo_map_cache

    def _prefetch_memory(self, user_input: str) -> str:
        """Query-aware memory retrieval for the current turn (T-293).

        Only fires on recall questions — statements of fact and small talk are
        skipped entirely to avoid spurious searches ("followed", "planning", etc.).
        Returns a formatted block to append to the system prompt, or "".
        Best-effort — never raises.
        """
        try:
            lower = user_input.lower().rstrip(" ?.")

            # T-205: episode recall triggers — route to conversation digest search
            # before fact retrieval so narrative context wins over extracted facts.
            EPISODE_TRIGGERS = (
                "what did we decide", "last time we", "remember when",
                "last session", "last conversation", "last time you", "we discussed",
                "previously decided", "we agreed",
            )
            for trigger in EPISODE_TRIGGERS:
                if trigger in lower:
                    hits = self.memory.recall_episode(query=user_input, limit=4)
                    if hits:
                        lines = ["[EPISODE RECALL]"]
                        for ep in hits:
                            lines.append(
                                f"  [{ep.get('created_at','')[:10]}] {ep.get('title','?')}: "
                                f"{ep.get('digest','')[:200]}"
                            )
                        return "\n".join(lines)
                    break

            # Only prefetch when the message is a recall question.
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

            # T-293: one fused query-time retrieval across L3+L2, dense cosine +
            # lexical (BM25/context), replacing the old single-keyword-extraction
            # + two-step (semantic-then-lexical) search.
            hits = self.memory.retrieve(
                user_input, k=4, current_mode=self.mode,
                current_conversation_id=self.conversation_id,
                current_scope=self.current_scope,
            ) or []

            if not hits:
                return ""
            lines = ["[RELEVANT MEMORY]"]
            for h in hits[:4]:
                tier_label = (h.get("tier") or "L2").upper()
                content = (h.get("content") or "")[:200]
                lines.append(f"  [{tier_label}] {content}")
            return "\n".join(lines)
        except Exception:
            return ""

    @staticmethod
    def _serialize_tool_result(result: Any, cap: int = 32_000) -> str:
        """Serialize a tool result to a JSON string safe for LLM consumption (T-197).

        - default=str handles datetime/Path/bytes without TypeError.
        - Results exceeding `cap` chars are truncated with an explicit notice so
          the model knows and can re-call with a narrower scope.
        """
        if isinstance(result, str):
            serialized = result
        else:
            try:
                serialized = json.dumps(result, default=str)
            except Exception:
                serialized = str(result)
        if len(serialized) > cap:
            notice = f" ... [truncated {len(serialized) - cap} chars — re-call with narrower scope]"
            serialized = serialized[:cap] + notice
        return serialized

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

    def _truncate_messages_safely(self, max_messages: int = 20,
                                    token_budget: Optional[int] = None):
        """Compress or hard-truncate message history (T-184 extended).

        T-184: when token_budget is set, compression also triggers when estimated
        tokens exceed the budget — blind to actual token weight removed. Uses the
        structured digest format so the file-touch trail survives compression.
        """
        from agent.truncation import estimate_tokens
        over_count = len(self.messages) >= 30
        over_budget = token_budget is not None and estimate_tokens(self.messages) > token_budget
        if over_count or over_budget:
            try:
                self.messages = compress_messages_with_groq(
                    self.messages, self.groq, threshold=30, keep_recent=12,
                    anthropic_client=self.claude, token_budget=token_budget,
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
                content = _message_text(msg)
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
            content = _message_text(msg)
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

    def _get_memory_for_config(self, cfg: ModeConfig):
        """Return the MemoryTools instance for cfg.memory_namespace.

        Public namespace returns self.memory. A non-default namespace lazily
        builds a MemoryTools(db_path=cfg.memory_db, namespace=ns) instance and
        caches it so repeated turns share one SQLite connection.
        """
        ns = cfg.memory_namespace
        cached = self._memory_by_namespace.get(ns)
        if cached is not None:
            return cached
        if cfg.memory_db is None:
            mem = self.memory
        else:
            mem = MemoryTools(
                supabase_url="",
                supabase_key="",
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

        Dispatched for root and normie. Behavior is driven entirely by
        ModeConfig flags — no per-mode branches inside this method.

        A mode with public_logging=False skips both the public evolution log
        and the public raw_wiki write; the cfg.ctx_token_budget / allowlist
        knobs otherwise fully parameterize the path.
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
                self._last_turn_meta = {"tools_used": [], "cost": 0.0, "model": "shortcut"}  # T-198
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
            # T-185: compact repo map for code-shaped turns (root only, cached)
            if cfg.inject_repo_map and self._is_code_shaped(user_input):
                repo_map_block = self._build_compact_repo_map(user_input)
                if repo_map_block:
                    dynamic_p += "\n\n" + repo_map_block
            # T-183: inject active plan block (survives compaction in dynamic segment)
            if not self.plan_state.is_empty():
                dynamic_p += "\n\n" + self.plan_state.render()
            # T-194: per-turn honest state block (mode/conversation/tools/refusal)
            dynamic_p += "\n\n" + build_session_state_block(
                cfg, self.conversation_id, self.turn_number + 1, len(allowed_tools)
            )
            system_prompt = (static_p, warm_p, dynamic_p)
        else:
            # Normie: single string prompt
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
            # T-194: per-turn honest state block (mode/conversation/tools/refusal)
            system_prompt += "\n\n" + build_session_state_block(
                cfg, self.conversation_id, self.turn_number + 1, len(allowed_tools)
            )

        # ── 3. Message history & truncation ───────────────────────────
        self.messages.append({"role": "user", "content": user_input})
        self._truncate_messages_safely(20, token_budget=cfg.ctx_token_budget)
        if cfg.ctx_message_window is not None:
            # T-149: normie sends a real bounded multi-turn history (both sides),
            # safe-sliced so the window never starts mid tool-pair. Providers
            # flatten dict content via anthropic_messages_to_openai.
            llm_messages = truncate_messages_safely(
                self.messages, max_messages=cfg.ctx_message_window
            )
        else:
            llm_messages = self.messages

        # ── 4. First LLM call ─────────────────────────────────────────
        tool_calls_made: List[Dict] = []
        l1_tool_records: List[Dict] = []
        self._turn_citations: List[Dict] = []  # T-229: collected by _execute_tool

        # T-178: live streaming callback. Prints "\nPi: " on the first delta,
        # then each text token as it arrives. tool_use rounds emit no text deltas
        # via the Anthropic SSE protocol, so the prefix never appears mid-tool.
        _stream_started = False

        def _on_delta(text_chunk: str) -> None:
            nonlocal _stream_started
            if not _stream_started:
                print("\nPi: ", end="", flush=True)
                _stream_started = True
            print(text_chunk, end="", flush=True)

        # T-191: per-task escalation to premium tier for hard/code-edit requests.
        # Default-OFF: premium requires PI_PREMIUM_DAILY_LIMIT env var > 0.
        actual_tier = cfg.router_tier
        _premium_limit = float(os.environ.get("PI_PREMIUM_DAILY_LIMIT", "0.0"))
        if (cfg.name == "root" and _premium_limit > 0.0
                and self._is_premium_turn(user_input)):
            actual_tier = "premium"

        try:
            resp = self.router.chat(
                messages=llm_messages,
                system=system_prompt,
                tools=allowed_tools,
                max_tokens=cfg.max_tokens,
                tier=actual_tier,
                on_delta=_on_delta,
            )
        except RuntimeError as e:
            err_str = str(e).lower()
            # T-210: surface actionable cause instead of generic fallback
            if "credit balance" in err_str or "billing" in err_str or "insufficient_quota" in err_str:
                content = (
                    "Anthropic credits exhausted — Pi needs a top-up before root mode works. "
                    "Add credits at console.anthropic.com or set a CEREBRAS_API_KEY fallback."
                )
            elif "resource_exhausted" in err_str or "free_tier" in err_str:
                content = (
                    "Free-tier quota exhausted on all configured providers. "
                    "Try again tomorrow or add a paid API key."
                )
            elif "rate" in err_str or "429" in err_str:
                content = (
                    "Hit the daily free-tier limit. "
                    "Switch to root mode, or check back in an hour."
                )
            elif "no key" in err_str or "invalid_api_key" in err_str:
                content = "API key error — check .env for a missing or invalid key."
            elif "api" in err_str or "status" in err_str:
                content = "Something went wrong on my end — try again in a moment."
            else:
                content = "Couldn't reach my language model — try again in a moment."
            print(f"[Pi] {cfg.name} router error: {e}", flush=True)
            self.messages.append({"role": "assistant", "content": content})
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
            self._last_turn_meta = {"tools_used": [], "cost": 0.0, "model": "error"}  # T-198
            return content

        self.messages.append({"role": "assistant",
                              "content": self._build_assistant_content(resp)})
        t_in = resp.tokens_in
        t_out = resp.tokens_out

        # ── 5. Agentic tool loop ───────────────────────────────────────
        _interrupted = False
        while resp.stop_reason == "tool_use" and cfg.supports_tools:
            tool_results = []
            try:
                # T-180: run independent tools concurrently; serial tools fall back
                # to sequential. Batch is parallel only when ALL calls are non-serial
                # and there are 2+ calls — correctness beats speed when writes involved.
                calls = resp.tool_calls
                use_parallel = (
                    len(calls) > 1
                    and all(tc.name not in self._SERIAL_TOOL_NAMES for tc in calls)
                )
                if use_parallel:
                    import concurrent.futures as _cf
                    _CALL_TIMEOUT = 60  # per-tool timeout seconds
                    results_by_id: dict = {}
                    with _cf.ThreadPoolExecutor(max_workers=4) as pool:
                        futures = {
                            pool.submit(
                                self._execute_tool, tc.name, tc.input,
                                memory_override=mem
                            ): tc
                            for tc in calls
                        }
                        for fut, tc in futures.items():
                            try:
                                res = fut.result(timeout=_CALL_TIMEOUT)
                            except _cf.TimeoutError:
                                res = {"error": "tool_timeout",
                                       "message": f"{tc.name} exceeded {_CALL_TIMEOUT}s"}
                            except Exception as exc:
                                res = {"error": "tool_error", "message": str(exc)}
                            results_by_id[tc.id] = res
                    # Reassemble in original order
                    for tc in calls:
                        result = results_by_id[tc.id]
                        tool_calls_made.append({"id": tc.id, "name": tc.name, "input": tc.input})
                        l1_tool_records.append({
                            "name": tc.name,
                            "input": dict(tc.input),
                            "result_summary": str(result)[:500],
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": self._serialize_tool_result(result),
                        })
                else:
                    for tc in calls:
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
                            "content": self._serialize_tool_result(result),  # T-197
                        })
            except KeyboardInterrupt:
                # T-179: user pressed Ctrl+C mid-turn. Synthesize cancellation
                # results for any unanswered tool_use blocks so message pairing
                # stays valid (no orphan tool_use without tool_result).
                executed_ids = {r["tool_use_id"] for r in tool_results}
                for tc in resp.tool_calls:
                    if tc.id not in executed_ids:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": "[cancelled by user]",
                        })
                _interrupted = True

            self.messages.append({"role": "user", "content": tool_results})
            if _interrupted:
                # Append a synthetic assistant turn so history stays well-formed
                self.messages.append({"role": "assistant", "content": "[Turn cancelled by user interrupt.]"})
                break

            resp = self.router.chat(
                messages=self.messages,
                system=system_prompt,
                tools=allowed_tools,
                max_tokens=cfg.max_tokens,
                tier=cfg.router_tier,
                on_delta=_on_delta,
            )

            self.messages.append({"role": "assistant",
                                  "content": self._build_assistant_content(resp)})
            t_in += resp.tokens_in
            t_out += resp.tokens_out

        # ── 6. Finalise turn ───────────────────────────────────────────
        # T-178: if streaming produced output, terminate the live line and mark the
        # flag so run() skips re-printing the same text.
        if _stream_started:
            print("", flush=True)  # newline after last streamed token
            self._last_turn_streamed = True
        else:
            self._last_turn_streamed = False

        final_text = resp.text
        # T-229: if research tools ran and the model omitted a Sources section, append one.
        _cites = getattr(self, "_turn_citations", [])
        if _cites and "**Sources**" not in final_text and "Sources" not in final_text[-200:]:
            _seen_urls: set = set()
            _src_lines = []
            for c in _cites:
                u = c.get("url", "")
                if u and u not in _seen_urls:
                    _seen_urls.add(u)
                    t = c.get("title", u)
                    _src_lines.append(f"- [{t}]({u})")
            if _src_lines:
                final_text = final_text.rstrip() + "\n\n**Sources**\n" + "\n".join(_src_lines)
        self._turn_citations = []

        duration_s = (datetime.now(timezone.utc) - interaction_start).total_seconds()
        self.turn_number += 1
        total_cost = self._calculate_cost(t_in, t_out)

        # T-198: stash metadata so process_input can write real values to turns.jsonl
        self._last_turn_meta = {
            "tools_used": [tc["name"] for tc in tool_calls_made],
            "cost": total_cost,
            "model": f"{resp.provider}/{resp.model}",
        }

        if cfg.public_logging:
            # T-130: optional inline cost footer (PI_SHOW_COST=on). stderr only;
            # never enters final_text so Telegram/voice paths stay clean.
            # Gated by cfg.public_logging — a non-logging mode never emits.
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
            # T-186: persist both sides of the turn to conversation_turns for resume.
            # idx is 0-based; user = even, assistant = odd within each turn pair.
            _ts_now = datetime.now(timezone.utc).isoformat()
            _base_idx = (self.turn_number - 1) * 2
            self._async_log(
                mem.persist_turn,
                conversation_id=self.conversation_id,
                role="user",
                content=user_input,
                idx=_base_idx,
                ts=_ts_now,
            )
            self._async_log(
                mem.persist_turn,
                conversation_id=self.conversation_id,
                role="assistant",
                content=final_text,
                idx=_base_idx + 1,
                ts=_ts_now,
            )
            # Lazy title generation on the second turn (first exchange complete)
            if self.turn_number == 2 and user_input:
                _title = user_input[:80].strip()
                self._async_log(mem.title_conversation, self.conversation_id, _title)

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
        return generate_session_summary(self.router, self.messages, n=12)

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

                # T-178: skip re-printing if streaming already wrote the text live.
                if not self._last_turn_streamed and response:
                    print(f"\nPi: {response}\n")
                if self._last_turn_streamed or response:
                    _emit_status_line(self)
                self._last_turn_streamed = False

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