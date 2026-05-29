"""testing/test_silent_failure_watcher.py — T-113: silent failure watcher tests."""
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_db(tmp_path: Path, rows: list) -> Path:
    """Create a silent_failures.db with given rows: [(category, exc_type), ...]."""
    db = tmp_path / "silent_failures.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE silent_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,
            exception_type TEXT,
            redacted_message TEXT,
            context_json TEXT
        )
    """)
    recent_ts = datetime.now(timezone.utc).isoformat()
    for cat, exc in rows:
        conn.execute(
            "INSERT INTO silent_failures(timestamp, category, exception_type) VALUES(?,?,?)",
            (recent_ts, cat, exc),
        )
    conn.commit()
    conn.close()
    return db


# ── Threshold checks ──────────────────────────────────────────────────────────

def test_pass_when_under_thresholds(tmp_path):
    from scripts.passive.silent_failure_watcher import check_silent_failures
    from scripts.passive.common import Status

    rows = [("tools.invalid_input", "ValueError")] * 10 + \
           [("memory.l3_invalidate", "Exception")] * 5 + \
           [("telegram.voice", "IOError")] * 3
    db = _make_db(tmp_path, rows)

    status, lines = check_silent_failures(db, warn_per_cat=50, fail_total=500)
    assert status == Status.PASS


def test_warn_when_category_exceeds(tmp_path):
    from scripts.passive.silent_failure_watcher import check_silent_failures
    from scripts.passive.common import Status

    rows = [("tools.invalid_input", "ValueError")] * 100
    db = _make_db(tmp_path, rows)

    status, lines = check_silent_failures(db, warn_per_cat=50, fail_total=500)
    assert status == Status.WARN
    assert any("tools.invalid_input" in line for line in lines)


def test_fail_when_total_exceeds(tmp_path):
    from scripts.passive.silent_failure_watcher import check_silent_failures
    from scripts.passive.common import Status

    rows = [("cat_a", "E")] * 300 + [("cat_b", "E")] * 300
    db = _make_db(tmp_path, rows)

    status, lines = check_silent_failures(db, warn_per_cat=50, fail_total=500)
    assert status == Status.FAIL


def test_thresholds_env_override(tmp_path):
    from scripts.passive.silent_failure_watcher import check_silent_failures
    from scripts.passive.common import Status

    rows = [("tools.invalid_input", "ValueError")] * 10
    db = _make_db(tmp_path, rows)

    # Override warn threshold to 5 → 10 events should WARN
    status, lines = check_silent_failures(db, warn_per_cat=5, fail_total=500)
    assert status == Status.WARN


# ── Report file written ───────────────────────────────────────────────────────

def test_report_file_written(tmp_path):
    from scripts.passive.silent_failure_watcher import run_check, REPORT_FILE

    db = _make_db(tmp_path, [("test.cat", "ValueError")] * 3)
    reports = tmp_path / "reports"
    reports.mkdir()

    written = {}

    def fake_write(filename, content, status):
        written["content"] = content
        written["status"] = status
        return reports / filename

    with patch("scripts.passive.silent_failure_watcher._DEFAULT_ROOT", tmp_path), \
         patch("scripts.passive.silent_failure_watcher.write_report", fake_write):
        status = run_check(root=tmp_path, reports=reports)

    assert written, "write_report should have been called"
    assert "Silent Failure" in written["content"] or "silent_failure" in written["content"].lower()


# ── No DB — PASS (not started yet) ───────────────────────────────────────────

def test_no_db_returns_pass(tmp_path):
    from scripts.passive.silent_failure_watcher import check_silent_failures
    from scripts.passive.common import Status

    missing = tmp_path / "does_not_exist.db"
    status, lines = check_silent_failures(missing, warn_per_cat=50, fail_total=500)
    assert status == Status.PASS


# ── Digest includes skill 14 ──────────────────────────────────────────────────

def test_daily_digest_includes_silent_failure_skill():
    from scripts.passive.passive_daily_digest import SKILL_MODULES
    names = [m[0] for m in SKILL_MODULES]
    assert "silent_failure_watcher" in names
    labels = [m[1] for m in SKILL_MODULES]
    assert any("Silent Failure" in lbl for lbl in labels)
