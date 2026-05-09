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
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from app.config import (
    ANTHROPIC_API_KEY,
    GROQ_API_KEY,
    SUPABASE_URL,
    SUPABASE_KEY,
    DEFAULT_MODE,
    OPENWEATHER_API_KEY,
    ALPHA_VANTAGE_KEY,
    NEWS_API_KEY,
)

import anthropic
from groq import Groq
try:
    from groq import RateLimitError as _GroqRateLimitError
    from groq import APIStatusError as _GroqAPIStatusError
except ImportError:
    _GroqRateLimitError = None
    _GroqAPIStatusError = None

from tools.tools_memory import MemoryTools
from tools.tools_execution import ExecutionTools
from tools.tools_awareness import AwarenessTools
from evolution import EvolutionTracker
from agent.health import run_health_check
from agent.review import check_monthly_review
from agent.truncation import (
    truncate_messages_safely, extract_text_from_messages,
    compress_messages_with_groq,
)
from agent.session import generate_session_summary, on_exit
from agent.tools import get_tool_definitions, execute_tool
from agent.prompt import build_system_prompt, minimal_consciousness
from agent.modes import detect_mode_switch
from agent.awareness_shortcut import try_answer_from_awareness

# God mode — gitignored private layer. Graceful no-op if file absent.
try:
    from agent.god import GodMode
    _god = GodMode()
    GOD_AVAILABLE = _god.is_available()
