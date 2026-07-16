"""testing/test_daemon_staleness_checker.py — T-284."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.passive import daemon_staleness_checker as dsc
from scripts.passive.common import Status


def _write_info(root: Path, started_at: datetime, git_rev=None):
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "daemon_info.json").write_text(
        json.dumps({"started_at": started_at.isoformat(), "git_rev": git_rev, "dirty_file_count": 0}),
        encoding="utf-8",
    )


def test_missing_info_file_warns(tmp_path):
    status, lines = dsc.check_staleness(tmp_path)
    assert status == Status.WARN
    assert "missing" in lines[0]


def test_newer_file_than_startup_warns(tmp_path, monkeypatch):
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    _write_info(tmp_path, started)
    monkeypatch.setattr(dsc, "run_git", lambda args: type("R", (), {"stdout": ""})())

    stale_file = tmp_path / "changed.py"
    stale_file.write_text("x = 1\n", encoding="utf-8")

    status, lines = dsc.check_staleness(tmp_path)
    assert status == Status.WARN
    assert any("changed.py" in l for l in lines)


def test_clean_match_passes(tmp_path, monkeypatch):
    started = datetime.now(timezone.utc)
    _write_info(tmp_path, started, git_rev="abc123")
    monkeypatch.setattr(dsc, "run_git", lambda args: type("R", (), {"stdout": "abc123\n"})())

    status, lines = dsc.check_staleness(tmp_path)
    assert status == Status.PASS


def test_drift_over_7_days_fails(tmp_path, monkeypatch):
    started = datetime.now(timezone.utc) - timedelta(days=8)
    _write_info(tmp_path, started, git_rev="old")
    monkeypatch.setattr(dsc, "run_git", lambda args: type("R", (), {"stdout": "new\n"})())

    status, lines = dsc.check_staleness(tmp_path)
    assert status == Status.FAIL
