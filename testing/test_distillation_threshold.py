"""
testing/test_distillation_threshold.py — T-029: distillation minimum importance
threshold must reject small talk and low-value observations.

Evidence: "[Distill] 12 facts written to L2, 0 skipped" from a 45-min session.
Root cause 1: threshold is importance < 4 — accepts everything importance 4+,
              which is half the scale and includes small talk scored 4-5 by Groq.
Root cause 2: no call to _is_l2_duplicate before writing — same fact re-written
              every session.

Offline — mocks Groq client and MemoryTools. No API calls.
"""
import sys
import os
import json
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.pipeline import distill_session


# ── Synthetic Groq response: 3 real facts + 7 junk ───────────────────────────

_MOCK_FACTS = [
    # Genuine facts — importance 7-9
    {"fact": "User is a CS PhD student at State University", "category": "permanent_profile", "importance": 9},
    {"fact": "User's research deadline is June 15 2026",    "category": "current_priority",  "importance": 8},
    {"fact": "User prefers dark mode and minimal UI",        "category": "permanent_profile", "importance": 7},
    # Small talk / low-value — importance 4-5
    {"fact": "User said hi at the start of the session",    "category": "session_history",   "importance": 4},
    {"fact": "User mentioned they might go to the gym",     "category": "note",               "importance": 4},
    {"fact": "User asked about the weather",                "category": "session_history",    "importance": 4},
    {"fact": "Pi greeted the user back",                   "category": "session_history",    "importance": 4},
    {"fact": "User is doing okay today",                   "category": "note",               "importance": 5},
    {"fact": "User was curious about something",           "category": "note",               "importance": 5},
    {"fact": "Pi said it would help",                      "category": "session_history",    "importance": 4},
]

_L1_ROWS = [
    {"role": "user",      "content": "hey what's up",                       "session_id": "test123"},
    {"role": "assistant", "content": "Hey! Doing well. What can I help with?", "session_id": "test123"},
    {"role": "user",      "content": "I have a research deadline June 15",  "session_id": "test123"},
    {"role": "assistant", "content": "Noted — I'll keep that in mind.",     "session_id": "test123"},
]


def _make_deps():
    """Return (memory_mock, groq_mock) with Groq returning _MOCK_FACTS."""
    memory = MagicMock()
    memory.get_l1_thread.return_value = _L1_ROWS
    memory._is_l2_duplicate.return_value = None  # no duplicates by default

    groq = MagicMock()
    groq.chat.completions.create.return_value.choices[0].message.content = (
        json.dumps(_MOCK_FACTS)
    )
    return memory, groq


# ── Test 1: threshold rejects low-importance facts ────────────────────────────

def test_distillation_rejects_low_importance():
    """Threshold must be >= 6. Current threshold=4 accepts all 10 facts — bug.

    With _MOCK_FACTS: 3 real facts (importance 7-9), 7 junk (importance 4-5).
    The correct behaviour is ≤ 4 written, ≥ 6 skipped.
    The current code writes all 10 (importance >= 4 passes).
    """
    memory, groq = _make_deps()
    result = distill_session(
        thread_id="fake-thread-id",
        session_id="test123",
        memory_tools=memory,
        groq_client=groq,
        dry_run=True,
    )
    assert result["distilled"] <= 4, (
        f"distill_session accepted {result['distilled']}/10 facts but expected ≤ 4. "
        f"7 mock facts have importance 4-5 (small talk) and must be rejected. "
        f"Raise minimum importance threshold from 4 to 6 in memory/pipeline.py."
    )
    assert result["skipped"] >= 6, (
        f"Only {result['skipped']} facts skipped — expected ≥ 6. "
        f"Low-value observations (importance 4-5) are not being filtered out."
    )


# ── Test 2: dedup against L2 before writing ───────────────────────────────────

def test_distillation_calls_l2_dedup_before_write():
    """distill_session must call _is_l2_duplicate before each L2 write.

    Currently the dedup check is skipped entirely — facts that already exist in
    L2 from a previous session get written again every time distillation runs.
    """
    memory, groq = _make_deps()
    memory._is_l2_duplicate.return_value = None  # none are duplicates

    distill_session(
        thread_id="fake-thread-id",
        session_id="test123",
        memory_tools=memory,
        groq_client=groq,
        dry_run=False,
    )

    assert memory._is_l2_duplicate.called, (
        "distill_session must call memory._is_l2_duplicate before each L2 write. "
        "Currently skips this check — same facts accumulate across sessions."
    )


def test_distillation_skips_l2_duplicates():
    """Facts already in L2 must be skipped, not written again.

    Simulate: 3 high-importance facts from Groq, 2 already in L2.
    Expected: 1 written, 2 skipped as duplicates.
    """
    memory, groq = _make_deps()

    # Only the first high-importance fact is a duplicate; the other two are new
    def _dup_side_effect(content, category):
        if "PhD student" in content:
            return "existing-l2-id"
        if "dark mode" in content:
            return "existing-l2-id-2"
        return None

    memory._is_l2_duplicate.side_effect = _dup_side_effect

    result = distill_session(
        thread_id="fake-thread-id",
        session_id="test123",
        memory_tools=memory,
        groq_client=groq,
        dry_run=False,
    )

    # With threshold fix (importance >= 6): 3 facts pass threshold.
    # Of those 3, 2 are duplicates → 1 written, 2 skipped.
    # Without threshold fix: 10 pass, 2 are duplicates → 8 written.
    # This assertion only passes when BOTH fixes are applied.
    assert result["distilled"] == 1, (
        f"Expected 1 fact written (1 high-importance non-duplicate) but got "
        f"{result['distilled']}. Either threshold is still too low or dedup is not called."
    )
