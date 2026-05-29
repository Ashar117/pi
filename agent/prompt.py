"""System prompt construction — consciousness + mode block + L3 context.

Supports {{INCLUDE:filename}} directives in consciousness.txt (T-050).
If a directive points to prompts/<filename>, its content is spliced in verbatim.
Missing files are replaced with a one-line warning so startup never crashes.
"""
import os
import re
from datetime import datetime, timezone

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

# T-074: cache the slim normie consciousness — loaded once, reused across turns
_NORMIE_CONSCIOUSNESS_CACHE: str = ""


def _load_normie_consciousness() -> str:
    """Load prompts/consciousness_normie.txt (slim, ~50 lines) once.

    Normie burns ~3-4k input tokens per turn loading the full root consciousness;
    on Groq free tier that exhausts the 100k TPD limit in ~25 turns of real chat.
    The slim version is ~500 tokens, an 8x reduction.
    """
    global _NORMIE_CONSCIOUSNESS_CACHE
    if _NORMIE_CONSCIOUSNESS_CACHE:
        return _NORMIE_CONSCIOUSNESS_CACHE
    path = os.path.join(_PROMPTS_DIR, "consciousness_normie.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            _NORMIE_CONSCIOUSNESS_CACHE = f.read()
    except FileNotFoundError:
        _NORMIE_CONSCIOUSNESS_CACHE = minimal_consciousness()
    return _NORMIE_CONSCIOUSNESS_CACHE


def _resolve_includes(text: str) -> str:
    """Replace {{INCLUDE:filename}} with the contents of prompts/filename."""
    def _sub(m: re.Match) -> str:
        fname = m.group(1).strip()
        path = os.path.join(_PROMPTS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return f"[INCLUDE missing: {fname}]"
    return re.sub(r"\{\{INCLUDE:([^}]+)\}\}", _sub, text)


def minimal_consciousness() -> str:
    """Fallback consciousness used when prompts/consciousness.txt is missing."""
    return """You are Pi, Ash's personal intelligence system.
You are autonomous, direct, and cost-conscious.
You use tools to act, you verify results, you learn from mistakes.
You never hallucinate. You never pretend to know what you don't.
Islamic values are non-negotiable. Quality over speed on critical tasks."""


_ROOT_MODE_BLOCK_STATIC = """
═══════════════════════════════════════════════════════════
CURRENT SESSION STATE
═══════════════════════════════════════════════════════════
MODE: ROOT | MODEL: Claude Sonnet 4.6 | TOOLS: ENABLED
You ARE in root mode. You HAVE tools. Use them when needed.
═══════════════════════════════════════════════════════════"""

_NORMIE_MODE_BLOCK_STATIC = """
═══════════════════════════════════════════════════════════
CURRENT SESSION STATE
═══════════════════════════════════════════════════════════
MODE: NORMIE | MODEL: Groq Llama 3.3 70B | TOOLS: NONE

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


def build_system_prompt_split(consciousness: str, mode: str, memory_tools) -> tuple:
    """Return (static, warm, dynamic) for Anthropic prompt caching (T-091).

    static  — consciousness + mode block; identical every turn in same mode;
              cached for hours via cache_control: ephemeral.
    warm    — L3 ambient context; changes at most once per session;
              cached for minutes via a second cache_control: ephemeral marker.
    dynamic — session timestamp; changes every turn; never cached.
              Prefetch, session context, and handoff are appended by the caller.

    Three-segment split gives two cache points instead of one, reducing token
    cost on the L3 segment (~300 tokens * turns * price).
    """
    # T-074: normie uses a slim consciousness (~500 tokens vs ~3-4k) so the
    # Groq free-tier 100k TPD budget lasts a full day of chat.
    if mode == "normie":
        resolved = _load_normie_consciousness()
        mode_block = _NORMIE_MODE_BLOCK_STATIC
    else:
        resolved = _resolve_includes(consciousness)
        mode_block = _ROOT_MODE_BLOCK_STATIC
    static = resolved + mode_block

    try:
        warm = memory_tools.get_l3_context(max_tokens=300)
    except Exception as e:
        print(f"[Pi] L3 load failed: {e}")
        warm = ""

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dynamic = f"SESSION TIME: {now_str}"

    return static, warm, dynamic


def build_system_prompt(consciousness: str, mode: str, memory_tools) -> str:
    """Build the full system prompt as a single string (used by normie/god modes)."""
    static, warm, dynamic = build_system_prompt_split(consciousness, mode, memory_tools)
    parts = [static]
    if warm:
        parts.append(warm)
    parts.append(dynamic)
    return "\n\n".join(parts)
