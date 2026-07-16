"""Mode-switch detection — loose matcher (S-010/T-015).

Mechanical lift from the mode-switch block at the top of PiAgent.process_input
(Phase 4) — no behaviour change.

ModeConfig dataclass + registry added per ADR-001 (R1/T-082). Modes are
declarative config consumed by the unified `_respond_via_config` path.
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
# ModeConfig — declarative per-mode configuration (ADR-001 / T-082)
# ---------------------------------------------------------------------------
#
# A single ModeConfig instance per mode drives the unified `_respond_via_config`
# path in pi_agent.py — no per-mode code forks.


@dataclass(frozen=True)
class ModeConfig:
    """Per-mode configuration.

    `memory_db=None` means use the default public pi.db. A non-None value
    combined with `memory_namespace != "pi"` opts the mode out of Supabase
    sync entirely, preserving privacy-by-file-separation (the mechanism the
    profile namespaces reuse).

    `tool_allowlist=None` admits all registered tools; `()` means no tools;
    a non-empty tuple is an explicit whitelist.

    R8 (T-089 / ADR-004) added behavior-flag fields so `_respond_via_config`
    in pi_agent.py can serve every mode from one code path.

    Handoff asymmetry: `builds_handoff_on_exit` (normie does this when
    leaving) and `consumes_handoff_on_first_turn` (root does this when
    entering) are two booleans rather than one because the action is
    different on each side — collapsing them would force build-vs-consume
    mode-checking elsewhere.
    """
    name: str
    prompt_path: str                            # e.g. "prompts/consciousness.txt"
    memory_db: Optional[str]                    # None = default public pi.db
    memory_namespace: str                       # "pi" (default) or a private namespace
    vault_path: str
    tickets_dir: str
    router_tier: str                            # "default" | "fast" | "cheap" | "premium"
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
    ctx_message_window: Optional[int] = None    # None = send full self.messages (root); int N = send last N msgs, safe-sliced (normie, T-149)
    refusal_hint: str = ""                      # T-194: one-line mode-specific rule injected into dynamic segment
    inject_repo_map: bool = False               # T-185: auto-inject compact repo map for code-shaped turns (root only)
    ctx_token_budget: Optional[int] = None     # T-184: token-budget for compaction; None = use legacy count threshold


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
        # R8 / ADR-004: root prefetches L3 + consumes any pending normie→root
        # handoff context on the first post-switch turn.
        prefetch_memory=True,
        # T-211: root has tools and its rule is "Act, don't describe" — it must
        # always reason through the LLM + tool loop, never short-circuit to a
        # cached snapshot. The shortcut hijacked "Add the ticker" (→ markets)
        # before create_ticket could fire. Shortcut stays ON only for normie.
        awareness_shortcut=False,
        consumes_handoff_on_first_turn=True,
        use_split_prompt=True,
        refusal_hint="Root mode: all tools active. Act, don't describe.",
        inject_repo_map=True,
        ctx_token_budget=24000,
    ),
    "normie": ModeConfig(
        name="normie",
        prompt_path="prompts/consciousness_normie.txt",
        memory_db=None,
        memory_namespace="pi",
        vault_path="vault",
        tickets_dir="tickets",
        router_tier="cheap",
        supports_tools=True,
        # T-201: READ-TIER allowlist — generic capabilities constant across modes.
        # The cost boundary (cheap vs default router tier) is the only split.
        # WRITE/side-effect tools stay root-only: file edits, code execution,
        # computer-use, browser automation, gmail_send, calendar_create/delete,
        # generate_video.
        # Note: a tool's internal API calls (e.g. analyze_media hitting a vision
        # API) are governed by the tool's own provider + the daily cap, not the
        # conversation model tier.
        tool_allowlist=(
            # Memory
            "memory_read", "memory_write", "memory_search_semantic",
            # Web
            "web_search", "fetch", "web_browse",
            # Documents + media analysis (fixes Telegram image refusal — T-201)
            "read_document", "analyze_document_smart",
            "analyze_media", "analyze_image", "analyze_images", "analyze_video",
            "transcribe_file", "ocr_image",
            # detect_faces / list_registered_faces / recognize_face excluded from normie:
            # biometric tools require a privacy story + accuracy test before broader exposure (T-171).
            # They remain available in root mode (no allowlist restriction).
            # Ambient info
            "get_weather", "get_news", "get_stocks", "get_tech_updates", "get_location",
            # Calendar (read-only)
            "calendar_today", "calendar_upcoming", "calendar_search",
            # Gmail (read-only)
            "gmail_inbox", "gmail_search", "gmail_read",
            # Obsidian (read-only)
            "obsidian_read", "obsidian_search",
            # Session awareness
            "get_session_stats", "system_introspect", "reflect", "refresh_awareness",
            "daily_briefing",
            # Research
            "scholar_search", "reddit_browse", "reddit_search", "reddit_thread",
            "grounded_search",  # T-227: Gemini Google-Search grounding with citations
            "deep_research",    # T-228: multi-source synthesis + cross-validation
            # Telegram interaction (reactions + file delivery for image_gen)
            "telegram_react", "telegram_send",
            # Image gen (Pollinations is free; auto-delivered via telegram_send)
            "image_gen",
            # Ticket creation (safe structured write)
            "create_ticket",
        ),
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
        refusal_hint="Normie: read-only tools only. For writes/runs/sends tell Ash to type 'root mode'.",
    ),
}


def get_mode_config(name: str) -> ModeConfig:
    """Look up a mode by name. Falls back to 'root' for unknown names.

    Unknown names default to root because that's the user-facing default
    mode and the most permissive — failure-mode is "tools available you
    didn't expect," not "tools missing you needed."
    """
    return MODE_CONFIGS.get(name, MODE_CONFIGS["root"])
