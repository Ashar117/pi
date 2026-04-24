import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ollama
import anthropic
from groq import Groq
from app.config import (
    ANTHROPIC_API_KEY,
    GROQ_API_KEY,
    LOCAL_MODEL,
    GROQ_MODEL,
    DEFAULT_MODE
)
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

def _build_system(profile: str = None) -> str:
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = open(os.path.join(_project_root, "prompts", "system.txt")).read()
    if profile:
        return base + f"\n\nASH'S PERMANENT PROFILE:\n{profile}"
    return base

TASK_TIERS = {
    "simple": "groq",
    "draft": "groq",
    "summary": "groq",
    "research": "cloud",
    "analysis": "cloud",
    "email": "cloud",
    "critical": "cloud",
}

def _decide_tier(mode: str, task_type: str) -> str:
    if mode == "root":
        return "cloud"
    if mode == "offline":
        return "local"
    return TASK_TIERS.get(task_type, "groq")

def _ask_groq(prompt: str, history: list = [], profile: str = None) -> dict:
    try:
        system_prompt = _build_system(profile)
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add system messages from history (L3, summaries)
        for h in history:
            if h["role"] == "system":
                messages.append({"role": "system", "content": h["content"]})
        
        # Add recent conversation messages
        for h in history[-6:]:
            if h["role"] in ("user", "assistant"):
                messages.append({"role": h["role"], "content": h["content"]})
        
        messages.append({"role": "user", "content": prompt})
        
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=1024
        )
        content = response.choices[0].message.content
        return {
            "content": content,
            "model": GROQ_MODEL,
            "tier": "groq",
            "cost": 0.0,
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens
        }
    except Exception as e:
        print(f"[Pi] Groq failed: {e}. Falling back to local.")
        return _ask_local(prompt, history, profile)

def _ask_local(prompt: str, history: list = [], profile: str = None) -> dict:
    try:
        system_prompt = _build_system(profile)
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add system messages from history (L3, summaries)
        for h in history:
            if h["role"] == "system":
                messages.append({"role": "system", "content": h["content"]})
        
        # Add recent conversation messages
        for h in history[-6:]:
            if h["role"] in ("user", "assistant"):
                messages.append({"role": h["role"], "content": h["content"]})
        
        messages.append({"role": "user", "content": prompt})
        
        response = ollama.chat(
            model=LOCAL_MODEL,
            messages=messages
        )
        content = response["message"]["content"]
        return {
            "content": content,
            "model": LOCAL_MODEL,
            "tier": "local",
            "cost": 0.0,
            "tokens_in": 0,
            "tokens_out": 0
        }
    except Exception as e:
        print(f"[Pi] Local failed: {e}")
        return {
            "content": "[Pi] All models failed.",
            "model": "none",
            "tier": "failed",
            "cost": 0.0,
            "tokens_in": 0,
            "tokens_out": 0
        }

def _ask_claude(prompt: str, history: list = [], profile: str = None) -> dict:
    try:
        system_prompt = _build_system(profile)
        
        # Collect all system messages from history and append to system prompt
        system_additions = []
        for h in history:
            if h["role"] == "system":
                system_additions.append(h["content"])
        
        if system_additions:
            system_prompt = system_prompt + "\n\n" + "\n\n".join(system_additions)
        
        # Build messages array (user/assistant only for Claude)
        messages = []
        for h in history[-6:]:
            if h["role"] in ("user", "assistant"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": prompt})
        
        message = claude_client.messages.create(
            model="claude-haiku-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages
        )
        content = message.content[0].text
        tokens_in = message.usage.input_tokens
        tokens_out = message.usage.output_tokens
        cost = (tokens_in * 0.00000025) + (tokens_out * 0.00000125)
        return {
            "content": content,
            "model": "claude-haiku",
            "tier": "cloud",
            "cost": cost,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out
        }
    except Exception as e:
        print(f"[Pi] Claude failed: {e}. Falling back to Groq.")
        return _ask_groq(prompt, history, profile)

def route(prompt: str, mode: str = DEFAULT_MODE, task_type: str = "simple", history: list = [], profile: str = None) -> dict:
    tier = _decide_tier(mode, task_type)
    if tier == "local":
        return _ask_local(prompt, history, profile)
    elif tier == "groq":
        return _ask_groq(prompt, history, profile)
    else:
        return _ask_claude(prompt, history, profile)

if __name__ == "__main__":
    print("Testing Groq (normie)...")
    result = route("who are you", mode="normie", task_type="simple")
    print(f"Model: {result['model']} | Tier: {result['tier']}")
    print(f"Response: {result['content'][:200]}")