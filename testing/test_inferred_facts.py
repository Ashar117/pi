"""
testing/test_inferred_facts.py — T-031: inferred facts must not reach L3 without
explicit user confirmation.

Evidence: Pi inferred "F-1 visa" from "I'm a student" and stored it on "yup".
Earlier it inferred Lawrenceville as home city (wrong — it's just current location).

Root cause: consciousness.txt has no stated-vs-inferred rule; memory_write has no
`source` field; nothing blocks inferred_unconfirmed facts from reaching L3.

Fix: add `source` param to memory_write (default "stated"); reject L3 writes with
source="inferred_unconfirmed"; add INFERRED VS STATED FACTS section to consciousness.txt.

Offline — real SQLite temp file; Supabase mocked.
"""
import sys
import os
import tempfile
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helper (mirrors test_t026_dedup_and_profile.py pattern) ──────────────────

def _make_memory():
    from tools.tools_memory import MemoryTools
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    supa = MagicMock()
    supa.table.return_value.insert.return_value.execute.return_value = MagicMock()
    supa.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "verify-ok"}
    ]
    import threading
    mt = MemoryTools.__new__(MemoryTools)
    mt.supabase = supa
    mt.sqlite_path = tmp.name
    mt._last_sync = None
    mt._sync_ttl_seconds = 300
    mt._sync_lock = threading.Lock()
    mt._supa_lock = threading.RLock()
    mt._init_sqlite()
    return mt, supa, tmp.name


# ── Test 1: source field must exist on memory_write ──────────────────────────

def test_memory_write_accepts_source_field():
    """memory_write must accept a `source` keyword argument without raising."""
    from tools.tools_memory import MemoryTools
    import inspect
    sig = inspect.signature(MemoryTools.memory_write)
    assert "source" in sig.parameters, (
        "MemoryTools.memory_write is missing the `source` parameter. "
        "Add: source: str = 'stated' to the signature."
    )


# ── Test 2: inferred_unconfirmed rejected for L3 ─────────────────────────────

def test_l3_write_rejects_inferred_unconfirmed():
    """memory_write(tier='l3', source='inferred_unconfirmed') must return success=False."""
    mt, supa, db = _make_memory()
    try:
        result = mt.memory_write(
            content="User has an F-1 visa",
            tier="l3",
            importance=7,
            category="permanent_profile",
            source="inferred_unconfirmed",
        )
        assert result.get("success") is False, (
            f"memory_write with source='inferred_unconfirmed' should be rejected "
            f"(success=False) but got: {result}"
        )
        assert "inferred" in result.get("error", "").lower(), (
            f"Rejection must include an 'error' key mentioning 'inferred', got: {result}"
        )
    finally:
        os.unlink(db)


# ── Test 3: stated facts still work ──────────────────────────────────────────

def test_l3_write_accepts_stated_source():
    """memory_write(tier='l3', source='stated') must succeed normally."""
    mt, supa, db = _make_memory()
    try:
        result = mt.memory_write(
            content="User studies computer science",
            tier="l3",
            importance=7,
            category="permanent_profile",
            source="stated",
        )
        assert result.get("success") is True, (
            f"Stated fact should succeed but got: {result}"
        )
    finally:
        os.unlink(db)


# ── Test 4: inferred_confirmed facts allowed ──────────────────────────────────

def test_l3_write_accepts_inferred_confirmed():
    """memory_write(tier='l3', source='inferred_confirmed') must succeed."""
    mt, supa, db = _make_memory()
    try:
        result = mt.memory_write(
            content="User has an F-1 visa",
            tier="l3",
            importance=7,
            category="permanent_profile",
            source="inferred_confirmed",
        )
        assert result.get("success") is True, (
            f"inferred_confirmed should succeed but got: {result}"
        )
    finally:
        os.unlink(db)


# ── Test 5: default source is 'stated' (no regression for existing callers) ──

def test_l3_write_default_source_is_stated():
    """Callers that omit source must behave as before (source defaults to 'stated')."""
    mt, supa, db = _make_memory()
    try:
        result = mt.memory_write(
            content="User prefers dark mode",
            tier="l3",
            importance=6,
            category="permanent_profile",
        )
        assert result.get("success") is True, (
            f"Default source should be 'stated' (no regression), got: {result}"
        )
    finally:
        os.unlink(db)


# ── Test 6: L2 writes not blocked by source ───────────────────────────────────

def test_l2_write_not_blocked_by_source():
    """source='inferred_unconfirmed' must only block L3. L2 is searchable archive."""
    mt, supa, db = _make_memory()
    try:
        result = mt.memory_write(
            content="Pi inferred user might have F-1 visa from context",
            tier="l2",
            importance=4,
            category="note",
            source="inferred_unconfirmed",
        )
        assert result.get("success") is True, (
            f"L2 inferred_unconfirmed write should NOT be blocked, got: {result}"
        )
    finally:
        os.unlink(db)


# ── Test 7: consciousness.txt has INFERRED VS STATED section ─────────────────

def test_consciousness_has_inferred_vs_stated_section():
    """consciousness.txt must contain the INFERRED VS STATED FACTS guardrail.

    consciousness.txt is private/gitignored (the identity "recipe") — skip on a
    public/CI checkout where only consciousness.default.txt is tracked.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "prompts", "consciousness.txt"
    )
    if not os.path.exists(path):
        pytest.skip("prompts/consciousness.txt is private/gitignored — not present in this checkout")
    content = open(path, encoding="utf-8").read()
    assert "INFERRED VS STATED" in content, (
        "prompts/consciousness.txt is missing the 'INFERRED VS STATED FACTS' section. "
        "Add the guardrail that teaches Pi not to persist unconfirmed inferences."
    )
    assert "inferred_unconfirmed" in content, (
        "The INFERRED VS STATED section must name the 'inferred_unconfirmed' source value "
        "so Pi knows to avoid it."
    )
