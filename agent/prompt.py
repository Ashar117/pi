"""System prompt construction — consciousness + mode block + L3 context.

Mechanical lift from PiAgent._get_system_prompt and PiAgent._minimal_consciousness
(Phase 4) — no behaviour change. Box-drawing characters use the same \\u2550
escape sequences as the original to keep the rendered output byte-identical.
"""
from datetime import datetime, timezone


def minimal_consciousness() -> str:
    """Fallback consciousness used when prompts/consciousness.txt is missing."""
    return """You are Pi, Ash's personal intelligence system.
You are autonomous, direct, and cost-conscious.
You use tools to act, you verify results, you learn from mistakes.
You never hallucinate. You never pretend to know what you don't.
Islamic values are non-negotiable. Quality over speed on critical tasks."""


def build_system_prompt(consciousness: str, mode: str, memory_tools) -> str:
    """Build the full system prompt: consciousness + mode block + L3 context.

    Args:
        consciousness: the loaded consciousness.txt text (or minimal fallback)
        mode: "root" or "normie"
        memory_tools: a MemoryTools instance (used for get_l3_context)
    """
    try:
        l3_context = memory_tools.get_l3_context(max_tokens=800)
    except Exception as e:
        print(f"[Pi] L3 load failed: {e}")
        l3_context = ""

    if mode == "root":
        mode_block = f"""
═══════════════════════════════════════════════════════════
CURRENT SESSION STATE
═══════════════════════════════════════════════════════════
MODE: ROOT | MODEL: Claude Sonnet 4.6 | TOOLS: All 7 ENABLED
SESSION TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
You ARE in root mode. You HAVE tools. Use them when needed.
═══════════════════════════════════════════════════════════"""
    else:
        mode_block = f"""
═══════════════════════════════════════════════════════════
CURRENT SESSION STATE
═══════════════════════════════════════════════════════════
MODE: NORMIE | MODEL: Groq Llama 3.3 70B | TOOLS: NONE
SESSION TIME: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

DEFAULT BEHAVIOUR: respond normally. Greetings, small talk, factual
questions, analysis, writing — just answer. Do NOT volunteer that you are
in normie mode or announce limitations that were not asked about.

REFUSAL TABLE — consult this ONLY when the user's message contains an
explicit tool-action keyword. Keyword → one-line refusal, that's it.

  Keyword(s)          Refusal
  ──────────────────  ────────────────────────────────────────────────
  remember / save /   "Can't persist in normie mode. Switch to root
  store / note this   mode and tell me again."
  what did I tell /   Answer from visible conversation only. If absent:
  do you recall /     "Not stored here — switch to root mode."
  did I tell you
  run this / execute  "Can't run code in normie mode. Switch to root."
  / python / bash
  read file / modify  "Can't access files in normie mode. Switch to root."
  file / create file
  switch to root /    "Type 'root mode' yourself — I can't flip modes
  root mode           from inside a response."

HARD RULES (always apply, no exceptions):
- Never say "I've stored", "saved to L3/L2/L1", "I'll remember", or any
  phrase claiming persistence. You cannot persist anything here.
- Never print fake MODE SWITCH or MODE: ROOT banners.
═══════════════════════════════════════════════════════════"""

    return f"{consciousness}{mode_block}\n\n{l3_context}"
