"""
testing/test_half_baked_feature_detector.py — Tests for SKILL 6.

Coverage:
  Stubs: test_no_stubs_passes, test_notimplemented_warns, test_bare_pass_warns,
         test_private_functions_not_flagged, test_real_function_passes
  Tests: test_all_tools_have_tests_passes, test_missing_test_warns,
         test_no_tools_dir_skips
  Markers: test_no_markers_passes, test_todo_warns, test_fixme_warns,
            test_case_insensitive_marker
  Imports: test_no_traps_passes, test_silenced_import_warns
  Env: test_all_keys_used_passes, test_unused_key_warns, test_no_env_example_skips
  Orphans: test_all_imported_passes, test_orphaned_file_warns, test_no_agent_tools_skips
  Integration: test_run_check_clean_passes, test_run_check_writes_report,
               test_strict_escalates_warn_to_fail
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import half_baked_feature_detector as hbd
from scripts.passive.common import Status


# ── check_stub_implementations ────────────────────────────────────────────────

class TestCheckStubImplementations:
    def _py(self, tmp_path: Path, name: str, src: str) -> Path:
        d = tmp_path / "tools"
        d.mkdir(exist_ok=True)
        p = d / name
        p.write_text(src, encoding="utf-8")
        return p

    def test_no_stubs_passes(self, tmp_path):
        self._py(tmp_path, "tools_foo.py",
                 "def compute(x):\n    return x * 2\n")
        status, _ = hbd.check_stub_implementations(tmp_path)
        assert status == Status.PASS

    def test_notimplemented_warns(self, tmp_path):
        self._py(tmp_path, "tools_foo.py",
                 "def speak():\n    raise NotImplementedError\n")
        status, lines = hbd.check_stub_implementations(tmp_path)
        assert status == Status.WARN
        assert any("speak" in l for l in lines)

    def test_bare_pass_warns(self, tmp_path):
        self._py(tmp_path, "tools_foo.py",
                 "def listen():\n    pass\n")
        status, lines = hbd.check_stub_implementations(tmp_path)
        assert status == Status.WARN
        assert any("listen" in l for l in lines)

    def test_docstring_then_pass_warns(self, tmp_path):
        self._py(tmp_path, "tools_foo.py",
                 'def todo_later():\n    """Not done yet."""\n    pass\n')
        status, lines = hbd.check_stub_implementations(tmp_path)
        assert status == Status.WARN

    def test_private_function_not_flagged(self, tmp_path):
        self._py(tmp_path, "tools_foo.py",
                 "def _helper():\n    pass\n")
        status, _ = hbd.check_stub_implementations(tmp_path)
        assert status == Status.PASS

    def test_real_function_passes(self, tmp_path):
        self._py(tmp_path, "tools_foo.py",
                 "def compute(x):\n    result = x + 1\n    return result\n")
        status, _ = hbd.check_stub_implementations(tmp_path)
        assert status == Status.PASS


# ── check_tools_without_tests ─────────────────────────────────────────────────

class TestCheckToolsWithoutTests:
    def test_all_have_tests_passes(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "testing").mkdir()
        (tmp_path / "tools" / "tools_memory.py").write_text("# tool", encoding="utf-8")
        (tmp_path / "testing" / "test_tools_memory.py").write_text("# test", encoding="utf-8")
        status, _ = hbd.check_tools_without_tests(tmp_path)
        assert status == Status.PASS

    def test_missing_test_warns(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "testing").mkdir()
        (tmp_path / "tools" / "tools_web.py").write_text("# tool", encoding="utf-8")
        # No test_tools_web.py
        status, lines = hbd.check_tools_without_tests(tmp_path)
        assert status == Status.WARN
        assert any("tools_web.py" in l for l in lines)

    def test_no_tools_dir_skips(self, tmp_path):
        status, lines = hbd.check_tools_without_tests(tmp_path)
        assert status == Status.PASS
        assert any("skip" in l.lower() for l in lines)


# ── check_todo_markers ────────────────────────────────────────────────────────

class TestCheckTodoMarkers:
    def _py(self, tmp_path: Path, src: str) -> None:
        d = tmp_path / "tools"
        d.mkdir(exist_ok=True)
        (d / "tools_foo.py").write_text(src, encoding="utf-8")

    def test_no_markers_passes(self, tmp_path):
        self._py(tmp_path, "def foo():\n    return 1\n")
        status, _ = hbd.check_todo_markers(tmp_path)
        assert status == Status.PASS

    def test_todo_warns(self, tmp_path):
        self._py(tmp_path, "def foo():\n    pass  # TODO: implement this\n")
        status, lines = hbd.check_todo_markers(tmp_path)
        assert status == Status.WARN
        assert any("TODO" in l for l in lines)

    def test_fixme_warns(self, tmp_path):
        self._py(tmp_path, "# FIXME: broken edge case\ndef bar():\n    return None\n")
        status, lines = hbd.check_todo_markers(tmp_path)
        assert status == Status.WARN
        assert any("FIXME" in l for l in lines)

    def test_stub_marker_warns(self, tmp_path):
        self._py(tmp_path, "def baz():\n    pass  # STUB\n")
        status, lines = hbd.check_todo_markers(tmp_path)
        assert status == Status.WARN

    def test_case_insensitive(self, tmp_path):
        self._py(tmp_path, "# todo: fix this later\n")
        status, _ = hbd.check_todo_markers(tmp_path)
        assert status == Status.WARN


# ── check_graceful_import_traps ───────────────────────────────────────────────

class TestCheckGracefulImportTraps:
    def _py(self, tmp_path: Path, src: str) -> None:
        d = tmp_path / "tools"
        d.mkdir(exist_ok=True)
        (d / "tools_foo.py").write_text(src, encoding="utf-8")

    def test_no_traps_passes(self, tmp_path):
        self._py(tmp_path, "import os\nprint(os.getcwd())\n")
        status, _ = hbd.check_graceful_import_traps(tmp_path)
        assert status == Status.PASS

    def test_silenced_import_warns(self, tmp_path):
        src = (
            "try:\n"
            "    import torch\n"
            "except ImportError:\n"
            "    torch = None\n"
        )
        self._py(tmp_path, src)
        status, lines = hbd.check_graceful_import_traps(tmp_path)
        assert status == Status.WARN
        assert any("torch" in l for l in lines)


# ── check_unused_env_vars ─────────────────────────────────────────────────────

class TestCheckUnusedEnvVars:
    def test_all_keys_used_passes(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "ANTHROPIC_API_KEY=your-key\n", encoding="utf-8"
        )
        d = tmp_path / "tools"
        d.mkdir()
        (d / "tools_foo.py").write_text(
            'key = os.getenv("ANTHROPIC_API_KEY")\n', encoding="utf-8"
        )
        status, _ = hbd.check_unused_env_vars(tmp_path)
        assert status == Status.PASS

    def test_unused_key_warns(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "ANTHROPIC_API_KEY=x\nGHOST_KEY=y\n", encoding="utf-8"
        )
        d = tmp_path / "tools"
        d.mkdir()
        (d / "tools_foo.py").write_text(
            'x = os.getenv("ANTHROPIC_API_KEY")\n', encoding="utf-8"
        )
        status, lines = hbd.check_unused_env_vars(tmp_path)
        assert status == Status.WARN
        assert any("GHOST_KEY" in l for l in lines)

    def test_no_env_example_skips(self, tmp_path):
        status, lines = hbd.check_unused_env_vars(tmp_path)
        assert status == Status.PASS
        assert any("skip" in l.lower() for l in lines)

    def test_comment_lines_ignored(self, tmp_path):
        (tmp_path / ".env.example").write_text(
            "# This is a comment\nANTHROPIC_API_KEY=x\n", encoding="utf-8"
        )
        d = tmp_path / "tools"
        d.mkdir()
        (d / "tools_foo.py").write_text("ANTHROPIC_API_KEY\n", encoding="utf-8")
        status, _ = hbd.check_unused_env_vars(tmp_path)
        assert status == Status.PASS


# ── check_orphaned_tool_files ─────────────────────────────────────────────────

class TestCheckOrphanedToolFiles:
    def test_all_imported_passes(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "agent").mkdir()
        (tmp_path / "tools" / "tools_memory.py").write_text("# tool", encoding="utf-8")
        (tmp_path / "agent" / "tools.py").write_text(
            "from tools.tools_memory import *\n", encoding="utf-8"
        )
        status, _ = hbd.check_orphaned_tool_files(tmp_path)
        assert status == Status.PASS

    def test_orphaned_file_warns(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "agent").mkdir()
        (tmp_path / "tools" / "tools_secret.py").write_text("# tool", encoding="utf-8")
        (tmp_path / "agent" / "tools.py").write_text(
            "from tools.tools_memory import *\n", encoding="utf-8"
        )
        status, lines = hbd.check_orphaned_tool_files(tmp_path)
        assert status == Status.WARN
        assert any("tools_secret.py" in l for l in lines)

    def test_no_tools_dir_skips(self, tmp_path):
        status, lines = hbd.check_orphaned_tool_files(tmp_path)
        assert status == Status.PASS
        assert any("skip" in l.lower() for l in lines)

    def test_no_agent_tools_skips(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "tools_foo.py").write_text("# tool", encoding="utf-8")
        status, lines = hbd.check_orphaned_tool_files(tmp_path)
        assert status == Status.PASS
        assert any("skip" in l.lower() for l in lines)


# ── integration ───────────────────────────────────────────────────────────────

class TestRunCheck:
    def test_clean_passes(self, tmp_path):
        with patch("scripts.passive.half_baked_feature_detector.write_report"):
            status = hbd.run_check(root=tmp_path)
        assert status == Status.PASS

    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        (tmp_path / "tools").mkdir()
        with patch("scripts.passive.common.REPORTS", reports):
            hbd.run_check(root=tmp_path, reports=reports)
        assert (reports / "half_baked_features.md").exists()

    def test_strict_escalates_warn_to_fail(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "testing").mkdir()
        # tools_web.py with no test file -> WARN
        (tmp_path / "tools" / "tools_web.py").write_text("# tool", encoding="utf-8")
        with patch("scripts.passive.half_baked_feature_detector.write_report"):
            normal = hbd.run_check(strict=False, root=tmp_path)
            strict = hbd.run_check(strict=True, root=tmp_path)
        assert normal == Status.WARN
        assert strict == Status.FAIL
