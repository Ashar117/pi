"""testing/test_startup_continuation.py — T-132: session continuation banner line.

Tests _format_continuation_line() in isolation by patching sqlite3.connect
and the closed-tickets glob. No real DB or agent needed.
"""
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.startup_banner import _format_continuation_line, format_banner


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_db(row=None):
    """Return a context-manager mock for sqlite3.connect that yields one row."""
    con = MagicMock()
    con.execute.return_value.fetchone.return_value = row
    con.__enter__ = MagicMock(return_value=con)
    con.__exit__ = MagicMock(return_value=False)
    return con


# ── _format_continuation_line ─────────────────────────────────────────────────

def test_continuation_line_renders_when_data_present(tmp_path):
    """Full line: session date + summary + last ticket."""
    row = ("We discussed UX upgrades and decided on plan-mode first.", "2026-05-24T14:32:00+00:00")
    db_mock = _mock_db(row)

    fake_ticket = tmp_path / "T-099-something.json"
    fake_ticket.write_text("{}")

    with patch("agent.startup_banner._PUBLIC_DB", tmp_path / "pi.db"), \
         patch("agent.startup_banner._CLOSED_TICKETS", tmp_path), \
         patch("agent.startup_banner.sqlite3.connect", return_value=db_mock), \
         patch.object(Path, "exists", return_value=True):
        line = _format_continuation_line("root")

    assert "2026-05-24 14:32" in line
    assert "UX upgrades" in line
    assert "T-099" in line


def test_empty_repo_no_continuation_line(tmp_path):
    """No session_history row AND no closed tickets → empty string."""
    db_mock = _mock_db(None)

    with patch("agent.startup_banner._PUBLIC_DB", tmp_path / "pi.db"), \
         patch("agent.startup_banner._CLOSED_TICKETS", tmp_path), \
         patch("agent.startup_banner.sqlite3.connect", return_value=db_mock), \
         patch.object(Path, "exists", return_value=True):
        # tmp_path has no *.json files and no DB row
        line = _format_continuation_line("root")

    assert line == ""


def test_text_truncated_at_80_chars(tmp_path):
    """Summary text longer than 80 chars is truncated with ellipsis."""
    long_text = "x" * 200
    row = (long_text, "2026-05-24T10:00:00+00:00")
    db_mock = _mock_db(row)

    fake_ticket = tmp_path / "T-010-x.json"
    fake_ticket.write_text("{}")

    with patch("agent.startup_banner._PUBLIC_DB", tmp_path / "pi.db"), \
         patch("agent.startup_banner._CLOSED_TICKETS", tmp_path), \
         patch("agent.startup_banner.sqlite3.connect", return_value=db_mock), \
         patch.object(Path, "exists", return_value=True):
        line = _format_continuation_line("root")

    # The quoted snippet must end with ...
    assert "..." in line
    # Extract the quoted portion and verify it's capped
    m = re.search(r'"([^"]+)"', line)
    assert m is not None
    assert len(m.group(1)) <= 80


def test_db_error_returns_empty_no_crash(tmp_path):
    """SQLite failure + no closed tickets → empty string, no exception propagated."""
    empty_tickets = tmp_path / "empty_closed"
    empty_tickets.mkdir()

    with patch("agent.startup_banner._PUBLIC_DB", tmp_path / "pi.db"), \
         patch("agent.startup_banner._CLOSED_TICKETS", empty_tickets), \
         patch.object(Path, "exists", return_value=True), \
         patch("agent.startup_banner.sqlite3.connect", side_effect=Exception("db locked")):
        line = _format_continuation_line("root")

    assert line == ""


def test_continuation_absent_when_db_missing(tmp_path):
    """If db file doesn't exist, return '' without even trying to connect."""
    called = []
    with patch("agent.startup_banner._PUBLIC_DB", tmp_path / "nonexistent.db"), \
         patch("agent.startup_banner.sqlite3.connect", side_effect=lambda *a, **k: called.append(a)):
        line = _format_continuation_line("root")

    assert line == ""
    assert called == [], "sqlite3.connect should not be called when db missing"


# ── format_banner integration ─────────────────────────────────────────────────

def test_format_banner_includes_continuation_when_present(tmp_path):
    """Full format_banner output includes the continuation line."""
    row = ("Worked on T-132 implementation.", "2026-05-27T12:00:00+00:00")
    db_mock = _mock_db(row)

    fake_ticket = tmp_path / "T-132-x.json"
    fake_ticket.write_text("{}")

    with patch("agent.startup_banner._PUBLIC_DB", tmp_path / "pi.db"), \
         patch("agent.startup_banner._CLOSED_TICKETS", tmp_path), \
         patch("agent.startup_banner.sqlite3.connect", return_value=db_mock), \
         patch.object(Path, "exists", return_value=True), \
         patch("agent.startup_banner._read_verify_status", return_value="PASS"), \
         patch("agent.startup_banner._count_open_tickets", return_value=2):
        banner = format_banner(
            mode="root",
            session_id="abc123",
            tool_count=64,
            telegram_online=False,
            scheduler_running=False,
            turns_today=5,
        )

    assert "T-132" in banner
    assert "2026-05-27 12:00" in banner
