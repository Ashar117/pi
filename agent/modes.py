"""Mode-switch detection — loose matcher (S-010/T-015).

Mechanical lift from the mode-switch block at the top of PiAgent.process_input
(Phase 4) — no behaviour change.

ModeConfig dataclass + registry added per ADR-001 (R1/T-082). The configs are
declarative only at this stage — `build_system_prompt` and `_respond_god` keep
reading their paths directly until step 6 of the migration plan wires the
unified `_respond` to consume them.
"""
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

SWITCH_VERBS = {"switch", "go", "enter", "activate", "use", "into", "to", "now"}


def detect_mode_switch(user_input: str) -> Optional[Tuple[str, str]]:
    """Detect a mode-switch intent in user input.

    Returns (target_mode, response_string) if recognised, else None.

    Triggers when a short message (≤8 words after stripping punctuation) contains
    'root' or 'normie' AND any of: ≤3 words total, the literal word 'mode', or
    any switch verb (switch, go, enter, activate, use, into, to, now).

    Bias is toward switching — false negatives strand the user in the wrong mode
    while they think they're in the other, which cascades into LLM-mimed tool
    calls (LOG1/LOG2 hallucination, T-019).
    """
    cmd = user_input.lower().strip()
    cmd_clean = re.sub(r"[?!.,;:]+", "", cmd).strip()
    words = cmd_clean.split()

    target_mode = None
    if "root" in words:
        target_mode = "root"
    elif "normie" in words:
        target_mode = "normie"

    if target_mode and 1 <= len(words) <= 8:
        is_switch_command = (
            len(words) <= 3
            or "mode" in words
            or any(v in words for v in SWITCH_VERBS)
        )
        if is_switch_command:
            response = (
                "Root mode active (Claude with tools)"
                if target_mode == "root"
                else "Normie mode active (Groq, free)"
            )
            return target_mode, response

    return None


# ---------------------------------------------------------------------------
# ModeConfig — declarative per-mode configuration (ADR-001 / T-082 step 2)
# ---------------------------------------------------------------------------
#
# Goal: replace the parallel `agent/god.py` fork with a single ModeConfig
# instance consumed by a unified `_respond` path in pi_agent.py. This step
# adds the dataclass + registry only; no call sites change. Wire-up lands
# in step 6.
#
# Privacy is preserved by .gitignore + config-driven paths, never by code
# duplication (P7, the literal R1 lesson). All four private paths
# (data/god_memory.db, prompts/god_consciousness.txt, tickets/god/,
# vault/.god/) are referenced exclusively through MODE_CONFIGS["god"].


@dataclass(frozen=True)
class ModeConfig:
    """Per-mode configuration.

    `memory_db=None` means use the default public pi.db. A non-None value
    combined with `memory_namespace != "pi"` opts the mode out of Supabase
    sync entirely (god mode), preserving privacy-by-file-separation.

    `tool_allowlist=None` admits all registered tools; `()` means no tools;
    a non-empty tuple is an explicit whitelist. For god mode the allowlist
    is for planner-menu minimalism — `execute_bash` is in it, so privacy
    isn't enforced here; .gitignore + namespace is the real boundary.

    R8 (T-089 / ADR-004) added behavior-flag fields so `_respond_via_config`
    in pi_agent.py can serve all three modes from one code path.

    Handoff asymmetry: `builds_handoff_on_exit` (normie does this when
    leaving) and `consumes_handoff_on_first_turn` (root does this when
    entering) are two booleans rather than one because the action is
    different on each side — collapsing them would force build-vs-consume
    mode-checking elsewhere.
    """
    name: str
    prompt_path: str                            # e.g. "prompts/consciousness.txt"
    memory_db: Optional[str]                    # None = default public pi.db
    memory_namespace: str                       # "pi" | "god"
    vault_path: str
    tickets_dir: str
    router_tier: str                            # "default" | "fast" | "private" | "cheap" | "premium"
    supports_tools: bool
    tool_allowlist: Optional[Tuple[str, ...]]   # None = all; () = none
    max_tokens: int
    public_logging: bool                        # turns land in logs/turns.jsonl + raw_wiki

    # R8 / ADR-004: behavior flags absorbed from the three _respond_* methods.
    prefetch_memory: bool = False               # run _prefetch_memory before the LLM call
    awareness_shortcut: bool = False            # try try_answer_from_awareness before any LLM call
    session_ctx_inject: bool = False            # extract_text_from_messages(n=10) appended to system prompt
    builds_handoff_on_exit: bool = False        # build _normie_handoff_context when leaving this mode
    consumes_handoff_on_first_turn: bool = False  # consume _normie_handoff_context on first turn in this mode
    use_split_prompt: bool = False              # root: send (static, dynamic) tuple for Anthropic cache
    single_message_ctx: bool = False            # DEPRECATED (T-149): send only current turn to LLM. Superseded by ctx_message_window.
    ctx_message_window: Optional[int] = None    # None = send full self.messages (root); int N = send last N msgs, safe-sliced (normie, T-149)


