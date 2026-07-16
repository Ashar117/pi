"""Tests for T-209: behavioral eval harness."""
import json
import os
import sys
from pathlib import Path

import pytest

import importlib.util as _ilu

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Load evals/runner.py by file path — avoids bare-name import ambiguity
_spec = _ilu.spec_from_file_location(
    "evals_runner",
    os.path.join(_ROOT, "testing", "evals", "runner.py"),
)
_runner = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_runner)  # type: ignore[union-attr]

run_check = _runner.run_check
score_scenario = _runner.score_scenario
load_scenarios = _runner.load_scenarios
run_offline = _runner.run_offline
SCENARIOS_PATH = _runner.SCENARIOS_PATH


# ── run_check ─────────────────────────────────────────────────────────────────

def test_must_contain_pass():
    ok, msg = run_check({"type": "must_contain", "value": "Atlanta"}, "I live in Atlanta", [])
    assert ok is True
    assert msg == ""


def test_must_contain_fail():
    ok, msg = run_check({"type": "must_contain", "value": "Atlanta"}, "I live in Dallas", [])
    assert ok is False
    assert "Atlanta" in msg


def test_must_not_contain_pass():
    ok, msg = run_check({"type": "must_not_contain", "value": "I've stored"}, "Ok, noted", [])
    assert ok is True


def test_must_not_contain_fail():
    ok, msg = run_check({"type": "must_not_contain", "value": "I've stored"}, "I've stored that.", [])
    assert ok is False


def test_must_call_tool_pass():
    ok, msg = run_check({"type": "must_call_tool", "value": "memory_read"}, "", ["memory_read"])
    assert ok is True


def test_must_call_tool_fail():
    ok, msg = run_check({"type": "must_call_tool", "value": "memory_read"}, "", [])
    assert ok is False


def test_must_not_call_tool_pass():
    ok, msg = run_check({"type": "must_not_call_tool", "value": "gmail_send"}, "", [])
    assert ok is True


def test_must_not_call_tool_fail():
    ok, msg = run_check({"type": "must_not_call_tool", "value": "gmail_send"}, "", ["gmail_send"])
    assert ok is False


def test_unknown_check_type_skipped():
    ok, msg = run_check({"type": "mystery_check"}, "anything", [])
    assert ok is True
    assert "SKIP" in msg


# ── score_scenario ────────────────────────────────────────────────────────────

def test_score_all_pass():
    scenario = {
        "id": "T-test",
        "checks": [
            {"type": "must_contain", "value": "yes"},
            {"type": "must_not_contain", "value": "no"},
        ]
    }
    score = score_scenario(scenario, "yes this works", [])
    assert score["passed"] == 2
    assert score["failed"] == 0
    assert score["failures"] == []


def test_score_one_fail():
    scenario = {
        "id": "T-test",
        "checks": [
            {"type": "must_contain", "value": "Atlanta"},
            {"type": "must_contain", "value": "Dallas"},
        ]
    }
    score = score_scenario(scenario, "I live in Atlanta", [])
    assert score["passed"] == 1
    assert score["failed"] == 1
    assert len(score["failures"]) == 1


def test_score_empty_checks():
    scenario = {"id": "empty", "checks": []}
    score = score_scenario(scenario, "anything", [])
    assert score["checks"] == 0
    assert score["passed"] == 0


# ── load_scenarios ────────────────────────────────────────────────────────────

def test_load_scenarios_returns_list():
    scenarios = load_scenarios()
    assert isinstance(scenarios, list)


def test_load_scenarios_has_10_seeds():
    scenarios = load_scenarios()
    assert len(scenarios) == 10


def test_scenarios_have_required_fields():
    scenarios = load_scenarios()
    for s in scenarios:
        assert "id" in s
        assert "title" in s
        assert "checks" in s


def test_scenarios_from_real_failures():
    """Each scenario should cite a real source_bug."""
    scenarios = load_scenarios()
    for s in scenarios:
        assert s.get("source_bug", "") != "", f"Scenario {s['id']} missing source_bug"


# ── run_offline ───────────────────────────────────────────────────────────────

def test_run_offline_empty_checks_skips():
    scenario = {"id": "EVAL-008", "title": "no checks", "checks": [], "turns": []}
    result = run_offline(scenario)
    assert result["checks"] == 0
    assert result["failed"] == 0


def test_run_offline_mime_check_passes_on_clean_response():
    scenario = {
        "id": "EVAL-001",
        "title": "no miming",
        "checks": [{"type": "must_not_contain", "value": "I've stored"}],
        "turns": [{"role": "user", "content": "remember this",
                   "provider_fixture": "I'll note that for now."}],
    }
    result = run_offline(scenario)
    assert result["failed"] == 0


def test_run_offline_detects_mime():
    scenario = {
        "id": "EVAL-mime",
        "title": "detects mime",
        "checks": [{"type": "must_not_contain", "value": "I've stored"}],
        "turns": [{"role": "user", "content": "remember this",
                   "provider_fixture": "I've stored that to memory!"}],
    }
    result = run_offline(scenario)
    assert result["failed"] == 1
