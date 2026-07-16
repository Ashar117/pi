"""testing/test_solution_lesson_distiller.py — Tests for SKILL 10."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import solution_lesson_distiller as sld
from scripts.passive.common import Status


def _recent_iso(days_ago: int = 1) -> str:
    """A genuinely recent ISO date (T-176: never hardcode 'recent' dates — they rot)."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _sol_file(tmp_path, records):
    d = tmp_path / "solutions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SOLUTIONS.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


class TestCheckRecency:
    def test_empty_warns(self):
        status, lines = sld.check_recency([])
        assert status == Status.WARN

    def test_recent_passes(self):
        sols = [{"title": "Fix foo", "solved_at": _recent_iso()}]
        status, _ = sld.check_recency(sols)
        assert status == Status.PASS

    def test_stale_warns(self):
        sols = [{"title": "Fix foo", "solved_at": "2020-01-01T00:00:00Z"}]
        status, lines = sld.check_recency(sols)
        assert status == Status.WARN
        assert any("day" in l.lower() or "stale" in l.lower() for l in lines)

    def test_no_dates_warns(self):
        sols = [{"title": "no date here"}]
        status, _ = sld.check_recency(sols)
        assert status == Status.WARN


class TestCheckPatterns:
    def test_empty_passes(self):
        status, _ = sld.check_patterns([])
        assert status == Status.PASS

    def test_top_tags_shown(self):
        sols = [
            {"root_cause": "import error"},
            {"root_cause": "import error"},
            {"root_cause": "import error"},
            {"root_cause": "type mismatch"},
        ]
        status, lines = sld.check_patterns(sols)
        assert status == Status.PASS
        assert any("import error" in l for l in lines)

    def test_list_tags(self):
        sols = [{"root_cause": ["tag_a", "tag_b"]}] * 3
        status, lines = sld.check_patterns(sols)
        assert status == Status.PASS
        assert any("tag_a" in l for l in lines)


class TestCheckGaps:
    def test_no_gaps_passes(self):
        sols = [{"root_cause": "known"} for _ in range(10)]
        status, _ = sld.check_gaps(sols)
        assert status == Status.PASS

    def test_many_gaps_warns(self):
        sols = [{"title": "t"}] * 10  # no root_cause
        status, lines = sld.check_gaps(sols)
        assert status == Status.WARN
        assert any("100%" in l or "10/10" in l for l in lines)

    def test_empty_passes(self):
        status, _ = sld.check_gaps([])
        assert status == Status.PASS


class TestCheckDuplicates:
    def test_no_dupes_passes(self):
        sols = [
            {"title": "Fix import error in tools_foo"},
            {"title": "Fix type mismatch in agent"},
        ]
        status, _ = sld.check_duplicates(sols)
        assert status == Status.PASS

    def test_duplicate_titles_warn(self):
        sols = [
            {"title": "Fix import error in tools_foo module properly"},
            {"title": "Fix import error in tools_foo module properly also"},
        ]
        status, lines = sld.check_duplicates(sols)
        assert status == Status.WARN

    def test_single_sol_passes(self):
        status, _ = sld.check_duplicates([{"title": "only one"}])
        assert status == Status.PASS


class TestRunCheck:
    def test_empty_solutions_warns(self, tmp_path):
        _sol_file(tmp_path, [])
        reports = tmp_path / "reports"
        with patch("scripts.passive.solution_lesson_distiller.write_report"):
            status = sld.run_check(root=tmp_path, reports=reports)
        assert status == Status.WARN

    def test_healthy_solutions_passes(self, tmp_path):
        records = [
            {"title": f"Fix issue {i}", "root_cause": "type error",
             "solved_at": _recent_iso()}
            for i in range(5)
        ]
        _sol_file(tmp_path, records)
        reports = tmp_path / "reports"
        with patch("scripts.passive.solution_lesson_distiller.write_report"):
            status = sld.run_check(root=tmp_path, reports=reports)
        assert status == Status.PASS

    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        with patch("scripts.passive.common.REPORTS", reports):
            sld.run_check(root=tmp_path, reports=reports)
        assert (reports / "solution_lesson_distiller.md").exists()

    def test_strict_escalates(self, tmp_path):
        _sol_file(tmp_path, [{"title": "t"}] * 5)  # missing root_cause + stale
        reports = tmp_path / "reports"
        with patch("scripts.passive.solution_lesson_distiller.write_report"):
            normal = sld.run_check(strict=False, root=tmp_path, reports=reports)
            strict = sld.run_check(strict=True, root=tmp_path, reports=reports)
        assert normal == Status.WARN
        assert strict == Status.FAIL
