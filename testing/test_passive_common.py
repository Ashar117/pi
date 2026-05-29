"""
testing/test_passive_common.py — Unit tests for scripts/passive/common.py (Phase 0).

Coverage:
  - Status enum values and ordering
  - read_jsonl: happy path, malformed lines, empty file, missing file
  - append_jsonl: creates file, appends correctly
  - write_report: file created, header correct, content present
  - status_to_exit_code: all four statuses
  - worst(): empty list, single, mixed, all-same
  - git helpers: mocked so tests work offline / without repo state
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make sure the project root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import common
from scripts.passive.common import Status


# ── Status enum ───────────────────────────────────────────────────────────────

class TestStatus:
    def test_values(self):
        assert Status.PASS.value    == "PASS"
        assert Status.WARN.value    == "WARN"
        assert Status.FAIL.value    == "FAIL"
        assert Status.BLOCKED.value == "BLOCKED"

    def test_all_members_present(self):
        names = {s.name for s in Status}
        assert names == {"PASS", "WARN", "FAIL", "BLOCKED"}


# ── read_jsonl ────────────────────────────────────────────────────────────────

class TestReadJsonl:
    def test_happy_path(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
        items = common.read_jsonl(f)
        assert len(items) == 2
        assert items[0] == {"a": 1}
        assert items[1] == {"b": 2}

    def test_skips_malformed_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"valid": 1}\nnot json at all\n{"valid": 2}\n', encoding="utf-8")
        items = common.read_jsonl(f)
        assert len(items) == 2
        assert items[0]["valid"] == 1
        assert items[1]["valid"] == 2

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")
        assert common.read_jsonl(f) == []

    def test_blank_lines_skipped(self, tmp_path):
        f = tmp_path / "blanks.jsonl"
        f.write_text('\n\n{"x": 9}\n\n', encoding="utf-8")
        items = common.read_jsonl(f)
        assert len(items) == 1
        assert items[0]["x"] == 9

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "does_not_exist.jsonl"
        assert common.read_jsonl(f) == []


# ── append_jsonl ──────────────────────────────────────────────────────────────

class TestAppendJsonl:
    def test_creates_file_and_appends(self, tmp_path):
        f = tmp_path / "out.jsonl"
        common.append_jsonl(f, {"k": "v1"})
        common.append_jsonl(f, {"k": "v2"})
        lines = [l for l in f.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"k": "v1"}
        assert json.loads(lines[1]) == {"k": "v2"}

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "nested" / "dir" / "out.jsonl"
        common.append_jsonl(f, {"x": 1})
        assert f.exists()


# ── write_report ──────────────────────────────────────────────────────────────

class TestWriteReport:
    def test_creates_file_with_status(self, tmp_path):
        with patch.object(common, "REPORTS", tmp_path):
            path = common.write_report("my_skill.md", "body text", Status.PASS)
        assert path.exists()
        text = path.read_text()
        assert "Status: PASS" in text or "**Status:** PASS" in text
        assert "body text" in text

    def test_warn_status_in_file(self, tmp_path):
        with patch.object(common, "REPORTS", tmp_path):
            path = common.write_report("skill.md", "details", Status.WARN)
        assert "WARN" in path.read_text()

    def test_fail_status_in_file(self, tmp_path):
        with patch.object(common, "REPORTS", tmp_path):
            path = common.write_report("skill.md", "details", Status.FAIL)
        assert "FAIL" in path.read_text()

    def test_timestamp_in_file(self, tmp_path):
        with patch.object(common, "REPORTS", tmp_path):
            path = common.write_report("skill.md", "content", Status.PASS)
        text = path.read_text()
        # ISO timestamp format: YYYY-MM-DDTHH:MM:SSZ
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", text)

    def test_creates_reports_dir_if_missing(self, tmp_path):
        reports_dir = tmp_path / "reports"
        assert not reports_dir.exists()
        with patch.object(common, "REPORTS", reports_dir):
            common.write_report("x.md", "y", Status.PASS)
        assert reports_dir.exists()


# ── status_to_exit_code ───────────────────────────────────────────────────────

class TestStatusToExitCode:
    def test_pass_is_zero(self):
        assert common.status_to_exit_code(Status.PASS) == 0

    def test_warn_is_one(self):
        assert common.status_to_exit_code(Status.WARN) == 1

    def test_fail_is_two(self):
        assert common.status_to_exit_code(Status.FAIL) == 2

    def test_blocked_is_two(self):
        assert common.status_to_exit_code(Status.BLOCKED) == 2


# ── worst() ───────────────────────────────────────────────────────────────────

class TestWorst:
    def test_empty_list_returns_pass(self):
        assert common.worst([]) == Status.PASS

    def test_single_pass(self):
        assert common.worst([Status.PASS]) == Status.PASS

    def test_single_fail(self):
        assert common.worst([Status.FAIL]) == Status.FAIL

    def test_mixed_returns_highest(self):
        assert common.worst([Status.PASS, Status.WARN, Status.FAIL]) == Status.FAIL

    def test_warn_beats_pass(self):
        assert common.worst([Status.PASS, Status.WARN]) == Status.WARN

    def test_all_same(self):
        assert common.worst([Status.WARN, Status.WARN]) == Status.WARN


# ── git helpers (mocked — offline safe) ──────────────────────────────────────

class TestGitHelpers:
    def test_git_ls_files_parses_output(self):
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = "README.md\nPI.md\ndocs/STATUS.md\n"
        with patch.object(common, "run_git", return_value=fake):
            files = common.git_ls_files()
        assert files == ["README.md", "PI.md", "docs/STATUS.md"]

    def test_git_ls_files_returns_empty_on_error(self):
        fake = MagicMock()
        fake.returncode = 1
        fake.stdout = ""
        with patch.object(common, "run_git", return_value=fake):
            assert common.git_ls_files() == []

    def test_git_staged_files_parses_output(self):
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = "agent/tools.py\n"
        with patch.object(common, "run_git", return_value=fake):
            files = common.git_staged_files()
        assert files == ["agent/tools.py"]

    def test_git_status_short_clean(self):
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = ""
        with patch.object(common, "run_git", return_value=fake):
            assert common.git_status_short() == ""

    def test_git_status_short_dirty(self):
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = " M PI.md\n?? new_file.py\n"
        with patch.object(common, "run_git", return_value=fake):
            assert "PI.md" in common.git_status_short()

    def test_run_git_survives_exception(self):
        with patch("subprocess.run", side_effect=OSError("git not found")):
            result = common.run_git(["status"])
        assert result.returncode == 1
        assert result.stdout == ""

    def test_git_check_ignore_true(self):
        fake = MagicMock()
        fake.returncode = 0
        with patch.object(common, "run_git", return_value=fake):
            assert common.git_check_ignore("data/pi.db") is True

    def test_git_check_ignore_false(self):
        fake = MagicMock()
        fake.returncode = 1
        with patch.object(common, "run_git", return_value=fake):
            assert common.git_check_ignore("README.md") is False
