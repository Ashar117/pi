"""testing/test_passive_daily_digest.py — Tests for SKILL 13."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import passive_daily_digest as pdd
from scripts.passive.common import Status


def _mock_skill(status: Status):
    mod = MagicMock()
    mod.run_check.return_value = status
    return mod


class TestRunSkill:
    def test_blocked_on_import_error(self, tmp_path):
        with patch("importlib.import_module", side_effect=ImportError("not found")):
            _, status = pdd._run_skill("nonexistent_skill", False, tmp_path, tmp_path)
        assert status == Status.BLOCKED

    def test_pass_status_returned(self, tmp_path):
        mock_mod = _mock_skill(Status.PASS)
        with patch("importlib.import_module", return_value=mock_mod):
            _, status = pdd._run_skill("any_skill", False, tmp_path, tmp_path)
        assert status == Status.PASS


class TestRunCheck:
    def _patch_skills(self, status: Status):
        return patch.object(
            pdd, "_run_skill",
            side_effect=lambda name, **kw: (name, status),
        )

    def test_all_pass_returns_pass(self, tmp_path):
        reports = tmp_path / "reports"
        with self._patch_skills(Status.PASS), \
             patch("scripts.passive.passive_daily_digest.write_report"):
            status = pdd.run_check(root=tmp_path, reports=reports)
        assert status == Status.PASS

    def test_one_fail_returns_fail(self, tmp_path):
        reports = tmp_path / "reports"
        results = iter([Status.FAIL] + [Status.PASS] * 20)
        with patch.object(pdd, "_run_skill",
                          side_effect=lambda n, **kw: (n, next(results))), \
             patch("scripts.passive.passive_daily_digest.write_report"):
            status = pdd.run_check(root=tmp_path, reports=reports)
        assert status == Status.FAIL

    def test_blocked_degrades_to_blocked(self, tmp_path):
        reports = tmp_path / "reports"
        with self._patch_skills(Status.BLOCKED), \
             patch("scripts.passive.passive_daily_digest.write_report"):
            status = pdd.run_check(root=tmp_path, reports=reports)
        assert status == Status.BLOCKED

    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        with self._patch_skills(Status.PASS), \
             patch("scripts.passive.common.REPORTS", reports):
            pdd.run_check(root=tmp_path, reports=reports)
        assert (reports / "passive_daily_digest.md").exists()

    def test_strict_escalates_warn(self, tmp_path):
        reports = tmp_path / "reports"
        with self._patch_skills(Status.WARN), \
             patch("scripts.passive.passive_daily_digest.write_report"):
            normal = pdd.run_check(strict=False, root=tmp_path, reports=reports)
        with self._patch_skills(Status.WARN), \
             patch("scripts.passive.passive_daily_digest.write_report"):
            strict = pdd.run_check(strict=True, root=tmp_path, reports=reports)
        assert normal == Status.WARN
        assert strict == Status.FAIL
