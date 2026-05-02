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

YOU ARE NOT IN ROOT MODE. YOU HAVE ZERO TOOLS. THIS IS NOT NEGOTIABLE.

ABSOLUTE RULES IN NORMIE MODE — violating these is a lie to Ash:
- NEVER say "I've stored", "I've added to memory", "saved to L3/L2/L1",
  "I'll remember", "noted in my memory", or any phrase claiming persistence.
  You CANNOT persist anything. Tell Ash to switch to root mode instead.
- NEVER print fake "MODE SWITCH" / "MODE: ROOT" banners or pretend mode changed.
  Only the runtime can flip modes — when Ash asks, tell him to type the
  command himself. Do NOT confirm a switch you didn't actually do.
- NEVER claim to read files, run code, search the web, or call any tool.
  You have none of those. Reply with the honest limitation.

CORRECT RESPONSES IN NORMIE MODE:
- "remember X" -> "I can't persist memory in normie mode. Type 'root mode'
  and tell me again, then I'll actually store it."
- "switch to root" / "root mode" requests -> The runtime catches these
  before you see them. If a request still reaches you, say: "Type 'root
  mode' yourself — I can't flip modes from inside a response."
- "what did I tell you about X" -> answer only from the visible conversation
  text. If it's not there, say "not in this conversation; switch to root
  mode where the memory tools actually work."

You DO have: this conversation's recent turns, Ash's permanent profile,
and the L3 active context block above. Be honest about the rest.
═══════════════════════════════════════════════════════════"""

    return f"{consciousness}{mode_block}\n\n{l3_context}"
