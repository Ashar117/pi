"""
testing/test_startup_banner.py — Phase A unit tests for the compact banner (T-041).
"""

import os
from unittest.mock import patch
from pathlib import Path

import pytest


class TestFormatBanner:
    def test_three_lines_no_reminders(self):
        from agent.startup_banner import format_banner
        out = format_banner(
            mode="normie",
            session_id="abc12345",
            tool_count=38,
            telegram_online=False,
            scheduler_running=True,
            turns_today=0,
            reminders=[],
        )
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) == 3
        assert "Pi v2" in lines[0]
        assert "normie" in lines[0]
        assert "abc12345" in lines[0]
        assert "38 tools" in lines[0]
        assert "Telegram offline" in lines[1]
        assert "Scheduler running" in lines[1]
        assert "0 turns today" in lines[1]
        assert "0 reminders due" in lines[2]

    def test_pluralisation_of_reminders(self):
        from agent.startup_banner import format_banner
        out_one = format_banner(
            mode="root", session_id="x", tool_count=1, telegram_online=False,
            scheduler_running=False, turns_today=0, reminders=["one"],
        )
        out_two = format_banner(
            mode="root", session_id="x", tool_count=1, telegram_online=False,
            scheduler_running=False, turns_today=0, reminders=["a", "b"],
        )
        assert "1 reminder due" in out_one
        assert "2 reminders due" in out_two

    def test_appends_reminder_block(self):
        from agent.startup_banner import format_banner
        out = format_banner(
            mode="root", session_id="x", tool_count=1, telegram_online=True,
            scheduler_running=True, turns_today=5,
            reminders=["[REMINDER due 2026-05-05] pay rent"],
        )
        assert "REMINDERS DUE TODAY" in out
        assert "pay rent" in out

    def test_telegram_online_label(self):
        from agent.startup_banner import format_banner
        out = format_banner(
            mode="root", session_id="x", tool_count=1, telegram_online=True,
            scheduler_running=True, turns_today=5, reminders=[],
        )
        assert "Telegram online" in out


class TestVerifyStatusRead:
    def test_pass_detected(self, tmp_path):
        status = tmp_path / "STATUS.md"
        status.write_text("# title\n\n**Last run:** now\n**Overall:** PASS\n", encoding="utf-8")
        from agent import startup_banner
        with patch.object(startup_banner, "_STATUS_PATH", status):
            assert startup_banner._read_verify_status() == "PASS"

    def test_fail_detected(self, tmp_path):
        status = tmp_path / "STATUS.md"
        status.write_text("**Overall:** FAIL\n", encoding="utf-8")
        from agent import startup_banner
        with patch.object(startup_banner, "_STATUS_PATH", status):
            assert startup_banner._read_verify_status() == "FAIL"

    def test_missing_status_returns_unknown(self, tmp_path):
        from agent import startup_banner
        with patch.object(startup_banner, "_STATUS_PATH", tmp_path / "no.md"):
            assert startup_banner._read_verify_status() == "unknown"


class TestOpenTicketCount:
    def test_count_zero_when_dir_missing(self, tmp_path):
        from agent import startup_banner
        with patch.object(startup_banner, "_OPEN_TICKETS", tmp_path / "nope"):
            assert startup_banner._count_open_tickets() == 0

    def test_count_json_files(self, tmp_path):
        d = tmp_path / "open"
        d.mkdir()
        (d / "T-001.json").write_text("{}")
        (d / "T-002.json").write_text("{}")
        (d / "README.md").write_text("not a ticket")
        from agent import startup_banner
        with patch.object(startup_banner, "_OPEN_TICKETS", d):
            assert startup_banner._count_open_tickets() == 2
