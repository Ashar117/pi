"""
testing/test_memory_pipeline.py — Unit tests for memory/pipeline.py.

All tests are offline (no Supabase, no Groq API).  External calls are replaced
by simple stubs defined inline.

Run:  python -m pytest testing/test_memory_pipeline.py -v
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from memory.pipeline import distill_session, _format_conversation, _extract_facts


# ── Fixtures / stubs ──────────────────────────────────────────────────────────

def _make_rows(pairs):
    """Build minimal raw_wiki row dicts from [(role, content, turn, seq)] tuples."""
    rows = []
    for role, content, turn, seq in pairs:
        rows.append({
            "id": f"row-{turn}-{seq}",
            "role": role,
            "content": content,
            "metadata": {"turn": turn, "seq": seq},
            "timestamp": f"2026-05-03T0{turn}:00:00Z",
        })
    return rows


def _stub_groq(facts_json):
    """Return a Groq client stub whose completions.create returns facts_json."""
    choice = MagicMock()
    choice.message.content = json.dumps(facts_json)
    resp = MagicMock()
    resp.choices = [choice]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


def _stub_memory(rows):
    """Return a MemoryTools stub that serves ``rows`` from get_l1_thread."""
    m = MagicMock()
    m.get_l1_thread.return_value = rows
    m.memory_write.return_value = {"success": True}
    return m


# ── _format_conversation ──────────────────────────────────────────────────────

def test_format_skips_tool_rows():
    rows = _make_rows([
        ("user", "hello", 1, 0),
        ("tool", "[search] ...", 1, 1),
        ("assistant", "hi there", 1, 2),
    ])
    result = _format_conversation(rows)
    assert "User: hello" in result
    assert "Pi: hi there" in result
    assert "tool" not in result.lower() or "[search]" not in result


def test_format_ordering_by_turn_seq():
    # Rows deliberately out of insertion order
    rows = _make_rows([
        ("assistant", "second", 1, 2),
        ("user", "first", 1, 0),
    ])
    # _format_conversation does NOT sort — caller (get_l1_thread) sorts.
    # Here we verify the format itself is correct.
    result = _format_conversation(rows)
    assert "Pi: second" in result
    assert "User: first" in result


def test_format_empty_rows():
    assert _format_conversation([]) == ""


# ── _extract_facts ────────────────────────────────────────────────────────────

def test_extract_facts_happy_path():
    facts = [{"fact": "User likes Python", "category": "permanent_profile", "importance": 7}]
    groq = _stub_groq(facts)
    result = _extract_facts("User: I like Python\nPi: Great!", groq, "llama-3.3-70b-versatile")
    assert result == facts


def test_extract_facts_strips_markdown_fences():
    facts = [{"fact": "Test fact", "category": "note", "importance": 5}]
    choice = MagicMock()
    choice.message.content = "```json\n" + json.dumps(facts) + "\n```"
    resp = MagicMock()
    resp.choices = [choice]
    groq = MagicMock()
    groq.chat.completions.create.return_value = resp
    result = _extract_facts("conversation", groq, "model")
    assert result == facts


def test_extract_facts_returns_empty_on_invalid_json():
    choice = MagicMock()
    choice.message.content = "not json at all"
    resp = MagicMock()
    resp.choices = [choice]
    groq = MagicMock()
    groq.chat.completions.create.return_value = resp
    result = _extract_facts("conversation", groq, "model")
    assert result == []


def test_extract_facts_returns_empty_on_groq_error():
    groq = MagicMock()
    groq.chat.completions.create.side_effect = RuntimeError("API down")
    result = _extract_facts("conversation", groq, "model")
    assert result == []


# ── distill_session ───────────────────────────────────────────────────────────

def test_distill_session_no_l1_rows():
    memory = _stub_memory([])
    groq = MagicMock()
    result = distill_session(
        thread_id="fake-uuid",
        session_id="aabbccdd",
        memory_tools=memory,
        groq_client=groq,
    )
    assert result["distilled"] == 0
    memory.memory_write.assert_not_called()
    groq.chat.completions.create.assert_not_called()


def test_distill_session_writes_facts_to_l2():
    rows = _make_rows([
        ("user", "I love dark mode", 1, 0),
        ("assistant", "Noted!", 1, 2),
    ])
    facts = [
        {"fact": "User loves dark mode", "category": "permanent_profile", "importance": 6},
        {"fact": "Minor detail", "category": "note", "importance": 2},  # below threshold
    ]
    memory = _stub_memory(rows)
    groq = _stub_groq(facts)

    result = distill_session(
        thread_id="test-thread",
        session_id="aabbccdd",
        memory_tools=memory,
        groq_client=groq,
    )

    # Only importance >= 4 should be written
    assert result["distilled"] == 1
    assert result["skipped"] == 1
    memory.memory_write.assert_called_once()
    call_kwargs = memory.memory_write.call_args
    assert call_kwargs.kwargs["tier"] == "l2"
    assert "dark mode" in call_kwargs.kwargs["content"]


def test_distill_session_dry_run_skips_writes():
    rows = _make_rows([
        ("user", "I prefer tabs", 1, 0),
        ("assistant", "Got it", 1, 1),
    ])
    facts = [{"fact": "User prefers tabs", "category": "note", "importance": 5}]
    memory = _stub_memory(rows)
    groq = _stub_groq(facts)

    result = distill_session(
        thread_id="test-thread",
        session_id="aabbccdd",
        memory_tools=memory,
        groq_client=groq,
        dry_run=True,
    )

    assert result["distilled"] == 1
    memory.memory_write.assert_not_called()


def test_distill_session_groq_failure_returns_zero():
    rows = _make_rows([("user", "hello", 1, 0), ("assistant", "hi", 1, 1)])
    memory = _stub_memory(rows)
    groq = MagicMock()
    groq.chat.completions.create.side_effect = RuntimeError("quota exceeded")

    result = distill_session(
        thread_id="test-thread",
        session_id="aabbccdd",
        memory_tools=memory,
        groq_client=groq,
    )
    assert result["distilled"] == 0
    memory.memory_write.assert_not_called()
