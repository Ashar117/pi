"""
Pi Agent - Complete System
Claude as consciousness, tools as capabilities, self-evolution enabled
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
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
from agent.health import run_health_check
from agent.review import check_monthly_review
from agent.truncation import truncate_messages_safely, extract_text_from_messages
from agent.session import generate_session_summary, on_exit
from agent.tools import get_tool_definitions, execute_tool
from agent.prompt import build_system_prompt, minimal_consciousness
from agent.modes import detect_mode_switch


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
        check_monthly_review(self.evolution)

        # Initialize LLM clients
        self.claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.groq = Groq(api_key=GROQ_API_KEY)
        
        # State
        self.mode = DEFAULT_MODE
        self.messages = []   # Persistent API message list (raw content blocks preserved)
        self.history = []    # Simplified string-only history for research mode context
        self.session_start = datetime.now(timezone.utc)
        self.session_id = uuid.uuid4().hex[:8]  # T-013: short ID for log correlation
        
        run_health_check(self.memory.supabase, self.memory.sqlite_path,
                         ANTHROPIC_API_KEY, GROQ_API_KEY, SUPABASE_KEY)
        print(f"[Pi] Agent initialized - {self.session_start.strftime('%Y-%m-%d %H:%M')}")
        print(f"[Pi] Session ID: {self.session_id}")
        print(f"[Pi] Mode: {self.mode}")
        print(f"[Pi] Consciousness loaded: {len(self.consciousness)} chars")
    
    def _minimal_consciousness(self) -> str:
        """Thin wrapper preserving the method API; logic in agent.prompt."""
        return minimal_consciousness()
    
    def _get_system_prompt(self) -> str:
        """Thin wrapper preserving the method API; logic in agent.prompt."""
        return build_system_prompt(self.consciousness, self.mode, self.memory)
    
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
        """Main processing - Claude decides, tools execute, evolution tracks"""

        # Mode-switch detection (loose matcher, S-010/T-015) — never clear self.messages,
        # session context must survive mode changes (L-001).
        switch = detect_mode_switch(user_input)
        if switch is not None:
            self.mode, response = switch
            return response

        cmd = user_input.lower().strip()

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
        """Thin wrapper preserving the method API; logic in agent.truncation."""
        self.messages = truncate_messages_safely(self.messages, max_messages)

    def _extract_text_from_messages(self, n: int = 10) -> str:
        """Thin wrapper preserving the method API; logic in agent.truncation."""
        return extract_text_from_messages(self.messages, n)

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
    
    def _generate_session_summary(self) -> str:
        """Thin wrapper preserving the method API; logic in agent.session."""
        return generate_session_summary(self.groq, self.messages, self.history, n=12)

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