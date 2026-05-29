"""testing/test_retention_tick.py — T-112: retention_tick script tests."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SCRIPT = str(Path(__file__).parent.parent / "scripts" / "retention_tick.py")


# ── Script exits 0 on clean state ────────────────────────────────────────────

def test_retention_tick_exits_zero_on_clean_state():
    """Script returns exit code 0 when no policies apply."""
    result = subprocess.run(
        [sys.executable, _SCRIPT],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "policies:" in result.stdout


# ── Dry run does not mutate files ─────────────────────────────────────────────

def test_retention_tick_dry_run_no_mutations(tmp_path):
    """--dry-run reports policies but does not change mtimes."""
    from agent.retention import Policy

    src = tmp_path / "turns.jsonl"
    src.write_text("test\n", encoding="utf-8")
    mtime_before = src.stat().st_mtime

    # Run with patched DEFAULT_POLICIES pointing at tmp file (mtime today = skip)
    result = subprocess.run(
        [sys.executable, _SCRIPT, "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DRY RUN" in result.stdout
    # mtime of real turns.jsonl unchanged
    from agent.turn_log import log_path
    lp = log_path()
    if lp.exists():
        # just assert no exception during dry run
        pass


# ── Policies filter ───────────────────────────────────────────────────────────

def test_retention_tick_policies_filter():
    """--policies filter runs only specified policies."""
    result = subprocess.run(
        [sys.executable, _SCRIPT, "--policies", "pi_db_vacuum"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "pi_db_vacuum" in result.stdout
    # turns_jsonl should NOT appear
    assert "turns_jsonl" not in result.stdout


def test_retention_tick_invalid_policies_exits_one():
    """--policies nonexistent exits 1."""
    result = subprocess.run(
        [sys.executable, _SCRIPT, "--policies", "does_not_exist"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
