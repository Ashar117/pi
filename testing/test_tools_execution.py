"""Tests for tools/tools_execution.py — T-182: edit safety (read-before-write + diff)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.tools_execution import ExecutionTools


def _et(tmp_path):
    return ExecutionTools(project_root=str(tmp_path))


# ── read_file: records in ledger ──────────────────────────────────────────────

def test_read_file_records_mtime(tmp_path):
    et = _et(tmp_path)
    f = tmp_path / "hello.txt"
    f.write_text("original", encoding="utf-8")
    et.read_file(str(f))
    assert str(f) in et._read_ledger


def test_read_file_returns_content(tmp_path):
    et = _et(tmp_path)
    f = tmp_path / "hi.txt"
    f.write_text("hello world", encoding="utf-8")
    result = et.read_file(str(f))
    assert result["success"]
    assert "hello world" in result["content"]


# ── modify_file: read-before-write guard ──────────────────────────────────────

def test_modify_without_read_refused(tmp_path):
    et = _et(tmp_path)
    f = tmp_path / "target.py"
    f.write_text("x = 1\n", encoding="utf-8")
    result = et.modify_file(str(f), "x = 1", "x = 2")
    assert not result["success"]
    assert result.get("error") == "read_before_write"


def test_modify_after_read_succeeds(tmp_path):
    et = _et(tmp_path)
    f = tmp_path / "target.py"
    f.write_text("x = 1\n", encoding="utf-8")
    et.read_file(str(f))
    result = et.modify_file(str(f), "x = 1", "x = 2")
    assert result["success"]
    assert "x = 2" in f.read_text(encoding="utf-8")


def test_modify_stale_read_refused(tmp_path):
    """File modified externally between read and write -> stale_read error."""
    et = _et(tmp_path)
    f = tmp_path / "target.py"
    f.write_text("x = 1\n", encoding="utf-8")
    et.read_file(str(f))
    # Simulate external modification by backdating the ledger entry
    et._read_ledger[str(f)] -= 2.0
    result = et.modify_file(str(f), "x = 1", "x = 2")
    assert not result["success"]
    assert result.get("error") == "stale_read"


# ── modify_file: diff output ──────────────────────────────────────────────────

def test_modify_returns_diff(tmp_path):
    et = _et(tmp_path)
    f = tmp_path / "code.py"
    f.write_text("a = 1\nb = 2\n", encoding="utf-8")
    et.read_file(str(f))
    result = et.modify_file(str(f), "a = 1", "a = 99")
    assert result["success"]
    assert "diff" in result
    assert "+a = 99" in result["diff"]
    assert "-a = 1" in result["diff"]


def test_modify_returns_lines_changed(tmp_path):
    et = _et(tmp_path)
    f = tmp_path / "code.py"
    f.write_text("a = 1\nb = 2\n", encoding="utf-8")
    et.read_file(str(f))
    result = et.modify_file(str(f), "a = 1", "a = 99")
    assert result.get("lines_changed", 0) > 0


def test_modify_updates_ledger_after_write(tmp_path):
    """Ledger mtime is updated after successful write so next modify works."""
    et = _et(tmp_path)
    f = tmp_path / "code.py"
    f.write_text("a = 1\n", encoding="utf-8")
    et.read_file(str(f))
    r1 = et.modify_file(str(f), "a = 1", "a = 2")
    assert r1["success"]
    # Second modify (without re-reading) should succeed because ledger was updated
    r2 = et.modify_file(str(f), "a = 2", "a = 3")
    assert r2["success"]


# ── create_file: refuses to overwrite existing ────────────────────────────────

def test_create_file_refuses_overwrite(tmp_path):
    et = _et(tmp_path)
    f = tmp_path / "existing.txt"
    f.write_text("original", encoding="utf-8")
    result = et.create_file(str(f), "replacement")
    assert not result["success"]
    assert result.get("error") == "file_exists"
    assert f.read_text() == "original"  # unchanged


def test_create_file_new_path_succeeds(tmp_path):
    et = _et(tmp_path)
    f = tmp_path / "brand_new.txt"
    result = et.create_file(str(f), "hello")
    assert result["success"]
    assert f.read_text() == "hello"
