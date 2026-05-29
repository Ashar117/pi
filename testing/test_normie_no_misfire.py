"""
testing/test_normie_no_misfire.py — T-024: normie mode must NOT misfire on greetings.

Evidence from real chat session:
    Ash: hey sup
    Pi:  Not in this conversation. Switch to root mode — memory tools work there.

A bare greeting has nothing to do with memory. The normie refusal table must only
trigger on actual memory-action verbs, not on casual openers.

@pytest.mark.costly — hits real Groq API (~$0.00 but live network).
Run once per prompt change; do not add to the per-commit regression suite.
"""
import sys
import os
import re
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── phrases that prove misfire ────────────────────────────────────────────────

MISFIRE_PHRASES = [
    "switch to root mode",
    "memory tools",
    "not in this conversation",
    "l3",
    "l2",
    "l1",
    "i can't persist",
    "cannot persist",
    "can't persist",
    "root mode",
    "normie mode",
    "stored",
    "memory",
]

# ── phrases that prove a normal greeting ─────────────────────────────────────

GREETING_WORDS = [
    "hey", "hi", "hello", "sup", "what's up", "what up",
    "good", "morning", "evening", "afternoon",
    "how are you", "how's it", "doing",
    "yo", "alright", "okay", "great",
]

GREETINGS_TO_TEST = [
    "hey",
    "sup",
    "good morning",
    "how are you",
    "what's up",
    "hey sup",
]


def _misfire_match(text: str) -> str | None:
    """Return the first misfire phrase found in text, or None."""
    lower = text.lower()
    for phrase in MISFIRE_PHRASES:
        if phrase in lower:
            return phrase
    return None


def _looks_like_greeting_response(text: str) -> bool:
    """True if response is short casual chat or contains a greeting word."""
    if len(text.strip()) <= 80:
        return True
    lower = text.lower()
    return any(word in lower for word in GREETING_WORDS)


@pytest.fixture(scope="module")
def normie_agent():
    """Spin up agent in normie mode, suppress monthly-review prompt."""
    from unittest.mock import patch

    def fake_input(prompt=""):
        return "n"

    with patch("builtins.input", side_effect=fake_input):
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.mode = "normie"
    return agent


# ── parametrised misfire tests ────────────────────────────────────────────────

@pytest.mark.parametrize("greeting", GREETINGS_TO_TEST)
@pytest.mark.costly
def test_greeting_no_memory_refusal(normie_agent, greeting):
    """Greetings must NOT trigger the memory refusal / 'switch to root' response."""
    response = normie_agent.process_input(greeting)
    misfire = _misfire_match(response)
    assert misfire is None, (
        f"Normie misfired on greeting {greeting!r}.\n"
        f"Triggered phrase: {misfire!r}\n"
        f"Full response: {response!r}"
    )


@pytest.mark.parametrize("greeting", GREETINGS_TO_TEST)
@pytest.mark.costly
def test_greeting_looks_like_normal_chat(normie_agent, greeting):
    """Greetings should get a short casual reply, not a wall of refusal text."""
    response = normie_agent.process_input(greeting)
    assert _looks_like_greeting_response(response), (
        f"Normie gave an abnormally long or off-topic response to {greeting!r}.\n"
        f"Response ({len(response)} chars): {response!r}"
    )
