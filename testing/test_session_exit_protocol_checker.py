"""
testing/test_session_exit_protocol_checker.py — Tests for SKILL 2.

Coverage:
  Happy path  → all checks PASS
  Violations  → stale verify, verify FAIL, old PI.md, old checkpoints,
                FAIL report present, privacy guard FAIL, dirty git tree
  Edge cases  → missing files, malformed STATUS.md, strict mode
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import session_exit_protocol_checker as sepc
from scripts.passive.common import Status


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_status_md(path: Path, overall: str = "PASS") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# docs/STATUS.md\n\n**Overall:** {overall}\n\n"
        "## Syntax check\n- Files checked: 88\n- Passed: 88\n",
        encoding="utf-8",
    )


def _write_report(path: Path, status: str = "PASS") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Report\n**Status:** {status}  \n**Generated:** 2026-01-01T00:00:00Z\n\n---\n\nBody.\n",
        encoding="utf-8",
    )


def _touch_now(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("content", encoding="utf-8")
    # mtime is already now


def _touch_old(path: Path, days_old: int = 3) -> None:
    """Write file then backdate its mtime by N days."""
    _touch_now(path)
    old_ts = time.time() - days_old * 86400
    import os
    os.utime(path, (old_ts, old_ts))


# ── check_verify_recency ──────────────────────────────────────────────────────

class TestCheckVerifyRecency:
    def test_recent_status_md_passes(self, tmp_path):
        status_md = tmp_path / "docs" / "STATUS.md"
        _write_status_md(status_md)  # just written = mtime now
        result, lines = sepc.check_verify_recency(status_md)
        assert result == Status.PASS

    def test_old_status_md_warns(self, tmp_path):
        status_md = tmp_path / "docs" / "STATUS.md"
        _touch_old(status_md, days_old=1)
        result, lines = sepc.check_verify_recency(status_md)
        assert result == Status.WARN
        assert any("verify.py" in l for l in lines)

    def test_missing_status_md_warns(self, tmp_path):
        missing = tmp_path / "docs" / "STATUS.md"
        result, lines = sepc.check_verify_recency(missing)
        assert result == Status.WARN


# ── check_verify_pass ─────────────────────────────────────────────────────────

class TestCheckVerifyPass:
    def test_pass_status_passes(self, tmp_path):
        status_md = tmp_path / "docs" / "STATUS.md"
        _write_status_md(status_md, "PASS")
        result, _ = sepc.check_verify_pass(status_md)
        assert result == Status.PASS

    def test_fail_status_fails(self, tmp_path):
        status_md = tmp_path / "docs" / "STATUS.md"
        _write_status_md(status_md, "FAIL")
        result, lines = sepc.check_verify_pass(status_md)
        assert result == Status.FAIL
        assert any("FAIL" in l for l in lines)

    def test_missing_file_warns(self, tmp_path):
        result, _ = sepc.check_verify_pass(tmp_path / "STATUS.md")
        assert result == Status.WARN

    def test_malformed_status_warns(self, tmp_path):
        status_md = tmp_path / "STATUS.md"
        status_md.write_text("# Nothing useful here\n", encoding="utf-8")
        result, _ = sepc.check_verify_pass(status_md)
        assert result == Status.WARN


# ── check_pi_md_refreshed ─────────────────────────────────────────────────────

class TestCheckPiMdRefreshed:
    def test_modified_today_passes(self, tmp_path):
        pi = tmp_path / "PI.md"
        _touch_now(pi)
        result, _ = sepc.check_pi_md_refreshed(pi)
        assert result == Status.PASS

    def test_modified_yesterday_warns(self, tmp_path):
        pi = tmp_path / "PI.md"
        _touch_old(pi, days_old=1)
        result, lines = sepc.check_pi_md_refreshed(pi)
        assert result == Status.WARN
        assert any("PI.md" in l for l in lines)

    def test_missing_pi_md_warns(self, tmp_path):
        result, _ = sepc.check_pi_md_refreshed(tmp_path / "PI.md")
        assert result == Status.WARN


# ── check_checkpoints_updated ─────────────────────────────────────────────────

class TestCheckCheckpointsUpdated:
    def test_updated_today_passes(self, tmp_path):
        cp = tmp_path / "CHECKPOINTS" / "current.md"
        _touch_now(cp)
        result, _ = sepc.check_checkpoints_updated(cp)
        assert result == Status.PASS

    def test_not_updated_today_warns(self, tmp_path):
        cp = tmp_path / "CHECKPOINTS" / "current.md"
        _touch_old(cp, days_old=2)
        result, lines = sepc.check_checkpoints_updated(cp)
        assert result == Status.WARN
        assert any("CHECKPOINTS" in l for l in lines)

    def test_missing_file_warns(self, tmp_path):
        result, _ = sepc.check_checkpoints_updated(tmp_path / "current.md")
        assert result == Status.WARN


# ── check_no_fail_reports ─────────────────────────────────────────────────────

class TestCheckNoFailReports:
    def test_all_pass_reports_passes(self, tmp_path):
        _write_report(tmp_path / "skill_a.md", "PASS")
        _write_report(tmp_path / "skill_b.md", "WARN")
        result, _ = sepc.check_no_fail_reports(tmp_path)
        assert result == Status.PASS

    def test_fail_report_fails(self, tmp_path):
        _write_report(tmp_path / "skill_a.md", "PASS")
        _write_report(tmp_path / "skill_b.md", "FAIL")
        result, lines = sepc.check_no_fail_reports(tmp_path)
        assert result == Status.FAIL
        assert any("skill_b.md" in l for l in lines)

    def test_missing_reports_dir_passes(self, tmp_path):
        result, _ = sepc.check_no_fail_reports(tmp_path / "nonexistent")
        assert result == Status.PASS

    def test_multiple_fail_reports_all_listed(self, tmp_path):
        _write_report(tmp_path / "a.md", "FAIL")
        _write_report(tmp_path / "b.md", "FAIL")
        result, lines = sepc.check_no_fail_reports(tmp_path)
        assert result == Status.FAIL
        assert len([l for l in lines if "FAIL" in l]) >= 2


# ── check_privacy_guard_pass ──────────────────────────────────────────────────

class TestCheckPrivacyGuardPass:
    def test_pass_report_passes(self, tmp_path):
        _write_report(tmp_path / "privacy_publish_guard.md", "PASS")
        result, _ = sepc.check_privacy_guard_pass(tmp_path)
        assert result == Status.PASS

    def test_warn_report_warns(self, tmp_path):
        _write_report(tmp_path / "privacy_publish_guard.md", "WARN")
        result, _ = sepc.check_privacy_guard_pass(tmp_path)
        assert result == Status.WARN

    def test_fail_report_fails(self, tmp_path):
        _write_report(tmp_path / "privacy_publish_guard.md", "FAIL")
        result, lines = sepc.check_privacy_guard_pass(tmp_path)
        assert result == Status.FAIL

    def test_missing_report_warns(self, tmp_path):
        result, lines = sepc.check_privacy_guard_pass(tmp_path)
        assert result == Status.WARN
        assert any("privacy_publish_guard" in l for l in lines)


# ── check_git_clean ───────────────────────────────────────────────────────────

class TestCheckGitClean:
    def test_clean_tree_passes(self):
        with patch("scripts.passive.session_exit_protocol_checker.git_status_short",
                   return_value=""):
            result, _ = sepc.check_git_clean()
        assert result == Status.PASS

    def test_dirty_tree_warns(self):
        with patch("scripts.passive.session_exit_protocol_checker.git_status_short",
                   return_value=" M PI.md\n?? new.py"):
            result, lines = sepc.check_git_clean()
        assert result == Status.WARN
        assert any("uncommitted" in l for l in lines)


# ── run_check (integration) ───────────────────────────────────────────────────

class TestRunCheck:
    def _setup_clean(self, tmp_path: Path) -> Path:
        """Create a fully clean tmp repo state."""
        reports = tmp_path / "reports"
        docs = tmp_path / "docs"
        _write_status_md(docs / "STATUS.md", "PASS")
        _touch_now(tmp_path / "PI.md")
        _touch_now(tmp_path / "CHECKPOINTS" / "current.md")
        _write_report(reports / "privacy_publish_guard.md", "PASS")
        return reports

    def test_all_clean_passes(self, tmp_path):
        reports = self._setup_clean(tmp_path)
        with patch("scripts.passive.session_exit_protocol_checker.git_status_short",
                   return_value=""), \
             patch("scripts.passive.session_exit_protocol_checker.write_report"):
            status = sepc.run_check(root=tmp_path, reports=reports)
        assert status == Status.PASS

    def test_verify_fail_fails(self, tmp_path):
        reports = self._setup_clean(tmp_path)
        _write_status_md(tmp_path / "docs" / "STATUS.md", "FAIL")
        with patch("scripts.passive.session_exit_protocol_checker.git_status_short",
                   return_value=""), \
             patch("scripts.passive.session_exit_protocol_checker.write_report"):
            status = sepc.run_check(root=tmp_path, reports=reports)
        assert status == Status.FAIL

    def test_strict_mode_escalates_warn(self, tmp_path):
        reports = self._setup_clean(tmp_path)
        # Make PI.md old → WARN
        _touch_old(tmp_path / "PI.md", days_old=1)
        with patch("scripts.passive.session_exit_protocol_checker.git_status_short",
                   return_value=""), \
             patch("scripts.passive.session_exit_protocol_checker.write_report"):
            normal = sepc.run_check(strict=False, root=tmp_path, reports=reports)
            strict = sepc.run_check(strict=True,  root=tmp_path, reports=reports)
        assert normal == Status.WARN
        assert strict == Status.FAIL

    def test_report_written(self, tmp_path):
        reports = self._setup_clean(tmp_path)
        actual_reports = tmp_path / "out_reports"
        with patch("scripts.passive.session_exit_protocol_checker.git_status_short",
                   return_value=""), \
             patch("scripts.passive.common.REPORTS", actual_reports):
            sepc.run_check(root=tmp_path, reports=reports)
        assert (actual_reports / "session_exit_protocol.md").exists()

    def test_fail_report_in_reports_fails(self, tmp_path):
        reports = self._setup_clean(tmp_path)
        _write_report(reports / "some_other_skill.md", "FAIL")
        with patch("scripts.passive.session_exit_protocol_checker.git_status_short",
                   return_value=""), \
             patch("scripts.passive.session_exit_protocol_checker.write_report"):
            status = sepc.run_check(root=tmp_path, reports=reports)
        assert status == Status.FAIL
