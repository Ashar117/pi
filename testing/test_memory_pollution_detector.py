"""testing/test_memory_pollution_detector.py — Tests for SKILL 12."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import memory_pollution_detector as mpd
from scripts.passive.common import Status


LONG_AGO = (datetime.now(timezone.utc) - timedelta(days=100)).strftime(
    "%Y-%m-%dT%H:%M:%SZ"
)
RECENT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestCheckL1Memory:
    def _l1(self, tmp_path, data, name="l1.json"):
        d = tmp_path / "memory"
        d.mkdir(exist_ok=True)
        (d / name).write_text(json.dumps(data), encoding="utf-8")

    def test_no_memory_dir_passes(self, tmp_path):
        status, lines = mpd.check_l1_memory(tmp_path)
        assert status == Status.PASS

    def test_healthy_entries_pass(self, tmp_path):
        data = {
            "key1": {"value": "hello", "updated_at": RECENT},
            "key2": {"value": "world", "updated_at": RECENT},
        }
        self._l1(tmp_path, data)
        status, _ = mpd.check_l1_memory(tmp_path)
        assert status == Status.PASS

    def test_stale_entry_warns(self, tmp_path):
        data = {
            "old_key": {"value": "stale", "updated_at": LONG_AGO},
        }
        self._l1(tmp_path, data)
        status, lines = mpd.check_l1_memory(tmp_path)
        assert status == Status.WARN
        assert any("stale" in l.lower() for l in lines)

    def test_oversized_entry_warns(self, tmp_path):
        big_val = {"value": "x" * 5000, "updated_at": RECENT}
        data = {"big_key": big_val}
        self._l1(tmp_path, data)
        status, lines = mpd.check_l1_memory(tmp_path)
        assert status == Status.WARN
        assert any("oversized" in l.lower() or "5" in l for l in lines)


class TestCheckVaultNotes:
    def test_no_vault_passes(self, tmp_path):
        status, lines = mpd.check_vault_notes(tmp_path)
        assert status == Status.PASS

    def test_healthy_notes_pass(self, tmp_path):
        d = tmp_path / "vault" / "notes"
        d.mkdir(parents=True)
        (d / "my_note.md").write_text(
            "---\ntitle: My Note\n---\n\nSome content here that is substantial.\n",
            encoding="utf-8",
        )
        status, _ = mpd.check_vault_notes(tmp_path)
        assert status == Status.PASS

    def test_empty_note_warns(self, tmp_path):
        d = tmp_path / "vault" / "notes"
        d.mkdir(parents=True)
        (d / "empty.md").write_text("", encoding="utf-8")
        status, lines = mpd.check_vault_notes(tmp_path)
        assert status == Status.WARN

    def test_no_frontmatter_warns(self, tmp_path):
        d = tmp_path / "vault" / "notes"
        d.mkdir(parents=True)
        (d / "no_fm.md").write_text(
            "This is a note without frontmatter. It has plenty of content here.\n" * 3,
            encoding="utf-8",
        )
        status, lines = mpd.check_vault_notes(tmp_path)
        assert status == Status.WARN
        assert any("frontmatter" in l.lower() for l in lines)


class TestCheckMemoryDensity:
    def test_no_memory_dir_passes(self, tmp_path):
        status, _ = mpd.check_memory_density(tmp_path)
        assert status == Status.PASS

    def test_within_limit_passes(self, tmp_path):
        d = tmp_path / "memory"
        d.mkdir()
        data = {f"k{i}": f"v{i}" for i in range(10)}
        (d / "l1.json").write_text(json.dumps(data), encoding="utf-8")
        status, _ = mpd.check_memory_density(tmp_path)
        assert status == Status.PASS

    def test_exceeds_limit_warns(self, tmp_path):
        d = tmp_path / "memory"
        d.mkdir()
        data = {f"k{i}": f"v{i}" for i in range(600)}
        (d / "l1.json").write_text(json.dumps(data), encoding="utf-8")
        status, lines = mpd.check_memory_density(tmp_path)
        assert status == Status.WARN
        assert any("600" in l for l in lines)


class TestRunCheck:
    def test_clean_passes(self, tmp_path):
        with patch("scripts.passive.memory_pollution_detector.write_report"):
            status = mpd.run_check(root=tmp_path)
        assert status == Status.PASS

    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        with patch("scripts.passive.common.REPORTS", reports):
            mpd.run_check(root=tmp_path, reports=reports)
        assert (reports / "memory_pollution_detector.md").exists()

    def test_strict_escalates(self, tmp_path):
        d = tmp_path / "vault" / "notes"
        d.mkdir(parents=True)
        (d / "no_fm.md").write_text(
            "No frontmatter here at all, but enough content to not be empty.\n" * 3,
            encoding="utf-8",
        )
        with patch("scripts.passive.memory_pollution_detector.write_report"):
            normal = mpd.run_check(strict=False, root=tmp_path)
            strict = mpd.run_check(strict=True, root=tmp_path)
        assert normal == Status.WARN
        assert strict == Status.FAIL
