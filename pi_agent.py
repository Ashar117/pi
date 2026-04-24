"""
Pi Agent - Complete System
Claude as consciousness, tools as capabilities, self-evolution enabled
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    DEFAULT_MODE
)

import anthropic
from groq import Groq

from tools.tools_memory import MemoryTools
from tools.tools_execution import ExecutionTools
from evolution import EvolutionTracker


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
        
        # Initialize systems
        self.memory = MemoryTools(SUPABASE_URL, SUPABASE_KEY)
        self.execution = ExecutionTools()
        self.evolution = EvolutionTracker()
        self._check_monthly_review()

        # Initialize LLM clients
        self.claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.groq = Groq(api_key=GROQ_API_KEY)
        
        # State
        self.mode = DEFAULT_MODE
        self.messages = []   # Persistent API message list (raw content blocks preserved)
        self.history = []    # Simplified string-only history for research mode context
        self.session_start = datetime.now(timezone.utc)
        self.session_id = uuid.uuid4().hex[:8]  # T-013: short ID for log correlation
        
        self._health_check()
        print(f"[Pi] Agent initialized - {self.session_start.strftime('%Y-%m-%d %H:%M')}")
        print(f"[Pi] Session ID: {self.session_id}")
        print(f"[Pi] Mode: {self.mode}")
        print(f"[Pi] Consciousness loaded: {len(self.consciousness)} chars")
    
    def _minimal_consciousness(self) -> str:
        """Fallback consciousness if file not found"""
        return """You are Pi, Ash's personal intelligence system.