except ImportError:
    _god = None
    GOD_AVAILABLE = False

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
        self.execution = ExecutionTools()
        self.evolution = EvolutionTracker()
        check_monthly_review(self.evolution)

        # Initialize LLM clients
        self.claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.groq = Groq(api_key=GROQ_API_KEY)

        # Awareness — fetch live world state once at startup, cache 30 min
        self.awareness = AwarenessTools(
            openweather_key=OPENWEATHER_API_KEY or "",
            alpha_vantage_key=ALPHA_VANTAGE_KEY or "",
            news_api_key=NEWS_API_KEY or "",
        )
        # T-041: Lazy awareness — snapshot loads on first access (3-5s saved on
        # cold start). --eager-awareness CLI flag forces eager load for parity
        # with the old behaviour.
        self._awareness_snapshot_cache: Optional[str] = None
        if "--eager-awareness" in sys.argv:
            self._awareness_snapshot_cache = self.awareness.get_awareness_snapshot()

        # T-024: L1 thread UUID — deterministic from session_id; shared by auto-log and tool-path writes
        self.l1_thread_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, self.session_id))
        self.turn_number = 0

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
        
        # T-041: Silent init — only health-check failures surface. Pass
        # --verbose-init for the legacy multi-line startup.
        run_health_check(
            self.memory.supabase, self.memory.sqlite_path,
            ANTHROPIC_API_KEY, GROQ_API_KEY, SUPABASE_KEY,
            verbose=("--verbose-init" in sys.argv),
        )
        if "--verbose-init" in sys.argv:
            print(f"[Pi] Agent initialized - {self.session_start.strftime('%Y-%m-%d %H:%M')}")
            print(f"[Pi] Session ID: {self.session_id}")
            print(f"[Pi] Mode: {self.mode}")
            print(f"[Pi] Consciousness loaded: {len(self.consciousness)} chars")

    @property
    def awareness_snapshot(self) -> str:
        """Lazy awareness — loads on first read, cached afterwards (T-041)."""
        if self._awareness_snapshot_cache is None:
            self._awareness_snapshot_cache = self.awareness.get_awareness_snapshot()
        return self._awareness_snapshot_cache

    @awareness_snapshot.setter
    def awareness_snapshot(self, value: str) -> None:
        """Allow tools (refresh_awareness) to overwrite the cache."""
        self._awareness_snapshot_cache = value

    def _minimal_consciousness(self) -> str:
        """Thin wrapper preserving the method API; logic in agent.prompt."""
        return minimal_consciousness()
    
    def _get_system_prompt(self) -> str:
        base = build_system_prompt(self.consciousness, self.mode, self.memory)
        if self.awareness_snapshot:
            return base + "\n\n" + self.awareness_snapshot
        return base
    
    def _get_tool_definitions(self) -> List[Dict]:
        """Thin wrapper preserving the method API; logic in agent.tools."""
        return get_tool_definitions()
    
    def _calculate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Calculate Claude Sonnet 4.6 API cost"""
        return (tokens_in / 1_000_000 * 0.80) + (tokens_out / 1_000_000 * 4.00)

    def _execute_tool(self, tool_name: str, tool_input: Dict) -> Any:
        """Thin wrapper preserving the method API; logic in agent.tools."""
        return execute_tool(self, tool_name, tool_input)
    
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
            error_str = str(e)
            response = f"[Pi] Internal error: {e}"

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
        except Exception:
            pass

        return response

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
            # T-037: On normie→root switch, snapshot the Groq conversation so Claude
            # gets a framed handoff instead of starting cold on a raw message list.
            if prev_mode == "normie" and self.mode == "root" and self.messages:
                self._normie_handoff_context = self._build_normie_handoff()
                self._archive_normie_session_to_vault()
            if prev_mode == "god" and _god is not None:
                try:
                    _god.memory.sync_to_vault()
                except Exception:
                    pass
            return response

        # God mode activation (handled separately — not in detect_mode_switch so
        # the trigger stays out of committed modes.py)
        if user_input.lower().strip() in ("god mode", "god"):
            if _god is None:
                return "God mode unavailable — agent/god.py not found."
            if not _god.is_available():
                return "God mode unavailable — no backend reachable (Groq key missing + Ollama offline)."
            self.mode = "god"
            return f"God mode active ({_god.backend_label()}, private, no restrictions)"

        cmd = user_input.lower().strip()

        if cmd == "analyze performance":
            return self._performance_report()
        elif cmd == "help":
            return (
                "Commands:\n"
                "  root mode        — switch to Claude (full tools)\n"
                "  normie mode      — switch to Groq (fast, no tools)\n"
                "  god mode         — private uncensored layer (gitignored)\n"
                "  research mode    — 3-agent debate on a question\n"
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

        interaction_start = datetime.now(timezone.utc)
        tool_calls_made = []
        success = False

        try:
            if self.mode == "root":
                return self._respond_root(user_input, interaction_start, tool_calls_made)
            elif self.mode == "god":
                return self._respond_god(user_input, interaction_start)
            else:
                return self._respond_normie(user_input, interaction_start)

        except Exception as e:
            self.evolution.log_interaction(
                user_input=user_input,
                pi_response=f"Error: {str(e)}",
                tool_calls=tool_calls_made,
                success=False,
                mode=self.mode,
                metadata={"error": str(e)}
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

            query = keywords[0]

            # Step 3: normalise plural → singular ("deadlines" → "deadline")
            if query.endswith("s") and len(query) > 4:
                query = query[:-1]

            hits = self.memory.memory_read(query=query, limit=4)
            if not hits:
                return ""
            lines = [f"[PREFETCH: '{query}']"]
            for h in hits[:4]:
                tier_label = h.get("tier", "?").upper()
                content = (h.get("content") or "")[:200]
                lines.append(f"  [{tier_label}] {content}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _respond_root(self, user_input: str, interaction_start, tool_calls_made: list) -> str:
        """Root mode: Claude with full tool loop"""
        shortcut = try_answer_from_awareness(user_input, self.awareness_snapshot)
        if shortcut:
            duration = (datetime.now(timezone.utc) - interaction_start).total_seconds()
            self.messages.append({"role": "user", "content": user_input})
            self.messages.append({"role": "assistant", "content": shortcut})
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": shortcut})
            self.evolution.log_interaction(
                user_input=user_input, pi_response=shortcut, tool_calls=[],
                success=True, mode=self.mode, cost=0.0, model="shortcut",
                tokens_in=0, tokens_out=0,
                metadata={"duration_seconds": duration, "session_id": self.session_id,
                          "shortcircuit": True},
            )
            self.turn_number += 1
            self.memory.log_turn(
                thread_id=self.l1_thread_id, session_id=self.session_id,
                turn_number=self.turn_number, user_content=user_input,
                assistant_content=shortcut, mode=self.mode,
            )
            return shortcut

        system_prompt = self._get_system_prompt()

        # T-037: Inject Groq session summary on first root response after mode switch.
        if self._normie_handoff_context:
            system_prompt = system_prompt + "\n\n" + self._normie_handoff_context
            self._normie_handoff_context = ""

        # Proactive memory prefetch: search L3+L2 for terms in the user's message
        # and inject relevant hits into the system prompt before Claude sees it.
        # This means Pi always has relevant context without needing to call memory_read.
        prefetch = self._prefetch_memory(user_input)
        if prefetch:
            system_prompt = system_prompt + "\n\n" + prefetch

        l1_tool_records = []  # T-024: separate from tool_calls_made to carry result_summary

        # Append user message to persistent history
        self.messages.append({"role": "user", "content": user_input})

        self._truncate_messages_safely(20)

        # Call Claude
        response = self.claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_prompt,
            messages=self.messages,
            tools=self._get_tool_definitions()
        )

        # CRITICAL: Append raw assistant response to history FIRST
        self.messages.append({"role": "assistant", "content": response.content})

        t_in = response.usage.input_tokens if response.usage else 0
        t_out = response.usage.output_tokens if response.usage else 0

        # Agentic loop: keep going while Claude wants to use tools
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = self._execute_tool(block.name, block.input)
                    tool_calls_made.append({"id": block.id, "name": block.name, "input": block.input})
                    l1_tool_records.append({  # T-024: include result for L1 archive
                        "name": block.name,
                        "input": dict(block.input),
                        "result_summary": str(result)[:500],
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })

            # Add tool results to history
            self.messages.append({"role": "user", "content": tool_results})

            # Continue conversation
            response = self.claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=system_prompt,
                messages=self.messages,
                tools=self._get_tool_definitions()
            )

            # Append this response too
            self.messages.append({"role": "assistant", "content": response.content})

            t_in += response.usage.input_tokens if response.usage else 0
            t_out += response.usage.output_tokens if response.usage else 0

        # Extract final text
        final_text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

        # Keep simplified string history for research mode
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": final_text})

        total_cost = self._calculate_cost(t_in, t_out)
        self.evolution.log_interaction(
            user_input=user_input,
            pi_response=final_text,
            tool_calls=tool_calls_made,
            success=True,
            mode=self.mode,
            cost=total_cost,
            model="claude-sonnet-4-6",
            tokens_in=t_in,
            tokens_out=t_out,
            metadata={"duration_seconds": (datetime.now(timezone.utc) - interaction_start).total_seconds(), "session_id": self.session_id}
        )

        # T-024: Auto-log complete turn to L1 archive.
        self.turn_number += 1
        self.memory.log_turn(
            thread_id=self.l1_thread_id,
            session_id=self.session_id,
            turn_number=self.turn_number,
            user_content=user_input,
            assistant_content=final_text,
            mode=self.mode,
            tool_calls=l1_tool_records,
            tokens_in=t_in,
            tokens_out=t_out,
            cost=total_cost,
        )

        return final_text

    def _truncate_messages_safely(self, max_messages: int = 20):
        """Compress or hard-truncate message history depending on length.

        At 30+ messages, Groq summarises the oldest half into a context block
        (free, zero Claude cost) so long sessions don't lose earlier context.
        Below that threshold, falls back to the safe hard-truncation logic.
        """
        if len(self.messages) >= 30:
            self.messages = compress_messages_with_groq(
                self.messages, self.groq, threshold=30, keep_recent=12
            )
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

    def _respond_god(self, user_input: str, interaction_start) -> str:
        """God mode: Groq/Ollama private LLM + full codebase tools + private memory."""
        if _god is None or not _god.is_available():
            self.mode = "normie"
            return "[God] backend unreachable — falling back to normie mode."

        # Load god consciousness (private, gitignored) as system prompt
        god_prompt_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "prompts", "god_consciousness.txt"
        )
        try:
            system = open(god_prompt_path, encoding="utf-8").read()
        except FileNotFoundError:
            system = self.consciousness  # fallback to base consciousness

        # Inject L3 context so god mode has memory access
        l3_ctx = self.memory.get_l3_context(max_tokens=600)
        if l3_ctx:
            system = system + "\n\n" + l3_ctx

        self.messages.append({"role": "user", "content": user_input})
        content = _god.respond(self.messages, system)
        self.messages.append({"role": "assistant", "content": content})
        self._truncate_messages_safely(20)

        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": content})

        duration = (datetime.now(timezone.utc) - interaction_start).total_seconds()
        self.evolution.log_interaction(
            user_input=user_input, pi_response=content, tool_calls=[],
            success=True, mode=self.mode, cost=0.0, model=f"god/{_god.backend_label()}",
            tokens_in=0, tokens_out=0,
            metadata={"duration_seconds": duration, "session_id": self.session_id},
        )
        self.turn_number += 1
        self.memory.log_turn(
            thread_id=self.l1_thread_id, session_id=self.session_id,
            turn_number=self.turn_number, user_content=user_input,
            assistant_content=content, mode=self.mode,
        )
        return content

    def _respond_normie(self, user_input: str, interaction_start) -> str:
        """Normie mode: Groq, no tools"""
        shortcut = try_answer_from_awareness(user_input, self.awareness_snapshot)
        if shortcut:
            duration = (datetime.now(timezone.utc) - interaction_start).total_seconds()
            self.messages.append({"role": "user", "content": user_input})
            self.messages.append({"role": "assistant", "content": shortcut})
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": shortcut})
            self.evolution.log_interaction(
                user_input=user_input, pi_response=shortcut, tool_calls=[],
                success=True, mode=self.mode, cost=0.0, model="shortcut",
                tokens_in=0, tokens_out=0,
                metadata={"duration_seconds": duration, "session_id": self.session_id,
                          "shortcircuit": True},
            )
            self.turn_number += 1
            self.memory.log_turn(
                thread_id=self.l1_thread_id, session_id=self.session_id,
                turn_number=self.turn_number, user_content=user_input,
                assistant_content=shortcut, mode=self.mode,
            )
            return shortcut

        system_prompt = self._get_system_prompt()

        # T-016: Build session context from prior messages BEFORE appending the
        # current turn, so it doesn't appear duplicated to Groq.
        session_ctx = self._extract_text_from_messages(n=10)
        if session_ctx:
            system_prompt += f"\n\nSESSION CONTEXT (read-only, from this conversation):\n{session_ctx}"

        # T-016: Persist the user turn to the unified message store so a later
        # mode switch (normie → root) sees the conversation as one thread.
        # Previously normie wrote only to self.history, leaving self.messages
        # empty and causing Claude to treat post-switch sessions as brand new.
        self.messages.append({"role": "user", "content": user_input})

        groq_messages = [{"role": "system", "content": system_prompt}]
        groq_messages.append({"role": "user", "content": user_input})

        error_type: str | None = None
        try:
            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=groq_messages,
                max_tokens=2048
            )
            content = response.choices[0].message.content
        except Exception as e:
            if _GroqRateLimitError and isinstance(e, _GroqRateLimitError):
                content = (
                    "Hit the daily free-tier limit on normie mode. "
                    "Switch to root mode, or check back in an hour."
                )
                error_type = "rate_limit"
            elif _GroqAPIStatusError and isinstance(e, _GroqAPIStatusError):
                content = "Something went wrong on my end — try again in a moment."
                error_type = "api_error"
            else:
                content = "Couldn't reach my language model — try again in a moment."
                error_type = "unknown"
            print(f"[Pi] Groq {error_type}: {e}", flush=True)

        # T-016: Persist assistant turn to unified store as well.
        self.messages.append({"role": "assistant", "content": content})
        self._truncate_messages_safely(20)

        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": content})

        duration = (datetime.now(timezone.utc) - interaction_start).total_seconds()
        self.evolution.log_interaction(
            user_input=user_input,
            pi_response=content,
            tool_calls=[],
            success=(error_type is None),
            mode=self.mode,
            cost=0.0,
            model="groq",
            tokens_in=0,
            tokens_out=0,
            metadata={
                "duration_seconds": duration,
                "session_id": self.session_id,
                **({"error_type": error_type} if error_type else {}),
            },
        )

        # T-024: Auto-log complete turn to L1 archive.
        self.turn_number += 1
        self.memory.log_turn(
            thread_id=self.l1_thread_id,
            session_id=self.session_id,
            turn_number=self.turn_number,
            user_content=user_input,
            assistant_content=content,
            mode=self.mode,
        )

        return content
    
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
            conn = _sq.connect(str(self.memory.sqlite_path))
            conn.row_factory = _sq.Row
            today = datetime.now(timezone.utc).date().isoformat()
            rows = conn.execute(
                """SELECT content, category, active_until
                   FROM l3_cache
                   WHERE active_until IS NOT NULL
                     AND active_until >= ? AND active_until <= ?
                     AND (archived = 0 OR archived IS NULL)
                   ORDER BY active_until""",
                (today, today + "T23:59:59"),
            ).fetchall()
            conn.close()
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

            except KeyboardInterrupt:
                print("\n[Pi] Interrupted")
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