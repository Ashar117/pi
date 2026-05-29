"""testing/test_verify_trend_watcher.py — Tests for SKILL 9."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import verify_trend_watcher as vtw
from scripts.passive.common import Status


def _run(tmp_path, records):
    hist = tmp_path / "analysis" / "verify_history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return tmp_path


class TestCheckTrendDirection:
    def test_not_enough_runs_passes(self):
        status, lines = vtw.check_trend_direction([{"passed": 9, "total": 10}])
        assert status == Status.PASS

    def test_regression_fails(self):
        runs = [
            {"passed": 100, "total": 100},
            {"passed": 85, "total": 100},
        ]
        status, lines = vtw.check_trend_direction(runs)
        assert status == Status.FAIL
        assert any("15.0" in l or "drop" in l.lower() or "regression" in l.lower() for l in lines)

    def test_small_slip_warns(self):
        runs = [
            {"passed": 100, "total": 100},
            {"passed": 95, "total": 100},
        ]
        status, lines = vtw.check_trend_direction(runs)
        assert status == Status.WARN

    def test_stable_passes(self):
        runs = [
            {"passed": 90, "total": 100},
            {"passed": 92, "total": 100},
        ]
        status, lines = vtw.check_trend_direction(runs)
        assert status == Status.PASS

    def test_missing_rate_warns(self):
        runs = [{"foo": "bar"}, {"baz": "qux"}]
        status, _ = vtw.check_trend_direction(runs)
        assert status == Status.WARN


class TestCheckStagnation:
    def _runs(self, rates):
        return [{"passed": int(r), "total": 100} for r in rates]

    def test_not_enough_runs_passes(self):
        status, _ = vtw.check_stagnation(self._runs([80, 81, 82]))
        assert status == Status.PASS

    def test_no_improvement_warns(self):
        runs = self._runs([90, 90, 89, 90, 90])
        status, lines = vtw.check_stagnation(runs)
        assert status == Status.WARN

    def test_improving_passes(self):
        runs = self._runs([80, 82, 84, 86, 90])
        status, _ = vtw.check_stagnation(runs)
        assert status == Status.PASS


class TestCheckFailureChurn:
    def test_no_churn_passes(self):
        runs = [
            {"failed_tests": ["test_a"]},
            {"failed_tests": ["test_b"]},
        ]
        status, _ = vtw.check_failure_churn(runs)
        assert status == Status.PASS

    def test_churning_test_warns(self):
        runs = [{"failed_tests": ["test_x"]}] * 5
        status, lines = vtw.check_failure_churn(runs)
        assert status == Status.WARN
        assert any("test_x" in l for l in lines)

    def test_not_enough_runs(self):
        runs = [{"failed_tests": ["test_a"]}]
        status, _ = vtw.check_failure_churn(runs)
        assert status == Status.PASS


class TestRunCheck:
    def test_no_history_passes(self, tmp_path):
        reports = tmp_path / "reports"
        with patch("scripts.passive.verify_trend_watcher.write_report"):
            status = vtw.run_check(root=tmp_path, reports=reports)
        assert status == Status.PASS

    def test_regression_fails(self, tmp_path):
        _run(tmp_path, [
            {"passed": 100, "total": 100},
            {"passed": 85, "total": 100},
        ])
        reports = tmp_path / "reports"
        with patch("scripts.passive.verify_trend_watcher.write_report"):
            status = vtw.run_check(root=tmp_path, reports=reports)
        assert status == Status.FAIL

    def test_strict_escalates(self, tmp_path):
        _run(tmp_path, [
            {"passed": 95, "total": 100},
            {"passed": 93, "total": 100},
        ])
        reports = tmp_path / "reports"
        with patch("scripts.passive.verify_trend_watcher.write_report"):
            normal = vtw.run_check(strict=False, root=tmp_path, reports=reports)
            strict = vtw.run_check(strict=True, root=tmp_path, reports=reports)
        assert normal == Status.WARN
        assert strict == Status.FAIL

    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        with patch("scripts.passive.common.REPORTS", reports):
            vtw.run_check(root=tmp_path, reports=reports)
        assert (reports / "verify_trend_watcher.md").exists()
