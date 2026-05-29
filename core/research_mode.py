import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from groq import Groq
from google import genai as google_genai
from google.genai import types
from app.config import (
    ANTHROPIC_API_KEY,
    GROQ_API_KEY,
    GEMINI_API_KEY,
    GROQ_MODEL,
    GEMINI_MODEL
)

# Initialise clients
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)
google_client = google_genai.Client(api_key=GEMINI_API_KEY)

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_BASE = open(os.path.join(_project_root, "prompts", "system.txt")).read()

AGENT_PERSONAS = {
    "claude": "You are Claude, representing Anthropic's perspective. You are rigorous, nuanced, and careful. You must directly address and critique other agents' points.",
    "gemini": "You are Gemini, representing Google's perspective. You are broad, fast, and practical. You must directly address and critique other agents' points.",
    "groq": "You are Llama running on Groq, representing Meta's open-source perspective. You are direct and efficient. You must directly address and critique other agents' points."
}

def _ask_claude_research(prompt: str) -> str:
    try:
        msg = claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_BASE + "\n\n" + AGENT_PERSONAS["claude"],
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"[Claude error: {e}]"

def _ask_gemini_research(prompt: str) -> str:
    """
    Ask Gemini with improved error handling and quota detection.
    Returns response or clear error message.
    """
    import time
    
    GEMINI_MODELS = [
        "gemini-2.0-flash-exp",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b"
    ]
    
    for model_name in GEMINI_MODELS:
        try:
            response = google_client.models.generate_content(
                model=model_name,
                contents=AGENT_PERSONAS["gemini"] + "\n\n" + prompt
            )
            
            # Successfully got response
            if model_name != GEMINI_MODELS[0]:
                print(f"[Pi] Gemini using fallback: {model_name}")
            
            return response.text
            
        except Exception as e:
            err_str = str(e).lower()
            
            # Check for actual quota exhaustion
            if "429" in str(e) or "quota" in err_str or "resource_exhausted" in err_str:
                # Try next model in list
                if model_name == GEMINI_MODELS[-1]:
                    # Last model also quota exhausted
                    return "[Gemini quota exhausted - resets in ~1 hour]"
                else:
                    print(f"[Pi] {model_name} quota hit, trying next model...")
                    continue
            
            # Check for rate limiting (temporary)
            elif "rate" in err_str or "limit" in err_str:
                print(f"[Pi] {model_name} rate limited, waiting 5s...")
                time.sleep(5)
                # Retry same model once
                try:
                    response = google_client.models.generate_content(
                        model=model_name,
                        contents=AGENT_PERSONAS["gemini"] + "\n\n" + prompt
                    )
                    return response.text
                except:
                    # Failed retry, try next model
                    continue
            
            # Other errors - try next model
            else:
                print(f"[Pi] {model_name} error: {str(e)[:50]}... trying next")
                continue
    
    # All models failed
    return "[Gemini unavailable - all models exhausted or errored]"

def _ask_groq_research(prompt: str) -> str:
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_BASE + "\n\n" + AGENT_PERSONAS["groq"]},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1024
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"[Groq error: {e}]"

def _display_response(agent: str, round_num: int, response: str):
    divider = "─" * 60
    print(f"\n{divider}")
    print(f"  {agent.upper()} — Round {round_num}")
    print(divider)
    print(response)

def _estimate_cost(rounds: int) -> str:
    # Claude Sonnet rough estimate per round
    est = rounds * 0.02
    return f"~${est:.2f}"

