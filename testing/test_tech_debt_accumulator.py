"""testing/test_tech_debt_accumulator.py — Tests for SKILL 11."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import tech_debt_accumulator as tda
from scripts.passive.common import Status


def _py(tmp_path, subdir, name, src):
    d = tmp_path / subdir
    d.mkdir(exist_ok=True)
    (d / name).write_text(src, encoding="utf-8")


class TestCheckTodoDensity:
    def test_no_markers_passes(self, tmp_path):
        _py(tmp_path, "tools", "tools_foo.py", "def f():\n    return 1\n")
        status, _ = tda.check_todo_density(tmp_path)
        assert status == Status.PASS

    def test_within_threshold_passes(self, tmp_path):
        src = "\n".join(f"# TODO: item {i}" for i in range(10))
        _py(tmp_path, "tools", "tools_foo.py", src)
        status, _ = tda.check_todo_density(tmp_path)
        assert status == Status.PASS

    def test_exceeds_threshold_warns(self, tmp_path):
        # HIGH_DENSITY is 50; write 55 TODOs
        src = "\n".join(f"# TODO: item {i}" for i in range(55))
        _py(tmp_path, "tools", "tools_foo.py", src)
        status, lines = tda.check_todo_density(tmp_path)
        assert status == Status.WARN
        assert any("55" in l or "warn" in l.lower() for l in lines)

    def test_no_source_dirs_skips(self, tmp_path):
        status, lines = tda.check_todo_density(tmp_path)
        assert status == Status.PASS
        assert any("skip" in l.lower() for l in lines)


class TestCheckSkippedTests:
    def test_no_testing_dir_skips(self, tmp_path):
        status, lines = tda.check_skipped_tests(tmp_path)
        assert status == Status.PASS
        assert any("skip" in l.lower() for l in lines)

    def test_within_threshold_passes(self, tmp_path):
        d = tmp_path / "testing"
        d.mkdir()
        src = "@pytest.mark.skip\ndef test_a(): pass\n"
        (d / "test_foo.py").write_text(src, encoding="utf-8")
        status, _ = tda.check_skipped_tests(tmp_path)
        assert status == Status.PASS

    def test_exceeds_threshold_warns(self, tmp_path):
        d = tmp_path / "testing"
        d.mkdir()
        src = "\n".join(
            f"@pytest.mark.skip\ndef test_{i}(): pass" for i in range(15)
        )
        (d / "test_foo.py").write_text(src, encoding="utf-8")
        status, lines = tda.check_skipped_tests(tmp_path)
        assert status == Status.WARN


class TestCheckSwallowedExceptions:
    def test_no_swallowed_passes(self, tmp_path):
        _py(tmp_path, "tools", "tools_foo.py",
            "try:\n    x()\nexcept ValueError:\n    log()\n")
        status, _ = tda.check_swallowed_exceptions(tmp_path)
        assert status == Status.PASS

    def test_bare_except_pass_warns(self, tmp_path):
        src = "try:\n    risky()\nexcept:\n    pass\n" * 20
        _py(tmp_path, "tools", "tools_foo.py", src)
        status, lines = tda.check_swallowed_exceptions(tmp_path)
        assert status == Status.WARN


class TestCheckTypeIgnores:
    def test_no_ignores_passes(self, tmp_path):
        _py(tmp_path, "tools", "tools_foo.py", "x: int = 1\n")
        status, _ = tda.check_type_ignores(tmp_path)
        assert status == Status.PASS

    def test_exceeds_threshold_warns(self, tmp_path):
        src = "\n".join(f"x = foo()  # type: ignore" for _ in range(25))
        _py(tmp_path, "tools", "tools_foo.py", src)
        status, lines = tda.check_type_ignores(tmp_path)
        assert status == Status.WARN


class TestRunCheck:
    def test_clean_passes(self, tmp_path):
        with patch("scripts.passive.tech_debt_accumulator.write_report"):
            status = tda.run_check(root=tmp_path)
        assert status == Status.PASS

    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        with patch("scripts.passive.common.REPORTS", reports):
            tda.run_check(root=tmp_path, reports=reports)
        assert (reports / "tech_debt_accumulator.md").exists()

    def test_strict_escalates(self, tmp_path):
        # 55 TODOs to trigger WARN
        src = "\n".join(f"# TODO: item {i}" for i in range(55))
        _py(tmp_path, "tools", "tools_foo.py", src)
        with patch("scripts.passive.tech_debt_accumulator.write_report"):
            normal = tda.run_check(strict=False, root=tmp_path)
            strict = tda.run_check(strict=True, root=tmp_path)
        assert normal == Status.WARN
        assert strict == Status.FAIL
