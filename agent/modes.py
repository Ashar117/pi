"""Mode-switch detection — loose matcher (S-010/T-015).

Mechanical lift from the mode-switch block at the top of PiAgent.process_input
(Phase 4) — no behaviour change.
"""
import re
from typing import Optional, Tuple

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