def run_research_mode(question: str, rounds: int = 2, context: str = "") -> str:
    """Returns synthesis text for caller to optionally save to memory"""
    print(f"\n{'='*60}")
    print(f"  PI RESEARCH MODE — {rounds} rounds")
    print(f"  Agents: Claude + Gemini + Groq/Llama")
    print(f"  Est. cost: {_estimate_cost(rounds)}")
    print(f"{'='*60}")
    print(f"\nQuestion: {question}\n")

    input("Press Enter to begin research session...")

    # Round 1 — independent answers
    print("\n[Pi] Sending to all agents simultaneously...")

    # Build context-aware prompt
    context_section = ""
    if context:
        context_section = f"\nCONVERSATION CONTEXT (last 10 messages):\n{context}\n\n"

    round1_prompt = f"""Question for research debate: {question}
{context_section}
Keep response under 150 words. No headers. No bold text. Plain text only.
POSITION: (one sentence)
REASONING: (4-5 sentences max)
KEY POINTS: (3 bullet points, one line each)"""

    claude_r1 = _ask_claude_research(round1_prompt)
    _display_response("Claude", 1, claude_r1)

    gemini_r1 = _ask_gemini_research(round1_prompt)
    if "unavailable" not in gemini_r1.lower() and "exhausted" not in gemini_r1.lower():
        _display_response("Gemini", 1, gemini_r1)
    else:
        print(f"\n[Pi] {gemini_r1}\n")
        print("[Pi] Running 2-agent debate (Claude + Groq).\n")
        gemini_r1 = ""

    groq_r1 = _ask_groq_research(round1_prompt)
    _display_response("Groq/Llama", 1, groq_r1)

    if rounds < 2:
        return _synthesize(question, claude_r1, gemini_r1, groq_r1)

    # Round 2 — debate
    print(f"\n\n{'='*60}")
    print("  ROUND 2 — DEBATE")
    print(f"{'='*60}")
    print("[Pi] Agents reviewing each other's positions...")

    debate_context = ""
    if context:
        debate_context = f"\nORIGINAL CONTEXT:\n{context}\n\n"

    debate_prompt_claude = f"""Original question: {question}
{debate_context}
You said: {claude_r1}

Gemini said: {gemini_r1}

Groq/Llama said: {groq_r1}

Now respond to the debate. You MUST:
1. Identify what you AGREE with from the other agents and why
2. Identify what you DISAGREE with and why specifically
3. Either defend or revise your original position
4. State your final position clearly
Under 150 words. No headers. No bold. Plain text only."""
    

    debate_prompt_gemini = f"""Original question: {question}
{debate_context}
Claude said: {claude_r1}

You said: {gemini_r1}

Groq/Llama said: {groq_r1}

Now respond to the debate. You MUST:
1. Identify what you AGREE with from the other agents and why
2. Identify what you DISAGREE with and why specifically
3. Either defend or revise your original position
4. State your final position clearly
Under 150 words. No headers. No bold. Plain text only."""

    debate_prompt_groq = f"""Original question: {question}
{debate_context}
Claude said: {claude_r1}

Gemini said: {gemini_r1}

You said: {groq_r1}

Now respond to the debate. You MUST:
1. Identify what you AGREE with from the other agents and why
2. Identify what you DISAGREE with and why specifically
3. Either defend or revise your original position
4. State your final position clearly
Under 150 words. No headers. No bold. Plain text only."""

    claude_r2 = _ask_claude_research(debate_prompt_claude)
    _display_response("Claude", 2, claude_r2)

    gemini_r2 = _ask_gemini_research(debate_prompt_gemini)
    if "unavailable" not in gemini_r2.lower() and "exhausted" not in gemini_r2.lower():
        _display_response("Gemini", 2, gemini_r2)
    else:
        print("\n[Gemini unavailable for Round 2. Using Round 1 position.]\n")
        gemini_r2 = gemini_r1  # Use Round 1 if Round 2 fails

    groq_r2 = _ask_groq_research(debate_prompt_groq)
    _display_response("Groq/Llama", 2, groq_r2)

    # Synthesis
    return _synthesize(question, claude_r2, gemini_r2, groq_r2)

def _synthesize(question: str, claude_ans: str, gemini_ans: str, groq_ans: str) -> str:
    print(f"\n\n{'='*60}")
    print("  PI SYNTHESIS — FINAL VERDICT")
    print(f"{'='*60}")
    print("[Pi] Synthesizing all positions...")

    synthesis_prompt = f"""You are Pi, synthesizing a multi-agent research debate for Ash.

Question: {question}

Claude's final position: {claude_ans}
Gemini's final position: {gemini_ans if gemini_ans else "Unavailable this session."}
Groq/Llama's final position: {groq_ans}

Produce a synthesis for Ash:
CONSENSUS: What all agents agreed on
DISSENT: Where they disagreed and why
STRONGEST ARGUMENT: Which agent made the most compelling case and why
RECOMMENDATION: Your synthesis recommendation for Ash
CONFIDENCE: High / Medium / Low and why"""

    verdict = _ask_groq_research(synthesis_prompt)
    print(f"\n{verdict}")
    print(f"\n{'='*60}\n")
    return verdict

if __name__ == "__main__":
    run_research_mode(
        "Should Pi use LangGraph or a custom agent framework?",
        rounds=2
    )