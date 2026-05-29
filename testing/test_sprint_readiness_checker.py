"""
testing/test_sprint_readiness_checker.py — Tests for SKILL 3: sprint_readiness_checker.

Coverage (minimum required + extras):

Happy path (ready → PASS):
  - test_clean_state_passes

P0/P1 ticket blocks:
  - test_p0_ticket_fails
  - test_p1_ticket_fails
  - test_p2_ticket_does_not_block
  - test_no_tickets_dir_passes
  - test_malformed_ticket_skipped

.env checks:
  - test_missing_env_fails
  - test_empty_env_fails
  - test_env_with_keys_passes

Branch checks:
  - test_main_branch_warns
  - test_master_branch_warns
  - test_feature_branch_passes
  - test_unknown_branch_warns

Strict mode:
  - test_strict_escalates_warn_to_fail

Verify status:
  - test_missing_status_md_fails
  - test_status_fail_fails
  - test_status_pass_passes

Privacy guard:
  - test_missing_privacy_report_fails
  - test_privacy_report_warn_warns
  - test_privacy_report_pass_passes
  - test_privacy_report_fail_fails

Git clean:
  - test_dirty_tree_fails

Doc drift (Skill 4 dependency):
  - test_doc_drift_missing_skips_as_pass
  - test_doc_drift_fail_warns
  - test_doc_drift_pass_passes

Integration (run_check):
  - test_run_check_all_clean_passes
  - test_run_check_writes_report
  - test_run_check_strict_mode
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import sprint_readiness_checker as src
from scripts.passive.common import Status


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_git_ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


def _fake_git_fail() -> MagicMock:
    m = MagicMock()
    m.returncode = 1
    m.stdout = ""
    m.stderr = "fatal: not a git repository"
    return m


def _make_ticket(tmp_path: Path, name: str, severity: str, title: str = "A ticket") -> Path:
    ticket_dir = tmp_path / "tickets" / "open"
    ticket_dir.mkdir(parents=True, exist_ok=True)
    ticket = ticket_dir / f"{name}.json"
    ticket.write_text(json.dumps({"id": name, "severity": severity, "title": title}),
                      encoding="utf-8")
    return ticket


def _make_status_md(tmp_path: Path, overall: str = "PASS") -> Path:
    d = tmp_path / "docs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "STATUS.md"
    p.write_text(f"# Status\n\n**Overall:** {overall}\n", encoding="utf-8")
    return p


def _make_report(reports_dir: Path, name: str, status: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    p = reports_dir / name
    p.write_text(f"# Report\n\n**Status:** {status}\n", encoding="utf-8")
    return p


def _make_env(tmp_path: Path, content: str = "ANTHROPIC_API_KEY=sk-abc\n") -> Path:
    p = tmp_path / ".env"
    p.write_text(content, encoding="utf-8")
    return p


# ── check_git_clean ───────────────────────────────────────────────────────────

class TestCheckGitClean:
    def test_clean_tree_passes(self):
        with patch.object(src, "git_status_short", return_value=""):
            status, lines = src.check_git_clean()
        assert status == Status.PASS

    def test_dirty_tree_fails(self):
        with patch.object(src, "git_status_short", return_value=" M scripts/foo.py\n"):
            status, lines = src.check_git_clean()
        assert status == Status.FAIL
        assert any("uncommitted" in l.lower() for l in lines)


# ── check_verify_pass ─────────────────────────────────────────────────────────

class TestCheckVerifyPass:
    def test_status_pass_passes(self, tmp_path):
        _make_status_md(tmp_path, "PASS")
        status, lines = src.check_verify_pass(tmp_path / "docs" / "STATUS.md")
        assert status == Status.PASS

    def test_status_fail_fails(self, tmp_path):
        _make_status_md(tmp_path, "FAIL")
        status, lines = src.check_verify_pass(tmp_path / "docs" / "STATUS.md")
        assert status == Status.FAIL

    def test_missing_status_md_fails(self, tmp_path):
        status, lines = src.check_verify_pass(tmp_path / "docs" / "STATUS.md")
        assert status == Status.FAIL
        assert any("missing" in l.lower() or "unparseable" in l.lower() for l in lines)


# ── check_privacy_guard ───────────────────────────────────────────────────────

class TestCheckPrivacyGuard:
    def test_pass_report_passes(self, tmp_path):
        _make_report(tmp_path, "privacy_publish_guard.md", "PASS")
        status, _ = src.check_privacy_guard(tmp_path)
        assert status == Status.PASS

    def test_warn_report_warns(self, tmp_path):
        _make_report(tmp_path, "privacy_publish_guard.md", "WARN")
        status, _ = src.check_privacy_guard(tmp_path)
        assert status == Status.WARN

    def test_fail_report_fails(self, tmp_path):
        _make_report(tmp_path, "privacy_publish_guard.md", "FAIL")
        status, _ = src.check_privacy_guard(tmp_path)
        assert status == Status.FAIL

    def test_missing_report_fails(self, tmp_path):
        status, lines = src.check_privacy_guard(tmp_path)
        assert status == Status.FAIL
        assert any("not found" in l.lower() or "privacy_publish_guard" in l for l in lines)


# ── check_doc_drift ───────────────────────────────────────────────────────────

class TestCheckDocDrift:
    def test_missing_report_skips_as_pass(self, tmp_path):
        status, lines = src.check_doc_drift(tmp_path)
        assert status == Status.PASS
        assert any("skip" in l.lower() or "not yet built" in l.lower() for l in lines)

    def test_fail_report_warns(self, tmp_path):
        _make_report(tmp_path, "doc_drift_watcher.md", "FAIL")
        status, _ = src.check_doc_drift(tmp_path)
        assert status == Status.WARN

    def test_pass_report_passes(self, tmp_path):
        _make_report(tmp_path, "doc_drift_watcher.md", "PASS")
        status, _ = src.check_doc_drift(tmp_path)
        assert status == Status.PASS


# ── check_no_blocking_tickets ─────────────────────────────────────────────────

class TestCheckNoBlockingTickets:
    def test_no_tickets_dir_passes(self, tmp_path):
        status, lines = src.check_no_blocking_tickets(tmp_path / "tickets" / "open")
        assert status == Status.PASS

    def test_empty_tickets_dir_passes(self, tmp_path):
        (tmp_path / "tickets" / "open").mkdir(parents=True)
        status, _ = src.check_no_blocking_tickets(tmp_path / "tickets" / "open")
        assert status == Status.PASS

    def test_p0_ticket_fails(self, tmp_path):
        _make_ticket(tmp_path, "T-001", "P0", "Critical bug")
        status, lines = src.check_no_blocking_tickets(tmp_path / "tickets" / "open")
        assert status == Status.FAIL
        assert any("T-001" in l for l in lines)

    def test_p1_ticket_fails(self, tmp_path):
        _make_ticket(tmp_path, "T-002", "P1", "High priority")
        status, lines = src.check_no_blocking_tickets(tmp_path / "tickets" / "open")
        assert status == Status.FAIL
        assert any("T-002" in l for l in lines)

    def test_p2_ticket_does_not_block(self, tmp_path):
        _make_ticket(tmp_path, "T-003", "P2", "Medium priority")
        status, _ = src.check_no_blocking_tickets(tmp_path / "tickets" / "open")
        assert status == Status.PASS

    def test_p3_ticket_does_not_block(self, tmp_path):
        _make_ticket(tmp_path, "T-004", "P3", "Low priority")
        status, _ = src.check_no_blocking_tickets(tmp_path / "tickets" / "open")
        assert status == Status.PASS

    def test_multiple_blocking_tickets_all_listed(self, tmp_path):
        _make_ticket(tmp_path, "T-005", "P0", "First critical")
        _make_ticket(tmp_path, "T-006", "P1", "Second urgent")
        status, lines = src.check_no_blocking_tickets(tmp_path / "tickets" / "open")
        assert status == Status.FAIL
        assert any("T-005" in l for l in lines)
        assert any("T-006" in l for l in lines)

    def test_malformed_ticket_skipped_gracefully(self, tmp_path):
        ticket_dir = tmp_path / "tickets" / "open"
        ticket_dir.mkdir(parents=True)
        bad = ticket_dir / "corrupt.json"
        bad.write_text("{not valid json", encoding="utf-8")
        # Should not raise; other valid tickets still processed
        status, _ = src.check_no_blocking_tickets(ticket_dir)
        assert status == Status.PASS

    def test_ticket_uses_sev_field_too(self, tmp_path):
        ticket_dir = tmp_path / "tickets" / "open"
        ticket_dir.mkdir(parents=True)
        t = ticket_dir / "T-007.json"
        t.write_text(json.dumps({"id": "T-007", "sev": "P0", "title": "Alt field"}),
                     encoding="utf-8")
        status, lines = src.check_no_blocking_tickets(ticket_dir)
        assert status == Status.FAIL
        assert any("T-007" in l for l in lines)


# ── check_branch ──────────────────────────────────────────────────────────────

class TestCheckBranch:
    def test_main_branch_warns(self):
        status, lines = src.check_branch("main")
        assert status == Status.WARN
        assert any("main" in l for l in lines)

    def test_master_branch_warns(self):
        status, lines = src.check_branch("master")
        assert status == Status.WARN
        assert any("master" in l for l in lines)

    def test_feature_branch_passes(self):
        status, lines = src.check_branch("feature/skill-3")
        assert status == Status.PASS
        assert any("feature/skill-3" in l for l in lines)

    def test_unknown_branch_warns(self):
        status, lines = src.check_branch("unknown")
        assert status == Status.WARN

    def test_reads_git_when_no_branch_given(self):
        with patch.object(src, "get_current_branch", return_value="feature/test"):
            status, _ = src.check_branch()
        assert status == Status.PASS


# ── check_env_file ────────────────────────────────────────────────────────────

class TestCheckEnvFile:
    def test_env_with_keys_passes(self, tmp_path):
        _make_env(tmp_path, "ANTHROPIC_API_KEY=sk-abc\nOPENAI_API_KEY=sk-xyz\n")
        status, lines = src.check_env_file(tmp_path)
        assert status == Status.PASS
        assert any("2" in l for l in lines)  # 2 key(s)

    def test_env_comment_only_fails(self, tmp_path):
        _make_env(tmp_path, "# This is a comment\n# Another comment\n\n")
        status, lines = src.check_env_file(tmp_path)
        assert status == Status.FAIL
        assert any("empty" in l.lower() for l in lines)

    def test_missing_env_fails(self, tmp_path):
        status, lines = src.check_env_file(tmp_path)
        assert status == Status.FAIL
        assert any("not found" in l.lower() or ".env" in l for l in lines)

    def test_empty_env_fails(self, tmp_path):
        _make_env(tmp_path, "")
        status, lines = src.check_env_file(tmp_path)
        assert status == Status.FAIL

    def test_env_with_blank_lines_counts_only_real_keys(self, tmp_path):
        _make_env(tmp_path, "\n\nANTHROPIC_API_KEY=sk-abc\n\n# comment\n")
        status, lines = src.check_env_file(tmp_path)
        assert status == Status.PASS
        assert any("1" in l for l in lines)  # 1 key


# ── strict mode ───────────────────────────────────────────────────────────────

class TestStrictMode:
    def test_strict_escalates_warn_to_fail(self, tmp_path):
        reports = tmp_path / "reports"
        _make_env(tmp_path)
        _make_status_md(tmp_path, "PASS")
        _make_report(reports, "privacy_publish_guard.md", "PASS")
        (tmp_path / "tickets" / "open").mkdir(parents=True)

        with patch.object(src, "git_status_short", return_value=""), \
             patch.object(src, "get_current_branch", return_value="main"), \
             patch("scripts.passive.sprint_readiness_checker.write_report"):
            status_normal = src.run_check(strict=False, root=tmp_path, reports=reports)
            status_strict = src.run_check(strict=True, root=tmp_path, reports=reports)

        # main branch → WARN; strict should escalate to FAIL
        assert status_normal == Status.WARN
        assert status_strict == Status.FAIL


# ── run_check integration ─────────────────────────────────────────────────────

class TestRunCheck:
    def _setup_clean(self, tmp_path: Path) -> Path:
        reports = tmp_path / "reports"
        _make_env(tmp_path)
        _make_status_md(tmp_path, "PASS")
        _make_report(reports, "privacy_publish_guard.md", "PASS")
        (tmp_path / "tickets" / "open").mkdir(parents=True)
        return reports

    def test_run_check_all_clean_passes(self, tmp_path):
        reports = self._setup_clean(tmp_path)

        with patch.object(src, "git_status_short", return_value=""), \
             patch.object(src, "get_current_branch", return_value="feature/test"), \
             patch("scripts.passive.sprint_readiness_checker.write_report"):
            status = src.run_check(root=tmp_path, reports=reports)

        assert status == Status.PASS

    def test_run_check_writes_report(self, tmp_path):
        reports = self._setup_clean(tmp_path)

        with patch.object(src, "git_status_short", return_value=""), \
             patch.object(src, "get_current_branch", return_value="feature/test"), \
             patch("scripts.passive.common.REPORTS", reports):
            src.run_check(root=tmp_path, reports=reports)

        assert (reports / "sprint_readiness.md").exists()

    def test_run_check_p0_ticket_fails(self, tmp_path):
        reports = self._setup_clean(tmp_path)
        _make_ticket(tmp_path, "T-010", "P0", "Critical blocker")

        with patch.object(src, "git_status_short", return_value=""), \
             patch.object(src, "get_current_branch", return_value="feature/test"), \
             patch("scripts.passive.sprint_readiness_checker.write_report"):
            status = src.run_check(root=tmp_path, reports=reports)

        assert status == Status.FAIL

    def test_run_check_dirty_tree_fails(self, tmp_path):
        reports = self._setup_clean(tmp_path)

        with patch.object(src, "git_status_short", return_value=" M scripts/foo.py\n"), \
             patch.object(src, "get_current_branch", return_value="feature/test"), \
             patch("scripts.passive.sprint_readiness_checker.write_report"):
            status = src.run_check(root=tmp_path, reports=reports)

        assert status == Status.FAIL

    def test_run_check_missing_env_fails(self, tmp_path):
        reports = tmp_path / "reports"
        # No .env created
        _make_status_md(tmp_path, "PASS")
        _make_report(reports, "privacy_publish_guard.md", "PASS")
        (tmp_path / "tickets" / "open").mkdir(parents=True)

        with patch.object(src, "git_status_short", return_value=""), \
             patch.object(src, "get_current_branch", return_value="feature/test"), \
             patch("scripts.passive.sprint_readiness_checker.write_report"):
            status = src.run_check(root=tmp_path, reports=reports)

        assert status == Status.FAIL

    def test_run_check_main_branch_warns(self, tmp_path):
        reports = self._setup_clean(tmp_path)

        with patch.object(src, "git_status_short", return_value=""), \
             patch.object(src, "get_current_branch", return_value="main"), \
             patch("scripts.passive.sprint_readiness_checker.write_report"):
            status = src.run_check(root=tmp_path, reports=reports)

        assert status == Status.WARN

    def test_run_check_strict_mode(self, tmp_path):
        reports = self._setup_clean(tmp_path)

        with patch.object(src, "git_status_short", return_value=""), \
             patch.object(src, "get_current_branch", return_value="main"), \
             patch("scripts.passive.sprint_readiness_checker.write_report"):
            status = src.run_check(strict=True, root=tmp_path, reports=reports)

        assert status == Status.FAIL
