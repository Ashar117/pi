"""testing/test_autonomy_loop_watcher.py — Tests for SKILL 7."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import autonomy_loop_watcher as alw
from scripts.passive.common import Status

NOW = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


# ── check_sprint_activity ─────────────────────────────────────────────────────

class TestCheckSprintActivity:
    def _log(self, tmp_path, name="sprint.jsonl", records=None):
        d = tmp_path / "logs" / "sprint"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        lines = [json.dumps(r) for r in (records or [])]
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def test_no_logs_dir_warns(self, tmp_path):
        status, lines = alw.check_sprint_activity(tmp_path)
        assert status == Status.WARN
        assert any("logs/sprint" in l for l in lines)

    def test_no_log_files_warns(self, tmp_path):
        (tmp_path / "logs" / "sprint").mkdir(parents=True)
        status, lines = alw.check_sprint_activity(tmp_path)
        assert status == Status.WARN

    def test_recent_log_passes(self, tmp_path):
        p = self._log(tmp_path, records=[{"outcome": "closed"}])
        # mtime is just now — 0 days ago
        status, lines = alw.check_sprint_activity(tmp_path, idle_days=14)
        assert status == Status.PASS
        assert any("day" in l for l in lines)

    def test_high_escalation_warns(self, tmp_path):
        records = [{"outcome": "escalated"}] * 6 + [{"outcome": "closed"}] * 4
        self._log(tmp_path, records=records)
        status, lines = alw.check_sprint_activity(tmp_path, escalation_threshold=0.50)
        assert status == Status.WARN
        assert any("60%" in l or "escalation" in l.lower() for l in lines)

    def test_low_escalation_passes(self, tmp_path):
        records = [{"outcome": "closed"}] * 9 + [{"outcome": "escalated"}]
        self._log(tmp_path, records=records)
        status, lines = alw.check_sprint_activity(tmp_path, escalation_threshold=0.50)
        assert any("10%" in l or "ok" in l.lower() for l in lines)


# ── check_plan_sprint_cadence ─────────────────────────────────────────────────

class TestCheckPlanSprintCadence:
    def _pi_md(self, tmp_path, week_of="2026-05-11"):
        p = tmp_path / "PI.md"
        p.write_text(
            f"## NOW\n\n**Week of:** {week_of} -> 2026-05-17\n",
            encoding="utf-8",
        )
        return p

    def _sprint_note(self, tmp_path, week="2026-W20"):
        d = tmp_path / "vault" / "notes" / "sprints"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{week}.md").write_text("# Sprint", encoding="utf-8")

    def test_current_week_and_note_passes(self, tmp_path):
        week = NOW.strftime("%G-W%V")
        self._pi_md(tmp_path, NOW.strftime("%Y-%m-%d"))
        self._sprint_note(tmp_path, week)
        status, _ = alw.check_plan_sprint_cadence(tmp_path, now=NOW)
        assert status == Status.PASS

    def test_stale_week_warns(self, tmp_path):
        self._pi_md(tmp_path, "2026-01-01")  # old week
        status, lines = alw.check_plan_sprint_cadence(tmp_path, now=NOW)
        assert status == Status.WARN
        assert any("warn" in l.lower() for l in lines)

    def test_missing_pi_md_warns(self, tmp_path):
        status, lines = alw.check_plan_sprint_cadence(tmp_path, now=NOW)
        assert status == Status.WARN
        assert any("PI.md" in l or "not found" in l for l in lines)

    def test_missing_sprint_note_warns(self, tmp_path):
        self._pi_md(tmp_path, NOW.strftime("%Y-%m-%d"))
        # No sprint note created
        status, lines = alw.check_plan_sprint_cadence(tmp_path, now=NOW)
        assert status == Status.WARN


# ── check_retro_cadence ───────────────────────────────────────────────────────

class TestCheckRetroCadence:
    def test_retro_exists_passes(self, tmp_path):
        last = alw._last_iso_week(NOW)
        d = tmp_path / "vault" / "notes" / "retros"
        d.mkdir(parents=True)
        (d / f"{last}.md").write_text("# Retro", encoding="utf-8")
        status, _ = alw.check_retro_cadence(tmp_path, now=NOW)
        assert status == Status.PASS

    def test_retro_missing_warns(self, tmp_path):
        status, lines = alw.check_retro_cadence(tmp_path, now=NOW)
        assert status == Status.WARN
        assert any("retro" in l.lower() for l in lines)


# ── check_refresh_pi_drift ────────────────────────────────────────────────────

class TestCheckRefreshPiDrift:
    def test_in_sync_passes(self, tmp_path):
        from scripts.passive.common import Status as S
        # patch in the doc_drift_watcher module (imported inside the function)
        with patch("scripts.passive.doc_drift_watcher.check_open_tickets",
                   return_value=(S.PASS, ["[ok]"])), \
             patch("scripts.passive.doc_drift_watcher.check_closed_tickets",
                   return_value=(S.PASS, ["[ok]"])), \
             patch("scripts.passive.doc_drift_watcher.check_solution_count",
                   return_value=(S.PASS, ["[ok]"])), \
             patch("scripts.passive.doc_drift_watcher.check_verify_status",
                   return_value=(S.PASS, ["[ok]"])):
            status, lines = alw.check_refresh_pi_drift(tmp_path)
        assert status == Status.PASS

    def test_drift_warns(self, tmp_path):
        from scripts.passive.common import Status as S
        with patch("scripts.passive.doc_drift_watcher.check_open_tickets",
                   return_value=(S.WARN, ["[warn] drift detected"])), \
             patch("scripts.passive.doc_drift_watcher.check_closed_tickets",
                   return_value=(S.PASS, ["[ok]"])), \
             patch("scripts.passive.doc_drift_watcher.check_solution_count",
                   return_value=(S.PASS, ["[ok]"])), \
             patch("scripts.passive.doc_drift_watcher.check_verify_status",
                   return_value=(S.PASS, ["[ok]"])):
            status, lines = alw.check_refresh_pi_drift(tmp_path)
        assert status == Status.WARN


# ── integration ───────────────────────────────────────────────────────────────

class TestRunCheck:
    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        with patch("scripts.passive.common.REPORTS", reports):
            alw.run_check(root=tmp_path, reports=reports)
        assert (reports / "autonomy_loop_watcher.md").exists()

    def test_strict_escalates(self, tmp_path):
        with patch("scripts.passive.autonomy_loop_watcher.write_report"):
            normal = alw.run_check(strict=False, root=tmp_path)
            strict = alw.run_check(strict=True, root=tmp_path)
        assert normal == Status.WARN
        assert strict == Status.FAIL
