"""
testing/test_plan_sprint_and_retro.py — Phase E unit tests (T-044 + T-045).

All offline. Telegram + Anthropic stubbed. tmp_path used for all writes.
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import plan_sprint, retro  # noqa: E402


# ── plan_sprint ──────────────────────────────────────────────────────────────

class TestPlanSprintHelpers:
    def test_iso_week_label(self):
        d = date(2026, 5, 5)  # a Tuesday
        label = plan_sprint.iso_week_label(d)
        assert label.startswith("2026-W")

    def test_week_range_is_monday_to_sunday(self):
        d = date(2026, 5, 7)  # a Thursday
        mon, sun = plan_sprint.week_range(d)
        assert mon.weekday() == 0
        assert sun.weekday() == 6
        assert (sun - mon).days == 6


class TestRenderSection3:
    def test_renders_with_tickets(self):
        out = plan_sprint.render_section_3(
            goal="ship the autonomy loop",
            tickets=[
                {"id": "T-100", "title": "Build x", "severity": "P1"},
                {"id": "T-101", "title": "Fix y", "severity": "P3"},
            ],
            start=date(2026, 5, 4),
            end=date(2026, 5, 10),
        )
        assert "ship the autonomy loop" in out
        assert "T-100" in out
        assert "T-101" in out
        assert "P1" in out
        assert "Week of:" in out

    def test_renders_with_no_tickets(self):
        out = plan_sprint.render_section_3(
            goal="x", tickets=[],
            start=date(2026, 5, 4), end=date(2026, 5, 10),
        )
        assert "no tickets selected" in out


class TestReplaceSection3:
    def test_replaces_only_section_3(self):
        original = (
            "## §1 something\n\nbla\n\n---\n\n"
            "## §3 NOW — this week's sprint\n\nold body\n\n---\n\n"
            "## §4 State (auto-generated)\n\n<!-- BEGIN AUTO §4 -->\n"
            "...\n<!-- END AUTO §4 -->\n"
        )
        out = plan_sprint.replace_section_3(original, "NEW BODY")
        assert "old body" not in out
        assert "NEW BODY" in out
        assert "## §1 something" in out
        assert "## §4 State" in out


class TestVaultSnapshot:
    def test_writes_snapshot(self, tmp_path):
        with patch.object(plan_sprint, "SPRINT_DIR", tmp_path / "sprints"):
            out = plan_sprint.write_vault_snapshot(
                goal="test goal",
                tickets=[{"id": "T-1", "title": "x", "severity": "P2", "component": "a/b"}],
                start=date(2026, 5, 4), end=date(2026, 5, 10),
            )
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert "test goal" in text
        assert "T-1" in text


class TestLoadOpenTickets:
    def test_filters_escalated(self, tmp_path):
        d = tmp_path / "open"
        d.mkdir()
        (d / "T-A.json").write_text(json.dumps({"id": "T-A", "severity": "P3", "status": "escalated"}))
        (d / "T-B.json").write_text(json.dumps({"id": "T-B", "severity": "P2"}))
        with patch.object(plan_sprint, "TICKETS_OPEN", d):
            tix = plan_sprint.load_open_tickets()
        assert len(tix) == 1
        assert tix[0]["id"] == "T-B"

    def test_sorts_by_severity(self, tmp_path):
        d = tmp_path / "open"
        d.mkdir()
        (d / "T-LOW.json").write_text(json.dumps({"id": "T-LOW", "severity": "P3"}))
        (d / "T-CRIT.json").write_text(json.dumps({"id": "T-CRIT", "severity": "P0"}))
        (d / "T-MID.json").write_text(json.dumps({"id": "T-MID", "severity": "P2"}))
        with patch.object(plan_sprint, "TICKETS_OPEN", d):
            tix = plan_sprint.load_open_tickets()
        assert [t["id"] for t in tix] == ["T-CRIT", "T-MID", "T-LOW"]


# ── retro ────────────────────────────────────────────────────────────────────

class TestParseWeek:
    def test_parses_label(self):
        mon, sun, label = retro.parse_week("2026-W18")
        assert mon.weekday() == 0
        assert label == "2026-W18"

    def test_default_is_current_week(self):
        mon, sun, label = retro.parse_week(None)
        today = date.today()
        assert mon <= today <= sun

    def test_bad_label_raises(self):
        with pytest.raises(ValueError):
            retro.parse_week("not-a-week")


class TestRangeCollectors:
    def test_closed_tickets_in_range(self, tmp_path):
        d = tmp_path / "closed"
        d.mkdir()
        (d / "T-IN.json").write_text(json.dumps({
            "id": "T-IN", "closed": "2026-05-06T12:00:00+00:00",
        }))
        (d / "T-OUT.json").write_text(json.dumps({
            "id": "T-OUT", "closed": "2025-01-01T00:00:00+00:00",
        }))
        with patch.object(retro, "TICKETS_CLOSED", d):
            r = retro.closed_tickets_in_range(date(2026, 5, 4), date(2026, 5, 10))
        assert [t["id"] for t in r] == ["T-IN"]

    def test_solutions_in_range(self, tmp_path):
        sols = tmp_path / "S.jsonl"
        sols.write_text(
            json.dumps({"id": "S-1", "date": "2026-05-06T00:00:00+00:00"}) + "\n" +
            json.dumps({"id": "S-2", "date": "2024-01-01T00:00:00+00:00"}) + "\n",
            encoding="utf-8",
        )
        with patch.object(retro, "SOLUTIONS", sols):
            r = retro.solutions_in_range(date(2026, 5, 4), date(2026, 5, 10))
        assert [s["id"] for s in r] == ["S-1"]

    def test_turns_in_range_skips_blank_lines(self, tmp_path):
        turns = tmp_path / "turns.jsonl"
        turns.write_text(
            "\n" +
            json.dumps({"ts": "2026-05-06T10:00:00+00:00", "mode": "root"}) + "\n" +
            "not-json\n" +
            json.dumps({"ts": "2025-01-01T00:00:00+00:00"}) + "\n",
            encoding="utf-8",
        )
        with patch.object(retro, "TURNS_LOG", turns):
            r = retro.turns_in_range(date(2026, 5, 4), date(2026, 5, 10))
        assert len(r) == 1


class TestSummarise:
    def test_aggregates_basics(self):
        s = retro.summarise(
            closed=[{"id": "T-1"}, {"id": "T-2"}],
            solutions=[{"id": "S-1"}],
            turns=[
                {"mode": "root", "cost": 0.05},
                {"mode": "root", "cost": 0.02},
                {"mode": "normie", "cost": 0.0},
            ],
            evolution=[{"success": True, "tools_used": ["memory_read"]},
                       {"success": False, "tools_used": ["memory_write"]}],
            commits=["abc done", "def fix"],
        )
        assert s["tickets_closed"] == 2
        assert s["solutions_filed"] == 1
        assert s["turns"] == 3
        assert s["by_mode"]["root"] == 2
        assert s["by_mode"]["normie"] == 1
        assert s["error_rate"] == 0.5
        assert s["commits"] == 2
        # tool_counts populated
        assert any(name == "memory_read" for name, _ in s["top_tools"])


class TestRenderRetro:
    def test_header_and_sections(self):
        out = retro.render_retro(
            label="2026-W18",
            start=date(2026, 5, 4), end=date(2026, 5, 10),
            closed=[{"id": "T-1", "title": "Did the thing", "severity": "P2"}],
            solutions=[{"id": "S-1"}],
            commits=["abc closed T-1"],
            summary={
                "tickets_closed": 1, "solutions_filed": 1, "turns": 12,
                "by_mode": {"root": 10, "normie": 2}, "total_cost_usd": 0.07,
                "error_rate": 0.05, "top_tools": [("memory_read", 4)], "commits": 1,
            },
        )
        assert "Retro 2026-W18" in out
        assert "T-1" in out
        assert "memory_read" in out
        assert "$0.0700" in out
        assert "Spend (USD)" in out


class TestRetroMainStdout:
    def test_stdout_mode_writes_no_file(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(retro, "RETROS_DIR", tmp_path / "retros")
        monkeypatch.setattr(retro, "TICKETS_CLOSED", tmp_path / "closed-empty")
        monkeypatch.setattr(retro, "SOLUTIONS", tmp_path / "S-empty.jsonl")
        monkeypatch.setattr(retro, "TURNS_LOG", tmp_path / "turns-empty.jsonl")
        monkeypatch.setattr(retro, "EVOLUTION_LOG", tmp_path / "evo-empty.jsonl")
        # commits via subprocess — patch to return empty
        monkeypatch.setattr(retro, "commits_in_range", lambda s, e: [])

        old_argv = sys.argv
        try:
            sys.argv = ["retro.py", "--stdout"]
            rc = retro.main()
        finally:
            sys.argv = old_argv

        assert rc == 0
        out = capsys.readouterr().out
        assert "Retro" in out
        assert not (tmp_path / "retros").exists()
