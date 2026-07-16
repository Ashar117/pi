"""Tests for tools/tools_project.py — run_verify and run_tests (T-181)."""
import os
import sys
import json
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.tools_project import ProjectTools


def _pt():
    return ProjectTools()


# ── run_verify: parse PASS correctly ─────────────────────────────────────────

def test_run_verify_pass_result(tmp_path):
    """Mock subprocess returning a PASS stdout; verify overall=PASS."""
    fake_stdout = (
        "[verify] Syntax check...\n"
        "  10 ok, 0 failed\n"
        "[verify] Coherence gate (keystone, T-152)...\n"
        "  gate: 3 run, 0 failed\n"
        "[verify] Running non-costly tests...\n"
        "  20 run, 0 skipped, 0 failed\n"
        "[verify] PASS\n"
    )
    mock_result = MagicMock()
    mock_result.stdout = fake_stdout
    mock_result.returncode = 0

    with patch("tools.tools_project.subprocess.run", return_value=mock_result):
        result = _pt().run_verify()

    assert result["overall"] == "PASS"
    assert result["exit_code"] == 0
    assert result["syntax_failed"] == []
    assert result["gate_failures"] == []
    assert result["test_failures"] == []


def test_run_verify_fail_result(tmp_path):
    """Mock subprocess returning a FAIL stdout; verify overall=FAIL."""
    fake_stdout = (
        "[verify] Syntax check...\n"
        "  SYNTAX FAIL testing/broken.py:5: invalid syntax\n"
        "[verify] FAIL\n"
    )
    mock_result = MagicMock()
    mock_result.stdout = fake_stdout
    mock_result.returncode = 1

    with patch("tools.tools_project.subprocess.run", return_value=mock_result):
        result = _pt().run_verify()

    assert result["overall"] == "FAIL"
    assert len(result["syntax_failed"]) == 1
    assert "broken.py" in result["syntax_failed"][0]


def test_run_verify_timeout_returns_structured_error():
    import subprocess
    with patch("tools.tools_project.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="verify.py", timeout=600)):
        result = _pt().run_verify()
    assert result["overall"] == "TIMEOUT"
    assert "error" in result


def test_run_verify_lock_prevents_concurrent_run(tmp_path, monkeypatch):
    """If a lock file already exists, run_verify returns BUSY immediately."""
    lock = ProjectTools._VERIFY_LOCK
    monkeypatch.setattr(ProjectTools, "_VERIFY_LOCK", tmp_path / "verify.lock")
    (tmp_path / "verify.lock").write_text("12345")

    result = _pt().run_verify()
    assert result["overall"] == "BUSY"
    assert "lock" in result["error"].lower()


def test_run_verify_lock_cleaned_on_success(tmp_path, monkeypatch):
    """Lock file is removed after a successful subprocess call."""
    fake_lock = tmp_path / "verify.lock"
    monkeypatch.setattr(ProjectTools, "_VERIFY_LOCK", fake_lock)
    assert not fake_lock.exists()

    mock_result = MagicMock()
    mock_result.stdout = "[verify] PASS\n"
    mock_result.returncode = 0

    with patch("tools.tools_project.subprocess.run", return_value=mock_result):
        _pt().run_verify()

    assert not fake_lock.exists()


# ── run_tests ─────────────────────────────────────────────────────────────────

def test_run_tests_pass_parsed(tmp_path):
    """Mock pytest returning a 5-passed summary."""
    fake_stdout = ".....\n5 passed in 0.14s\n"
    mock_result = MagicMock()
    mock_result.stdout = fake_stdout
    mock_result.returncode = 0

    with patch("tools.tools_project.subprocess.run", return_value=mock_result):
        with patch("pathlib.Path.exists", return_value=True):
            result = _pt().run_tests("test_memory.py")

    assert result["passed"] == 5
    assert result["failed"] == 0
    assert result["exit_code"] == 0


def test_run_tests_fail_parsed(tmp_path):
    """Mock pytest returning 1 failed."""
    fake_stdout = "F\n1 failed in 0.05s\n"
    mock_result = MagicMock()
    mock_result.stdout = fake_stdout
    mock_result.returncode = 1

    with patch("tools.tools_project.subprocess.run", return_value=mock_result):
        with patch("pathlib.Path.exists", return_value=True):
            result = _pt().run_tests("test_bad.py")

    assert result["failed"] == 1
    assert result["exit_code"] == 1


def test_run_tests_missing_file_returns_error():
    result = _pt().run_tests("test_nonexistent_xyzzy.py")
    assert result["exit_code"] == -1
    assert "error" in result


def test_run_tests_timeout_returns_error():
    import subprocess
    with patch("tools.tools_project.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=120)):
        with patch("pathlib.Path.exists", return_value=True):
            result = _pt().run_tests("test_memory.py")
    assert result["exit_code"] == -1
    assert "error" in result


# ── ToolSpec registration check ───────────────────────────────────────────────

def test_run_verify_toolspec_registered():
    from tools.tools_project import TOOLS
    names = [t.name for t in TOOLS]
    assert "run_verify" in names


def test_run_tests_toolspec_registered():
    from tools.tools_project import TOOLS
    names = [t.name for t in TOOLS]
    assert "run_tests" in names
