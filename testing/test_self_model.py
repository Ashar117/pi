"""Tests for T-193: identity-from-memory self-model distillation (scripts/retro.py)."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.retro import build_self_model, write_self_model_to_l3


# ── build_self_model ─────────────────────────────────────────────────────────

def test_build_self_model_returns_string():
    result = build_self_model()
    assert isinstance(result, str)
    assert len(result) > 0


def test_build_self_model_respects_line_cap(tmp_path):
    """Output must not exceed max_lines."""
    sol_path = tmp_path / "SOLUTIONS.jsonl"
    for i in range(50):
        sol_path.open("a").write(json.dumps({
            "id": f"S-{i}", "title": "some fix", "summary": "write read divergence fix",
        }) + "\n")
    result = build_self_model(solutions_path=sol_path, max_lines=10)
    assert len(result.splitlines()) <= 10


def test_build_self_model_counts_write_read_divergence(tmp_path):
    sol_path = tmp_path / "SOLUTIONS.jsonl"
    for _ in range(3):
        sol_path.open("a").write(json.dumps({
            "id": "S-1", "title": "x", "summary": "write read path divergence bug fixed",
        }) + "\n")
    result = build_self_model(solutions_path=sol_path)
    assert "write read divergence" in result


def test_build_self_model_empty_solutions(tmp_path):
    sol_path = tmp_path / "SOLUTIONS.jsonl"
    sol_path.write_text("", encoding="utf-8")
    result = build_self_model(solutions_path=sol_path)
    assert "No solutions recorded" in result


def test_build_self_model_missing_solutions(tmp_path):
    sol_path = tmp_path / "DOES_NOT_EXIST.jsonl"
    result = build_self_model(solutions_path=sol_path)
    assert "No solutions recorded" in result


def test_build_self_model_includes_solution_count(tmp_path):
    sol_path = tmp_path / "SOLUTIONS.jsonl"
    for i in range(7):
        sol_path.open("a").write(json.dumps({"id": f"S-{i}", "title": "fix"}) + "\n")
    result = build_self_model(solutions_path=sol_path)
    assert "7" in result  # total solution count


def test_build_self_model_includes_standing_commitments():
    # solutions/SOLUTIONS.jsonl is local-only/untracked (it accumulated personal
    # facts as example content). With no solutions file, build_self_model() short-
    # circuits to "(No solutions recorded yet.)" and the standing-commitments block
    # isn't emitted — skip on a fresh public checkout where the file is absent.
    sol = Path(__file__).resolve().parent.parent / "solutions" / "SOLUTIONS.jsonl"
    if not sol.exists():
        pytest.skip("solutions/SOLUTIONS.jsonl is local-only/untracked — not present in this checkout")
    result = build_self_model()
    assert "verify.py" in result
    assert "mime" in result.lower() or "tool" in result.lower()


# ── write_self_model_to_l3 ────────────────────────────────────────────────────

def test_write_self_model_no_memory_tools():
    result = write_self_model_to_l3("some model text", memory_tools=None)
    assert not result["success"]
    assert "error" in result


def test_write_self_model_calls_memory_write():
    mock_mt = MagicMock()
    mock_mt.memory_read.return_value = []
    mock_mt.memory_write.return_value = {"success": True, "tier": "l3"}

    result = write_self_model_to_l3("## Pi Self-Model\n...", memory_tools=mock_mt)

    mock_mt.memory_write.assert_called_once()
    call_kwargs = mock_mt.memory_write.call_args
    assert call_kwargs.kwargs.get("tier") == "l3"
    assert call_kwargs.kwargs.get("category") == "self_model"
    assert call_kwargs.kwargs.get("importance", 0) >= 8


def test_write_self_model_deletes_existing_before_write():
    mock_mt = MagicMock()
    mock_mt.memory_read.return_value = [
        {"id": "old-id-123", "category": "self_model", "content": "old model"}
    ]
    mock_mt.memory_write.return_value = {"success": True}

    write_self_model_to_l3("## New Model", memory_tools=mock_mt)

    mock_mt.memory_delete.assert_called_once_with("old-id-123")
