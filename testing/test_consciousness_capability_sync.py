"""
testing/test_consciousness_capability_sync.py — Tests for SKILL 5.

Coverage:

Tool discovery:
  - test_load_code_tools_missing_dir_returns_error
  - test_load_code_tools_bad_module_returns_error

Missing tools check:
  - test_all_tools_mentioned_passes
  - test_missing_tool_warns
  - test_multiple_missing_warns
  - test_missing_consciousness_file_warns
  - test_triggers_md_counts_as_mention

Phantom tools check:
  - test_no_phantoms_passes
  - test_phantom_with_verb_prefix_warns
  - test_param_suffix_not_flagged_as_phantom
  - test_tool_in_code_not_flagged_as_phantom
  - test_missing_consciousness_skips_gracefully

Coverage check:
  - test_full_coverage_passes
  - test_partial_coverage_below_threshold_warns
  - test_empty_code_tools_passes
  - test_triggers_count_toward_coverage

Strict mode:
  - test_strict_escalates_warn_to_fail

Integration:
  - test_run_check_blocked_on_import_failure
  - test_run_check_writes_report
  - test_run_check_all_in_sync_passes
  - test_run_check_missing_tools_warns
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import consciousness_capability_sync as ccs
from scripts.passive.common import Status


# ── Helpers ───────────────────────────────────────────────────────────────────

SAMPLE_TOOLS = {"memory_read", "memory_write", "web_search", "execute_bash",
                "create_ticket", "speak", "listen"}


def _make_consciousness(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "prompts" / "consciousness.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _make_triggers(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "prompts" / "triggers.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _fake_load_tools(tools: set):
    """Return a patcher that makes _load_code_tools return (tools, None)."""
    return patch.object(ccs, "_load_code_tools", return_value=(tools, None))


# ── _load_code_tools ──────────────────────────────────────────────────────────

class TestLoadCodeTools:
    def test_missing_agent_dir_returns_error(self, tmp_path):
        tools, err = ccs._load_code_tools(tmp_path)
        assert tools is None
        assert err is not None

    def test_malformed_module_returns_error(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "tools.py").write_text("THIS IS NOT VALID PYTHON !!!", encoding="utf-8")
        tools, err = ccs._load_code_tools(tmp_path)
        assert tools is None
        assert err is not None


# ── check_missing_tools ───────────────────────────────────────────────────────

class TestCheckMissingTools:
    def test_all_mentioned_passes(self, tmp_path):
        cons = _make_consciousness(tmp_path,
            "use memory_read, memory_write, web_search, execute_bash")
        tools = {"memory_read", "memory_write", "web_search", "execute_bash"}
        status, lines = ccs.check_missing_tools(tools, cons)
        assert status == Status.PASS

    def test_missing_tool_warns(self, tmp_path):
        cons = _make_consciousness(tmp_path, "use memory_read only")
        tools = {"memory_read", "web_search"}
        status, lines = ccs.check_missing_tools(tools, cons)
        assert status == Status.WARN
        assert any("web_search" in l for l in lines)

    def test_multiple_missing_warns(self, tmp_path):
        cons = _make_consciousness(tmp_path, "use memory_read")
        tools = {"memory_read", "web_search", "execute_bash", "create_ticket"}
        status, lines = ccs.check_missing_tools(tools, cons)
        assert status == Status.WARN
        assert any("web_search" in l for l in lines)
        assert any("execute_bash" in l for l in lines)
        assert any("create_ticket" in l for l in lines)

    def test_missing_consciousness_warns(self, tmp_path):
        cons = tmp_path / "prompts" / "consciousness.txt"  # does not exist
        status, lines = ccs.check_missing_tools({"memory_read"}, cons)
        assert status == Status.WARN
        assert any("not found" in l for l in lines)

    def test_triggers_md_counts_as_mention(self, tmp_path):
        cons = _make_consciousness(tmp_path, "use memory_read")
        triggers = _make_triggers(tmp_path, "trigger: listen, transcribe_file")
        tools = {"memory_read", "listen", "transcribe_file"}
        status, _ = ccs.check_missing_tools(tools, cons, triggers)
        assert status == Status.PASS

    def test_empty_tool_set_passes(self, tmp_path):
        cons = _make_consciousness(tmp_path, "nothing here")
        status, _ = ccs.check_missing_tools(set(), cons)
        assert status == Status.PASS


# ── check_phantom_tools ───────────────────────────────────────────────────────

class TestCheckPhantomTools:
    def test_no_phantoms_passes(self, tmp_path):
        cons = _make_consciousness(tmp_path,
            "use memory_read, memory_write when needed")
        tools = {"memory_read", "memory_write"}
        status, _ = ccs.check_phantom_tools(tools, cons)
        assert status == Status.PASS

    def test_param_suffix_not_flagged(self, tmp_path):
        # channel_id, message_id, max_results are params — should NOT be phantom
        cons = _make_consciousness(tmp_path,
            "send to channel_id with max_results, message_id param")
        tools = {"telegram_send"}
        status, _ = ccs.check_phantom_tools(tools, cons)
        assert status == Status.PASS

    def test_phantom_with_verb_prefix_warns(self, tmp_path):
        # list_codebase is an old tool name, not in code_tools
        cons = _make_consciousness(tmp_path,
            "use list_codebase to search the project files")
        tools = {"search_codebase"}  # renamed
        status, lines = ccs.check_phantom_tools(tools, cons)
        assert status == Status.WARN
        assert any("list_codebase" in l for l in lines)

    def test_tool_in_code_not_flagged_as_phantom(self, tmp_path):
        cons = _make_consciousness(tmp_path,
            "use search_codebase to explore the project")
        tools = {"search_codebase"}
        status, _ = ccs.check_phantom_tools(tools, cons)
        assert status == Status.PASS

    def test_missing_consciousness_skips_gracefully(self, tmp_path):
        cons = tmp_path / "prompts" / "consciousness.txt"  # does not exist
        status, lines = ccs.check_phantom_tools({"memory_read"}, cons)
        assert status == Status.PASS
        assert any("skip" in l.lower() for l in lines)


# ── check_coverage ────────────────────────────────────────────────────────────

class TestCheckCoverage:
    def test_full_coverage_passes(self, tmp_path):
        tools = {"memory_read", "web_search"}
        cons = _make_consciousness(tmp_path, "memory_read and web_search")
        status, lines = ccs.check_coverage(tools, cons, threshold=0.80)
        assert status == Status.PASS
        assert any("100%" in l for l in lines)

    def test_below_threshold_warns(self, tmp_path):
        tools = {"memory_read", "memory_write", "web_search", "execute_bash",
                 "create_ticket"}
        cons = _make_consciousness(tmp_path, "memory_read only")  # 20% coverage
        status, lines = ccs.check_coverage(tools, cons, threshold=0.80)
        assert status == Status.WARN
        assert any("20%" in l or "1/5" in l for l in lines)

    def test_empty_tools_passes(self, tmp_path):
        cons = _make_consciousness(tmp_path, "nothing")
        status, _ = ccs.check_coverage(set(), cons)
        assert status == Status.PASS

    def test_triggers_count_toward_coverage(self, tmp_path):
        tools = {"memory_read", "listen"}
        cons = _make_consciousness(tmp_path, "use memory_read")
        triggers = _make_triggers(tmp_path, "trigger: listen for wakeword")
        status, _ = ccs.check_coverage(tools, cons, triggers, threshold=0.80)
        assert status == Status.PASS  # both mentioned

    def test_missing_consciousness_warns(self, tmp_path):
        tools = {"memory_read"}
        cons = tmp_path / "prompts" / "consciousness.txt"
        status, _ = ccs.check_coverage(tools, cons)
        assert status == Status.WARN


# ── strict mode ───────────────────────────────────────────────────────────────

class TestStrictMode:
    def test_strict_escalates_warn_to_fail(self, tmp_path):
        tools = {"memory_read", "web_search"}
        cons = _make_consciousness(tmp_path, "use memory_read")  # web_search missing

        with _fake_load_tools(tools), \
             patch("scripts.passive.consciousness_capability_sync.write_report"):
            normal = ccs.run_check(strict=False, root=tmp_path)
            strict = ccs.run_check(strict=True, root=tmp_path)

        assert normal == Status.WARN
        assert strict == Status.FAIL


# ── run_check integration ─────────────────────────────────────────────────────

class TestRunCheck:
    def test_blocked_on_import_failure(self, tmp_path):
        with patch.object(ccs, "_load_code_tools", return_value=(None, "tools.py not found")), \
             patch("scripts.passive.consciousness_capability_sync.write_report"):
            status = ccs.run_check(root=tmp_path)
        assert status == Status.BLOCKED

    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        tools = {"memory_read"}
        _make_consciousness(tmp_path, "memory_read")

        with _fake_load_tools(tools), \
             patch("scripts.passive.common.REPORTS", reports):
            ccs.run_check(root=tmp_path, reports=reports)

        assert (reports / "consciousness_capability_sync.md").exists()

    def test_all_in_sync_passes(self, tmp_path):
        tools = {"memory_read", "web_search"}
        _make_consciousness(tmp_path, "memory_read and web_search are available")

        with _fake_load_tools(tools), \
             patch("scripts.passive.consciousness_capability_sync.write_report"):
            status = ccs.run_check(root=tmp_path)

        assert status == Status.PASS

    def test_missing_tools_warns(self, tmp_path):
        tools = {"memory_read", "listen", "transcribe_file"}
        _make_consciousness(tmp_path, "use memory_read")

        with _fake_load_tools(tools), \
             patch("scripts.passive.consciousness_capability_sync.write_report"):
            status = ccs.run_check(root=tmp_path)

        assert status == Status.WARN

    def test_no_consciousness_file_warns(self, tmp_path):
        # consciousness.txt absent
        with _fake_load_tools({"memory_read"}), \
             patch("scripts.passive.consciousness_capability_sync.write_report"):
            status = ccs.run_check(root=tmp_path)

        assert status == Status.WARN