MODE_CONFIGS: Dict[str, ModeConfig] = {
    "root": ModeConfig(
        name="root",
        prompt_path="prompts/consciousness.txt",
        memory_db=None,
        memory_namespace="pi",
        vault_path="vault",
        tickets_dir="tickets",
        router_tier="default",
        supports_tools=True,
        tool_allowlist=None,
        max_tokens=4096,
        public_logging=True,
        # R8 / ADR-004: root prefetches L3 + uses awareness shortcut + consumes
        # any pending normie→root handoff context on the first post-switch turn.
        prefetch_memory=True,
        awareness_shortcut=True,
        consumes_handoff_on_first_turn=True,
        use_split_prompt=True,
    ),
    "normie": ModeConfig(
        name="normie",
        prompt_path="prompts/consciousness_normie.txt",
        memory_db=None,
        memory_namespace="pi",
        vault_path="vault",
        tickets_dir="tickets",
        router_tier="cheap",
        supports_tools=False,
        tool_allowlist=(),
        max_tokens=2048,
        public_logging=True,
        # T-149: normie now sends a real bounded multi-turn message array
        # (ctx_message_window) instead of single_message_ctx + a lossy
        # 300-char session_ctx string. The OpenAI-compatible providers
        # (Cerebras/Groq) flatten canonical dict content via
        # anthropic_messages_to_openai, so both sides of the conversation are
        # visible. session_ctx_inject is now off — the message array carries
        # the context, and double-injecting wasted tokens. Window=16 (~8 turns)
        # balances coherence against the Groq free-tier TPD budget (router TPD
        # brownout, T-084, still guards the daily cap).
        awareness_shortcut=True,
        ctx_message_window=16,
        builds_handoff_on_exit=True,
        use_split_prompt=True,
        prefetch_memory=True,
    ),
    "god": ModeConfig(
        name="god",
        prompt_path="prompts/god_consciousness.txt",
        memory_db="data/god_memory.db",
        memory_namespace="god",
        vault_path="vault/.god",
        tickets_dir="tickets/god",
        router_tier="private",
        supports_tools=True,
        # Conservative initial allowlist mirrors the 20 tools in agent/god.py
        # plus three read-only adds (system_introspect, get_session_stats,
        # reflect). Side-effectful or auth-bound tools (gmail_*, telegram_*,
        # calendar_*, computer_*, browser_*, scholar_search, image_gen,
        # daily_briefing, watcher_*, listen, speak) stay out. Expansion
        # requires explicit user request — leaky-by-execute_bash means the
        # allowlist is for planner accuracy, not privacy.
        tool_allowlist=(
            "read_file", "modify_file", "create_file",
            "execute_python", "execute_bash",
            "memory_read", "memory_write", "memory_delete",
            "memory_search_semantic",  # T-097: cosine retrieval, read-only
            "search_codebase", "repo_map", "system_introspect",
            "web_search", "web_browse",
            "create_ticket", "get_session_stats",
            "obsidian_read", "obsidian_write", "obsidian_append", "obsidian_search",
            "reflect",
        ),
        max_tokens=4096,
        public_logging=False,
    ),
}


def get_mode_config(name: str) -> ModeConfig:
    """Look up a mode by name. Falls back to 'root' for unknown names.

    Unknown names default to root because that's the user-facing default
    mode and the most permissive — failure-mode is "tools available you
    didn't expect," not "tools missing you needed."
    """
    return MODE_CONFIGS.get(name, MODE_CONFIGS["root"])
