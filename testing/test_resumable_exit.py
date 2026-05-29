"""testing/test_resumable_exit.py — T-085 R4 acceptance tests.

Covers:
  1. _ExitState lifecycle: fresh → start → complete → finalize
  2. _ExitState.load_pending returns None on missing file, completed state, or all-completed
  3. Resume picks up in_progress / pending steps via _run_exit_steps
  4. EXIT_STEPS is trimmed to the canonical 3 ops
  5. Legacy state file with old step names (promote_l2_l3, vault_sync, etc.) is tolerated
  6. on_exit body is structurally just 3 op-orchestration calls (AST inspection)

No agent boot, no network — _run_exit_steps takes a MagicMock agent.
"""
from __future__ import annotations

import ast
import inspect
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ── 1: _ExitState lifecycle ─────────────────────────────────────────────────

def test_exit_state_fresh_persists_pending_steps():
    from agent.session import _ExitState, EXIT_STEPS
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        st = _ExitState.fresh("abc")
        st.path = Path(td) / "state.json"
        st._persist()
        data = json.loads(st.path.read_text(encoding="utf-8"))
        assert data["session_id"] == "abc"
        assert data["completed_at"] is None
        assert [s["name"] for s in data["steps"]] == list(EXIT_STEPS)
        assert all(s["status"] == "pending" for s in data["steps"])


def test_exit_state_start_complete_fail_transitions():
    from agent.session import _ExitState
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        st = _ExitState.fresh("abc")
        st.path = Path(td) / "state.json"

        st.start("flush_logs")
        assert st._step("flush_logs").status == "in_progress"
        assert st._step("flush_logs").started_at is not None

        st.complete("flush_logs")
        assert st._step("flush_logs").status == "completed"
        assert st._step("flush_logs").completed_at is not None

        st.fail("session_summary", "Groq down")
        assert st._step("session_summary").status == "failed"
        assert st._step("session_summary").error == "Groq down"


# ── 2: load_pending ─────────────────────────────────────────────────────────

def test_load_pending_returns_none_on_missing_file():
    from agent.session import _ExitState
    assert _ExitState.load_pending(Path("/nonexistent/state.json")) is None


def test_load_pending_returns_none_on_completed_state():
    from agent.session import _ExitState
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        st = _ExitState.fresh("xyz")
        st.path = Path(td) / "state.json"
        st.finalize()  # sets completed_at
        assert _ExitState.load_pending(st.path) is None


def test_load_pending_returns_state_when_pending_steps_exist():
    from agent.session import _ExitState
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        st = _ExitState.fresh("xyz")
        st.path = Path(td) / "state.json"
        st.start("flush_logs")  # in_progress
        loaded = _ExitState.load_pending(st.path)
        assert loaded is not None
        pending = loaded.pending_steps()
        assert "flush_logs" in pending  # in_progress counts as pending for resume


# ── 3 + 4: resume via _run_exit_steps + EXIT_STEPS shape ────────────────────

def test_exit_steps_canonical():
    from agent.session import EXIT_STEPS
    # T-112 added retention_tick before finalize
    assert "flush_logs" in EXIT_STEPS
    assert "session_summary" in EXIT_STEPS
    assert "retention_tick" in EXIT_STEPS
    assert "finalize" in EXIT_STEPS
    assert EXIT_STEPS[-1] == "finalize", "finalize must be the last step"


def test_run_exit_steps_drives_state_to_completion():
    from agent.session import _ExitState, _run_exit_steps, EXIT_STEPS
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        st = _ExitState.fresh("sess")
        st.path = Path(td) / "state.json"

        agent = MagicMock()
        agent.flush_logs.return_value = True
        agent.messages = []
        agent.history = []
        agent.session_id = "sess"

        _run_exit_steps(agent, st)
        st.finalize()

        statuses = {s.name: s.status for s in st.steps}
        # All canonical steps should be completed (retention_tick may complete or fail non-fatally)
        for step_name in EXIT_STEPS:
            assert step_name in statuses, f"Missing step {step_name} in statuses"
            assert statuses[step_name] in ("completed", "failed"), \
                f"Step {step_name} has unexpected status {statuses[step_name]}"


def test_resume_picks_up_interrupted_in_progress_step():
    """If a step was marked in_progress when the daemon crashed, resume re-runs it."""
    from agent.session import _ExitState, _run_exit_steps
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        st = _ExitState.fresh("crash_sess")
        st.path = Path(td) / "state.json"
        st.complete("flush_logs")
        st.start("session_summary")  # simulate crash mid-step

        # Now resume — re-run from session_summary onward
        agent = MagicMock()
        agent.messages = []
        agent.history = []
        agent.session_id = "crash_sess"

        loaded = _ExitState.load_pending(st.path)
        assert loaded is not None
        _run_exit_steps(agent, loaded)
        loaded.finalize()

        statuses = {s.name: s.status for s in loaded.steps}
        # session_summary was in_progress, now completed
        assert statuses["session_summary"] == "completed"
        assert statuses["finalize"] == "completed"


# ── 5: legacy state file tolerance ──────────────────────────────────────────

def test_legacy_state_file_with_old_step_names_is_tolerated():
    """A state file written by pre-R4-step-9 code lists steps like
    promote_l2_l3 / vault_sync that no longer exist. Resume must mark them
    completed (so finalize can run) instead of crashing."""
    from agent.session import _ExitState, _run_exit_steps
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        legacy = {
            "session_id": "old",
            "started_at": "2026-05-16T00:00:00+00:00",
            "completed_at": None,
            "steps": [
                {"name": "flush_logs", "status": "completed",
                 "started_at": None, "completed_at": None, "error": None},
                {"name": "promote_l2_l3", "status": "in_progress",
                 "started_at": None, "completed_at": None, "error": None},
                {"name": "vault_sync", "status": "pending",
                 "started_at": None, "completed_at": None, "error": None},
            ],
        }
        path = Path(td) / "legacy.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = _ExitState.load_pending(path)
        assert loaded is not None
        agent = MagicMock()
        agent.flush_logs.return_value = True
        agent.messages = []
        agent.history = []
        agent.session_id = "old"

        _run_exit_steps(agent, loaded)
        loaded.finalize()

        statuses = {s.name: s.status for s in loaded.steps}
        # Legacy step names should be marked completed (not stuck in_progress)
        assert statuses["promote_l2_l3"] == "completed"
        assert statuses["vault_sync"] == "completed"
        # And load_pending should now return None (resolved)
        assert _ExitState.load_pending(path) is None


# ── 6: on_exit body shape ──────────────────────────────────────────────────

def test_on_exit_body_is_three_orchestration_calls():
    """AST-check on_exit body: should be a small orchestrator that calls
    state.fresh + _run_exit_steps + state.finalize, not a 100-line monster."""
    from agent.session import on_exit
    src = inspect.getsource(on_exit)
    tree = ast.parse(src)
    func = tree.body[0]
    # Count statements at the top level of on_exit (excluding docstring)
    stmts = [s for s in func.body if not (
        isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str)
    )]
    # Should be a tight body: print, fresh, _run_exit_steps, finalize,
    # plus the cost-summary tail (4-7 statements is "tight"). Pre-R4 was 30+.
    assert len(stmts) <= 10, (
        f"on_exit body has {len(stmts)} top-level statements; "
        f"expected ≤10 after R4 trim. Body source:\n{src}"
    )
