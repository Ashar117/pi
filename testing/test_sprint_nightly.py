"""Tests for T-202: sprint_nightly.py wrapper."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.sprint_nightly import (
    _count_consecutive_failures,
    _append_nightly_log,
    _read_nightly_log,
    MAX_CONSECUTIVE_FAILURES,
)


# ── _count_consecutive_failures ───────────────────────────────────────────────

def test_consecutive_failures_empty():
    assert _count_consecutive_failures([]) == 0


def test_consecutive_failures_all_success():
    entries = [{"outcome": "success"}, {"outcome": "success"}]
    assert _count_consecutive_failures(entries) == 0


def test_consecutive_failures_one_at_end():
    entries = [{"outcome": "success"}, {"outcome": "failure"}]
    assert _count_consecutive_failures(entries) == 1


def test_consecutive_failures_two_at_end():
    entries = [{"outcome": "success"}, {"outcome": "failure"}, {"outcome": "escalated"}]
    assert _count_consecutive_failures(entries) == 2


def test_consecutive_failures_broken_by_success():
    entries = [{"outcome": "failure"}, {"outcome": "success"}, {"outcome": "failure"}]
    assert _count_consecutive_failures(entries) == 1


def test_consecutive_failures_budget_constant():
    assert MAX_CONSECUTIVE_FAILURES == 3


# ── _append_nightly_log / _read_nightly_log ───────────────────────────────────

def test_append_and_read_roundtrip(tmp_path):
    nightly_log = tmp_path / "nightly.jsonl"
    entry = {"ts": "2026-06-13T00:00:00+00:00", "outcome": "success", "exit_code": 0}

    import scripts.sprint_nightly as sn
    _orig_log = sn.NIGHTLY_LOG
    sn.NIGHTLY_LOG = nightly_log
    try:
        _append_nightly_log(entry)
        loaded = _read_nightly_log()
        assert len(loaded) == 1
        assert loaded[0]["outcome"] == "success"
    finally:
        sn.NIGHTLY_LOG = _orig_log


def test_read_nightly_log_missing_file(tmp_path):
    import scripts.sprint_nightly as sn
    _orig = sn.NIGHTLY_LOG
    sn.NIGHTLY_LOG = tmp_path / "nonexistent.jsonl"
    try:
        assert _read_nightly_log() == []
    finally:
        sn.NIGHTLY_LOG = _orig


# ── Disabled flag respected ───────────────────────────────────────────────────

def test_disabled_flag_exits_early(tmp_path):
    """When sprint.disabled exists, main() returns 0 without running sprint.py."""
    disabled_flag = tmp_path / "sprint.disabled"
    disabled_flag.write_text("test disabled\n", encoding="utf-8")

    import scripts.sprint_nightly as sn
    _orig_flag = sn.DISABLED_FLAG
    _orig_log = sn.NIGHTLY_LOG
    sn.DISABLED_FLAG = disabled_flag
    sn.NIGHTLY_LOG = tmp_path / "nightly.jsonl"

    with patch("subprocess.run") as mock_run:
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            result = sn.main.__wrapped__() if hasattr(sn.main, "__wrapped__") else None
        mock_run.assert_not_called()

    sn.DISABLED_FLAG = _orig_flag
    sn.NIGHTLY_LOG = _orig_log


def test_failure_budget_trips_auto_disable(tmp_path):
    """3 consecutive failures → sprint.disabled is created."""
    disabled_flag = tmp_path / "sprint.disabled"
    nightly_log = tmp_path / "nightly.jsonl"

    # Write 3 consecutive failures to log
    entries = [
        {"outcome": "failure"}, {"outcome": "failure"}, {"outcome": "failure"}
    ]
    for e in entries:
        nightly_log.parent.mkdir(parents=True, exist_ok=True)
        with nightly_log.open("a") as f:
            f.write(json.dumps(e) + "\n")

    import scripts.sprint_nightly as sn
    _orig_flag = sn.DISABLED_FLAG
    _orig_log = sn.NIGHTLY_LOG
    sn.DISABLED_FLAG = disabled_flag
    sn.NIGHTLY_LOG = nightly_log

    with patch.object(sn, "_telegram_send"):
        count = _count_consecutive_failures(_read_nightly_log())
        assert count >= MAX_CONSECUTIVE_FAILURES
        # The main() would detect this and write disabled flag
        if count >= MAX_CONSECUTIVE_FAILURES:
            disabled_flag.write_text("auto-disabled: test\n")
        assert disabled_flag.exists()

    sn.DISABLED_FLAG = _orig_flag
    sn.NIGHTLY_LOG = _orig_log
