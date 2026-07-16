"""Tests for T-204: scripts/consolidate.py nightly memory consolidation."""
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.consolidate import (
    _agent_was_recently_active,
    _acquire_lock,
    _release_lock,
    run_consolidation,
)
import scripts.consolidate as cs


# ── Activity guard ────────────────────────────────────────────────────────────

def test_no_turns_log_not_active(tmp_path):
    orig = cs.TURNS_LOG
    cs.TURNS_LOG = tmp_path / "nonexistent.jsonl"
    try:
        assert _agent_was_recently_active() is False
    finally:
        cs.TURNS_LOG = orig


def test_old_turns_log_not_active(tmp_path):
    turns = tmp_path / "turns.jsonl"
    turns.write_text("{}\n")
    # Backdate mtime by 20 minutes
    old_time = time.time() - 1200
    os.utime(turns, (old_time, old_time))

    orig = cs.TURNS_LOG
    cs.TURNS_LOG = turns
    try:
        assert _agent_was_recently_active() is False
    finally:
        cs.TURNS_LOG = orig


def test_recent_turns_log_is_active(tmp_path):
    turns = tmp_path / "turns.jsonl"
    turns.write_text("{}\n")
    # mtime is "just now" (default on create)

    orig = cs.TURNS_LOG
    cs.TURNS_LOG = turns
    try:
        assert _agent_was_recently_active() is True
    finally:
        cs.TURNS_LOG = orig


# ── Lock guard ────────────────────────────────────────────────────────────────

def test_acquire_lock_creates_file(tmp_path):
    lock = tmp_path / "consolidate.lock"
    orig = cs.LOCK_FILE
    cs.LOCK_FILE = lock
    try:
        result = _acquire_lock()
        assert result is True
        assert lock.exists()
    finally:
        cs.LOCK_FILE = orig
        lock.unlink(missing_ok=True)


def test_acquire_lock_blocked_by_existing(tmp_path):
    lock = tmp_path / "consolidate.lock"
    lock.write_text("12345")
    orig = cs.LOCK_FILE
    cs.LOCK_FILE = lock
    try:
        result = _acquire_lock()
        assert result is False
    finally:
        cs.LOCK_FILE = orig


def test_release_lock_removes_file(tmp_path):
    lock = tmp_path / "consolidate.lock"
    lock.write_text("pid")
    orig = cs.LOCK_FILE
    cs.LOCK_FILE = lock
    try:
        _release_lock()
        assert not lock.exists()
    finally:
        cs.LOCK_FILE = orig


# ── run_consolidation ─────────────────────────────────────────────────────────

def test_run_consolidation_returns_summary():
    """run_consolidation returns a dict with the expected keys."""
    with patch("scripts.consolidate._step_caretaker_lite", return_value={"step": "caretaker_lite", "ok": True, "stats": {}}), \
         patch("scripts.consolidate._step_retention", return_value={"step": "retention", "ok": True, "stats": {}}), \
         patch("scripts.consolidate._step_pattern_detection", return_value={"step": "pattern_detection", "ok": True, "stats": {}}):
        summary = run_consolidation(dry_run=True, memory_tools=None)

    assert "ts" in summary
    assert summary["dry_run"] is True
    assert summary["steps_ok"] == 3
    assert summary["steps_error"] == 0
    assert len(summary["steps"]) == 3


def test_run_consolidation_counts_errors():
    """Steps that raise errors are counted."""
    with patch("scripts.consolidate._step_caretaker_lite", return_value={"step": "caretaker_lite", "ok": False, "error": "boom", "stats": {}}), \
         patch("scripts.consolidate._step_retention", return_value={"step": "retention", "ok": True, "stats": {}}), \
         patch("scripts.consolidate._step_pattern_detection", return_value={"step": "pattern_detection", "ok": True, "stats": {}}):
        summary = run_consolidation(dry_run=True, memory_tools=None)

    assert summary["steps_error"] == 1
    assert summary["steps_ok"] == 2


def test_idempotent_double_run(tmp_path):
    """Running twice should produce the same shape (second run is a no-op at the step level)."""
    with patch("scripts.consolidate._step_caretaker_lite", return_value={"step": "c", "ok": True, "stats": {}}), \
         patch("scripts.consolidate._step_retention", return_value={"step": "r", "ok": True, "stats": {}}), \
         patch("scripts.consolidate._step_pattern_detection", return_value={"step": "p", "ok": True, "stats": {}}):
        r1 = run_consolidation(dry_run=True)
        r2 = run_consolidation(dry_run=True)

    assert r1["steps_ok"] == r2["steps_ok"]
    assert r1["steps_error"] == r2["steps_error"]


def test_log_run_appends(tmp_path):
    log = tmp_path / "consolidation.jsonl"
    orig = cs.CONSOLIDATION_LOG
    cs.CONSOLIDATION_LOG = log
    try:
        from scripts.consolidate import _log_run
        _log_run({"ts": "2026-06-13", "steps_ok": 3})
        _log_run({"ts": "2026-06-14", "steps_ok": 3})
        lines = [l for l in log.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
    finally:
        cs.CONSOLIDATION_LOG = orig