You are autonomous, direct, and cost-conscious.
You use tools to act, you verify results, you learn from mistakes.
You never hallucinate. You never pretend to know what you don't.
Islamic values are non-negotiable. Quality over speed on critical tasks."""
    
    def _get_system_prompt(self) -> str:
        """Build complete system prompt"""
        try:
            l3_context = self.memory.get_l3_context(max_tokens=800)
        except Exception as e:
            print(f"[Pi] L3 load failed: {e}")
            l3_context = ""

        if self.mode == "root":
            mode_block = f"""
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
CURRENT SESSION STATE
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
MODE: ROOT | MODEL: Claude Sonnet 4.6 | TOOLS: All 7 ENABLED
SESSION TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
You ARE in root mode. You HAVE tools. Use them when needed.
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550"""
        else:
            mode_block = f"""
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
CURRENT SESSION STATE
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
MODE: NORMIE | MODEL: Groq Llama 3.3 70B | TOOLS: NONE
SESSION TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
You are NOT in root mode. You do NOT have tools. You cannot use tools.
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550"""

        return f"{self.consciousness}{mode_block}\n\n{l3_context}"
    
    def _get_tool_definitions(self) -> List[Dict]:
        """Tool definitions for Claude"""
        
        return [
            {
                "name": "memory_read",
                "description": "Search memory. Returns matching entries.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for"},
                        "tier": {"type": "string", "enum": ["l1", "l2", "l3"], "description": "Optional tier filter"}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "memory_write",
                "description": "Write to memory. Auto-verifies.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "tier": {"type": "string", "enum": ["l1", "l2", "l3"], "default": "l3"},
                        "importance": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                        "category": {"type": "string", "default": "note"},
                        "expiry": {"type": "string", "description": "ISO datetime"}
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "memory_delete",
                "description": "Delete from memory. Soft delete = archive to L2.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "soft": {"type": "boolean", "default": True}
                    },
                    "required": ["target"]
                }
            },
            {
                "name": "execute_python",
                "description": "Execute Python code. Returns output/errors.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"}
                    },
                    "required": ["code"]
                }
            },
            {
                "name": "execute_bash",
                "description": "Execute bash command.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"}
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "read_file",
                "description": "Read file contents.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "modify_file",
                "description": "Modify file (including self). String must be unique.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"}
                    },
                    "required": ["path", "old_str", "new_str"]
                }
            },
            {
                "name": "create_file",
                "description": "Create a new file with given content.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["path", "content"]
                }
            }
        ]
    
    def _calculate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Calculate Claude Sonnet 4.6 API cost"""
        return (tokens_in / 1_000_000 * 0.80) + (tokens_out / 1_000_000 * 4.00)

    def _execute_tool(self, tool_name: str, tool_input: Dict) -> Any:
        """Execute tool and track performance"""
        
        start_time = datetime.now(timezone.utc)
        success = False
        
        try:
            if tool_name == "memory_read":
                result = self.memory.memory_read(
                    query=tool_input["query"],
                    tier=tool_input.get("tier")
                )
                success = True
            
            elif tool_name == "memory_write":
                expiry = None
                if "expiry" in tool_input and tool_input["expiry"]:
                    expiry = datetime.fromisoformat(tool_input["expiry"])

                result = self.memory.memory_write(
                    content=tool_input["content"],
                    tier=tool_input.get("tier", "l3"),
                    importance=tool_input.get("importance", 5),
                    category=tool_input.get("category", "note"),
                    expiry=expiry,
                    session_id=self.session_id  # T-013: consistent L1 threading
                )
                success = result.get("verified", False)
            
            elif tool_name == "memory_delete":
                result = self.memory.memory_delete(
                    target=tool_input["target"],
                    soft=tool_input.get("soft", True)
                )
                success = result.get("deleted", 0) > 0
            
            elif tool_name == "execute_python":
                result = self.execution.execute_python(code=tool_input["code"])
                success = result.get("success", False)
            
            elif tool_name == "execute_bash":
                result = self.execution.execute_bash(command=tool_input["command"])
                success = result.get("success", False)
            
            elif tool_name == "read_file":
                result = self.execution.read_file(path=tool_input["path"])
                success = result.get("success", False)
            
            elif tool_name == "modify_file":
                result = self.execution.modify_file(
                    path=tool_input["path"],
                    old_str=tool_input["old_str"],
                    new_str=tool_input["new_str"]
                )
                success = result.get("success", False)
                if success:
                    self.memory.memory_write(
                        content=f"Modified file: {tool_input['path']}",
                        tier="l3", importance=3, category="file_operations",
                        session_id=self.session_id
                    )

            elif tool_name == "create_file":
                result = self.execution.create_file(
                    path=tool_input["path"],
                    content=tool_input["content"]
                )
                success = result.get("success", False)
                if success:
                    self.memory.memory_write(
                        content=f"Created file: {tool_input['path']}",
                        tier="l3", importance=3, category="file_operations",
                        session_id=self.session_id
                    )

            else:
                result = {"error": f"Unknown tool: {tool_name}"}
                success = False
            
            # Track pattern
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            self.evolution.track_pattern(
                pattern_name=f"tool_{tool_name}",
                success=success,
                metadata={"duration_seconds": duration}
            )
            
            return result
        
        except Exception as e:
            self.evolution.track_pattern(
                pattern_name=f"tool_{tool_name}",
                success=False,
                metadata={"error": str(e)}
            )
            return {"error": str(e), "success": False}
    
    def process_input(self, user_input: str) -> str:
        """Main processing - Claude decides, tools execute, evolution tracks"""

        # Mode switches — never clear self.messages, session context must survive mode changes
        cmd = user_input.lower().strip()
        cmd_clean = re.sub(r"[?!.,;:]+", "", cmd).strip()
        words = cmd_clean.split()

        # T-015: Loose mode-switch detection. The previous strict matcher silently
        # ignored natural variants like "can u switch to root mode ?", which left
        # the agent in normie mode while the user believed they were in root.
        # Match any short message (≤ 8 words) containing the mode phrase.
        if len(words) <= 8:
            if "root mode" in cmd_clean:
                self.mode = "root"
                return "Root mode active (Claude with tools)"
            if "normie mode" in cmd_clean:
                self.mode = "normie"
                return "Normie mode active (Groq, free)"

        if cmd == "analyze performance":
            return self._performance_report()
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

    def _respond_root(self, user_input: str, interaction_start, tool_calls_made: list) -> str:
        """Root mode: Claude with full tool loop"""
        system_prompt = self._get_system_prompt()

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

        return final_text

    def _truncate_messages_safely(self, max_messages: int = 20):
        """T-012: Bound message history without orphaning tool_result blocks.
        Walk forward from the naive slice point to a plain user text message."""
        if len(self.messages) <= max_messages:
            return
        start = len(self.messages) - max_messages
        while start < len(self.messages):
            msg = self.messages[start]
            if msg["role"] == "user" and isinstance(msg.get("content"), str):
                break
            start += 1
        self.messages = self.messages[start:]

    def _extract_text_from_messages(self, n: int = 10) -> str:
        """Extract readable text from self.messages for Groq context"""
        lines = []
        for msg in self.messages[-n:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                lines.append(f"{role}: {content[:300]}")
            elif isinstance(content, list):
                for block in content:
                    if hasattr(block, "text"):
                        lines.append(f"{role}: {block.text[:300]}")
                    elif isinstance(block, dict) and block.get("type") == "tool_result":
                        lines.append(f"tool_result: {str(block.get('content', ''))[:100]}")
        return "\n".join(lines)

    def _respond_normie(self, user_input: str, interaction_start) -> str:
        """Normie mode: Groq, no tools"""
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

        try:
            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=groq_messages,
                max_tokens=2048
            )
            content = response.choices[0].message.content
        except Exception as e:
            content = f"[Pi] Groq error: {str(e)}"

        # T-016: Persist assistant turn to unified store as well.
        self.messages.append({"role": "assistant", "content": content})
        self._truncate_messages_safely(20)

        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": content})

        self.evolution.log_interaction(
            user_input=user_input,
            pi_response=content,
            tool_calls=[],
            success=True,
            mode=self.mode,
            cost=0.0,
            model="groq",
            tokens_in=0,
            tokens_out=0,
            metadata={"duration_seconds": (datetime.now(timezone.utc) - interaction_start).total_seconds(), "session_id": self.session_id}
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
    
    def _health_check(self):
        """Verify all systems are operational on startup"""
        checks = []

        try:
            self.memory.supabase.table("l3_active_memory").select("id").limit(1).execute()
            checks.append(("Supabase", "✓"))
        except Exception as e:
            checks.append(("Supabase", f"✗ {str(e)[:50]}"))

        try:
            import sqlite3
            conn = sqlite3.connect(self.memory.sqlite_path)
            conn.execute("SELECT 1")
            conn.close()
            checks.append(("SQLite", "✓"))
        except Exception as e:
            checks.append(("SQLite", f"✗ {str(e)[:50]}"))

        checks.append(("Anthropic Key", "✓" if ANTHROPIC_API_KEY else "✗ Missing"))
        checks.append(("Groq Key", "✓" if GROQ_API_KEY else "✗ Missing"))
        checks.append(("Supabase Key", "✓" if SUPABASE_KEY else "✗ Missing"))

        print("\n[Health Check]")
        for system, status in checks:
            print(f"  {system}: {status}")
        print()

    def _check_monthly_review(self):
        """Check if monthly self-review is due and prompt Ash"""
        marker_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "last_review.json")
        now = datetime.now(timezone.utc)

        data = {}
        if os.path.exists(marker_path):
            with open(marker_path, 'r') as f:
                try:
                    data = json.load(f)
                except Exception:
                    data = {}

        def _days_since(key):
            val = data.get(key)
            if not val:
                return 9999
            return (now - datetime.fromisoformat(val)).days

        # Don't prompt if reviewed in last 30 days or declined in last 7 days
        if _days_since("last_review") <= 30:
            return
        if _days_since("last_declined") <= 7:
            return

        print("\n" + "="*60)
        print("  MONTHLY SELF-REVIEW DUE")
        print("="*60)
        response = input("Pi has been running 30+ days. Run self-review? (yes/no): ").strip().lower()

        os.makedirs(os.path.dirname(marker_path), exist_ok=True)

        if response in ['yes', 'y']:
            analysis = self.evolution.analyze_performance(days=30)
            if "error" not in analysis:
                improvements = self.evolution.identify_improvements(analysis)
                if improvements:
                    print("\nImprovement opportunities identified:")
                    for imp in improvements:
                        print(f"  [{imp['severity'].upper()}] {imp['issue']}")

                    proposal = self.evolution.propose_consciousness_update(improvements)
                    if proposal:
                        print(f"\nProposed consciousness update:\n{proposal}")
                        approve = input("Approve and apply? (yes/no): ").strip().lower()
                        if approve in ['yes', 'y']:
                            print("[Pi] Auto-modification not yet implemented. Manual review required.")
                else:
                    print("[Pi] No improvements needed. Performance is good.")
            else:
                print(f"[Pi] {analysis['error']}")

            data["last_review"] = now.isoformat()
        else:
            data["last_declined"] = now.isoformat()

        with open(marker_path, 'w') as f:
            json.dump(data, f)

    def _generate_session_summary(self) -> str:
        """Summarize the session using Groq (free). Falls back to self.history if messages empty."""
        try:
            context = self._extract_text_from_messages(n=12)

            # Fallback: use string-only history if messages gave nothing
            if not context and self.history:
                lines = []
                for h in self.history[-12:]:
                    lines.append(f"{h['role']}: {str(h.get('content', ''))[:300]}")
                context = "\n".join(lines)

            if not context:
                return ""

            response = self.groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{
                    "role": "user",
                    "content": f"Summarize this conversation in 2-3 sentences for future reference:\n\n{context}"
                }],
                max_tokens=150
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[Pi] Summary generation failed: {e}")
            return ""

    def run(self):
        """Main loop"""

        print("\n" + "="*60)
        print("PI AGENT v2.0 - Autonomous Intelligence")
        print("="*60)
        print(f"Mode: {self.mode}")
        print("Commands: 'root mode', 'normie mode', 'research mode', 'analyze performance', 'exit'")
        print("="*60 + "\n")

        while True:
            try:
                user_input = input("Ash: ").strip()

                if not user_input:
                    continue

                response = self.process_input(user_input)

                if response == "EXIT":
                    print("[Pi] Shutting down...")

                    # Write session summary before exit
                    if self.messages or self.history:
                        summary = self._generate_session_summary()
                        if summary:
                            self.memory.memory_write(
                                content=f"Session summary ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}): {summary}",
                                tier="l3",
                                importance=4,
                                category="session_history",
                                session_id=self.session_id
                            )
                            print("[Memory] Session summary saved")

                    recent = self.evolution.get_recent_interactions(hours=24)
                    total_cost = sum(i.get("cost", 0) for i in recent)
                    if total_cost > 0:
                        print(f"[Session Cost: ${total_cost:.4f}]")
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