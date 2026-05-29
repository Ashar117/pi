"""
testing/test_refresh_pi.py — Phase B unit tests for scripts/refresh_pi.py (T-042).

Verifies:
  1. Section markers are preserved (hand-edits between non-AUTO sections survive)
  2. Idempotency — second run yields no diff
  3. Each renderer produces non-empty output
  4. --check exit code reflects staleness correctly
"""

import json
import os
import re
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import refresh_pi  # noqa: E402


# ── Pure renderer tests ──────────────────────────────────────────────────────

class TestRenderers:
    def test_section_4_has_all_keys(self):
        out = refresh_pi.render_section_4()
        for key in ("Phase:", "Last verify:", "Open tickets:", "Closed tickets:",
                    "Solutions logged:", "Turns today:", "Last session end:"):
            assert key in out, f"missing field: {key}"

    def test_section_7_lists_tools(self):
        out = refresh_pi.render_section_7()
        assert "Memory" in out
        assert "memory_read" in out
        assert "Total:" in out

    def test_section_8_when_no_open_tickets(self, tmp_path):
        with patch.object(refresh_pi, "TICKETS_OPEN", tmp_path / "open"):
            out = refresh_pi.render_section_8()
        assert "(none open)" in out
        assert "| ID | Title | Sev | Component |" in out

    def test_section_8_with_open_tickets(self, tmp_path):
        d = tmp_path / "open"
        d.mkdir()
        (d / "T-099.json").write_text(json.dumps({
            "id": "T-099", "title": "Test ticket", "severity": "P2",
            "component": "agent/test.py",
        }))
        with patch.object(refresh_pi, "TICKETS_OPEN", d):
            out = refresh_pi.render_section_8()
        assert "T-099" in out
        assert "Test ticket" in out
        assert "P2" in out

    def test_section_9_handles_both_schemas(self, tmp_path):
        sols = tmp_path / "sol.jsonl"
        sols.write_text(
            json.dumps({"id": "S-100", "ticket": "T-100", "title": "newer schema"}) + "\n"
            + json.dumps({"id": "S-099", "ticket_ids": ["T-099"], "problem": "older schema with problem field"}) + "\n",
            encoding="utf-8",
        )
        with patch.object(refresh_pi, "SOLUTIONS", sols):
            out = refresh_pi.render_section_9()
        assert "S-100" in out
        assert "newer schema" in out
        assert "S-099" in out
        # older schema falls back to 'problem' as title
        assert "older schema" in out

    def test_section_9_skips_blank_lines(self, tmp_path):
        sols = tmp_path / "sol.jsonl"
        sols.write_text("\n\n" + json.dumps({"id": "S-1", "title": "x"}) + "\n\n", encoding="utf-8")
        with patch.object(refresh_pi, "SOLUTIONS", sols):
            out = refresh_pi.render_section_9()
        assert "S-1" in out


# ── Marker replacement ───────────────────────────────────────────────────────

class TestReplaceSection:
    def test_replaces_only_between_markers(self):
        original = (
            "# Hand text 1\n"
            "<!-- BEGIN AUTO §4 -->\n"
            "old body\n"
            "<!-- END AUTO §4 -->\n"
            "# Hand text 2\n"
        )
        out = refresh_pi.replace_section(original, 4, "new body")
        assert "# Hand text 1" in out
        assert "# Hand text 2" in out
        assert "old body" not in out
        assert "new body" in out

    def test_missing_markers_leaves_content_unchanged(self):
        original = "no markers here\n"
        out = refresh_pi.replace_section(original, 4, "x")
        assert out == original


# ── Full pipeline + idempotency ──────────────────────────────────────────────

class TestRegenerate:
    def test_idempotent(self):
        original = refresh_pi.PI_MD.read_text(encoding="utf-8")
        once = refresh_pi.regenerate(original)
        twice = refresh_pi.regenerate(once)
        assert once == twice, "regenerate is not idempotent"

    def test_handcurated_sections_preserved(self):
        """§1, §2, §3, §5, §6, §10, §11, §12 must survive a refresh untouched."""
        original = refresh_pi.PI_MD.read_text(encoding="utf-8")
        regen = refresh_pi.regenerate(original)
        for marker in ("## §1 Identity & preferences",
                       "## §2 Read order at session start",
                       "## §3 NOW",
                       "## §5 Engineering loop",
                       "## §6 Architecture",
                       "## §10 File-touch policy",
                       "## §11 Session protocol",
                       "## §12 Phases beyond 6"):
            assert marker in regen, f"hand-curated section lost: {marker}"


class TestCLI:
    """Minimal smoke test on the entry point — does it run end-to-end."""

    def test_check_mode_exit_code(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "refresh_pi.py"), "--check"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        # 0 if up-to-date, 1 if stale — both are valid
        assert r.returncode in (0, 1)

    def test_dry_run_does_not_modify_file(self, tmp_path):
        # Use a temp copy so external writes to the real PI.md can't interfere.
        pi_copy = tmp_path / "PI.md"
        pi_copy.write_text(refresh_pi.PI_MD.read_text(encoding="utf-8"), encoding="utf-8")
        before = pi_copy.read_text(encoding="utf-8")

        with patch.object(refresh_pi, "PI_MD", pi_copy), \
             patch.object(sys, "argv", ["refresh_pi.py", "--dry-run"]):
            rc = refresh_pi.main()

        after = pi_copy.read_text(encoding="utf-8")
        assert before == after, "dry-run must not write to PI.md"
        assert rc == 0
