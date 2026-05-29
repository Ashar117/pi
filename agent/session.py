"""Session lifecycle helpers — summary generation (Groq) and exit handler.

T-085 (R4) — Resumable exit state machine
=========================================

`on_exit` writes step-by-step progress to `data/session_exit_state.json` so a
SIGKILL or daemon crash mid-exit can be resumed on next startup. The file is
rewritten atomically (write to `.tmp`, rename) on every step transition.

Schema::

    {
      "session_id":   "8-char hex",
      "started_at":   "2026-05-17T01:30:00+00:00",
      "completed_at": null | "ISO-8601",   # set by finalize step; nullable
      "steps": [
        {
          "name":         "flush_logs",
          "status":       "pending" | "in_progress" | "completed" | "failed",
          "started_at":   null | "ISO-8601",
          "completed_at": null | "ISO-8601",
          "error":        null | "str"
        },
        ...
      ]
    }

Resume contract: any step left in `pending` or `in_progress` is re-run by
`resume_exit_if_needed()` on next daemon startup, before `server.listen()`.
Each step is independently idempotent — see ADR-005 §Decision for the
per-step justification.

When the final `finalize` step runs, `completed_at` is set at the top level
and the file becomes a no-op signal for next startup.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.truncation import extract_text_from_messages
from memory.pipeline import distill_session
from tools.tools_obsidian import sync_vault


# ── T-085 R4: resumable exit state machine ───────────────────────────────────

_EXIT_STATE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "session_exit_state.json"
)

# T-085 R4 step 9: on_exit trimmed to 3 ops. The other 5 moved out:
#   distill_l1_l2  → mid-session (T-072 already covered this every 10 turns)
#   promote_l2_l3  → mid-session (in _maybe_mid_session_distill, ADR-005 step 5)
#   vault_sync     → mid-session (in _maybe_mid_session_distill, ADR-005 step 6)
#   prune_l3_expired + prune_l2_stale → daily cron (PiScheduler._memory_prune_job,
#                                       backed by scripts/passive/memory_prune.py)
#   weekly_audit   → weekly cron (PiScheduler._weekly_audit_job,
#                                       backed by scripts/passive/weekly_memory_audit.py)
#
# Legacy state files from before R4 step 9 may list those names; the resume
# loop tolerates unknown step names by marking them completed (so they don't
# block the finalize step). The underlying work each one did is now scheduled
# elsewhere and will re-run within hours/days at worst — and each is idempotent.
EXIT_STEPS: tuple = (
    "flush_logs",
    "session_summary",
    "retention_tick",
    "caretaker_full",
    "finalize",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON atomically: tmp + rename. Never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use NamedTemporaryFile in the same dir so os.replace is atomic across volumes
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent),
        prefix=".session_exit_state.", suffix=".tmp", delete=False
    ) as f:
        json.dump(payload, f, indent=2)
        tmp_path = f.name
    os.replace(tmp_path, str(path))


@dataclass
class _ExitStep:
    name: str
    status: str = "pending"  # pending | in_progress | completed | failed
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


@dataclass
class _ExitState:
    """Resumable exit state — see module docstring for schema.

    Atomic file writes mean the state on disk is always either the prior
    consistent snapshot or the new one — never a half-written truncation.
    """
    session_id: str
    started_at: str = field(default_factory=_now)
    completed_at: Optional[str] = None
    steps: List[_ExitStep] = field(default_factory=list)
    path: Path = _EXIT_STATE_PATH

    @classmethod
    def fresh(cls, session_id: str) -> "_ExitState":
        st = cls(session_id=session_id)
        st.steps = [_ExitStep(name=n) for n in EXIT_STEPS]
        st._persist()
        return st

    @classmethod
    def load_pending(cls, path: Path = _EXIT_STATE_PATH) -> Optional["_ExitState"]:
        """Read the state file. Returns None when nothing to resume (file
        missing, parse error, or completed_at already set). Returns an
        _ExitState with the pending/in_progress steps populated otherwise."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            # Corrupted file is fail-open: log + skip resume. Worst case is
            # one session's exit ops aren't resumed — same as pre-R4 behavior.
            print(f"[Daemon] WARNING: {path} unreadable; skipping resume")
            return None
        if data.get("completed_at"):
            return None
        steps = [_ExitStep(**s) for s in data.get("steps", [])]
        if not any(s.status in ("pending", "in_progress") for s in steps):
            return None
        st = cls(
            session_id=data.get("session_id", ""),
            started_at=data.get("started_at", _now()),
            steps=steps,
            path=path,
        )
        return st

    def _persist(self) -> None:
        payload = {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "steps": [s.to_dict() for s in self.steps],
        }
        try:
            _atomic_write_json(self.path, payload)
        except Exception as e:
            # Non-fatal: state file is for crash recovery; failing to write
            # shouldn't abort the actual exit work.
            print(f"[Pi] exit-state write failed (non-fatal): {e}")

    def _step(self, name: str) -> Optional[_ExitStep]:
        return next((s for s in self.steps if s.name == name), None)

    def start(self, name: str) -> None:
        s = self._step(name)
        if s is None:
            return
        s.status = "in_progress"
        s.started_at = _now()
        s.error = None
        self._persist()

    def complete(self, name: str) -> None:
        s = self._step(name)
        if s is None:
            return
        s.status = "completed"
        s.completed_at = _now()
        self._persist()

    def fail(self, name: str, err: str) -> None:
        s = self._step(name)
        if s is None:
            return
        s.status = "failed"
        s.completed_at = _now()
        s.error = err[:500]
        self._persist()

    def finalize(self) -> None:
        """Mark the whole state as complete. After this runs, load_pending
        returns None on next startup — there is nothing to resume."""
        self.completed_at = _now()
        self._persist()

    def pending_steps(self) -> List[str]:
        """Step names that still need to run (pending or interrupted in_progress)."""
        return [s.name for s in self.steps if s.status in ("pending", "in_progress")]


def generate_session_summary(
    groq_client,
    messages: List[Dict],
    history: List[Dict],
    n: int = 12,
) -> str:
    """Summarize the session via Groq. Falls back to history if messages empty."""
    try:
        context = extract_text_from_messages(messages, n=n)

        # Fallback: use string-only history if messages gave nothing
        if not context and history:
            lines = []
            for h in history[-n:]:
                lines.append(f"{h['role']}: {str(h.get('content', ''))[:300]}")
            context = "\n".join(lines)

        if not context:
            return ""

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    "Summarize this conversation in 2-3 sentences for future "
                    f"reference:\n\n{context}"
                ),
            }],
            max_tokens=150,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[Pi] Summary generation failed: {e}")
        return ""


def on_exit(agent) -> None:
    """Handle the EXIT command: write session summary, run distill/prune/sync.

    T-085 R4: each step transitions a row in `_ExitState` so a SIGKILL or
    daemon crash mid-exit can be resumed by `resume_exit_if_needed()` on
    next startup. Step bodies unchanged in this commit (scaffolding-only);
    the move-to-mid-session and move-to-cron migrations happen in later
    commits per ADR-005's migration plan.
    """
    print("[Pi] Shutting down...")
    state = _ExitState.fresh(agent.session_id)
    _run_exit_steps(agent, state)
    state.finalize()

    recent = agent.evolution.get_recent_interactions(hours=24)
    total_cost = sum(i.get("cost", 0) for i in recent)
    if total_cost > 0:
        print(f"[24h Cost: ${total_cost:.4f}]")


def _do_flush_logs(agent) -> None:
    drained = agent.flush_logs(timeout=5.0)
    if not drained:
        print("[Pi] WARNING: log queue did not drain cleanly within 5s")


def _do_session_summary(agent) -> None:
    if not (agent.messages or agent.history):
        return
    summary = agent._generate_session_summary()
    if not summary:
        return
    agent.memory.memory_write(
        content=(
            f"Session summary ("
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}): "
            f"{summary}"
        ),
        tier="l3",
        importance=4,
        category="session_history",
        session_id=agent.session_id,
    )
    print("[Memory] Session summary saved")


# Dispatch table for exit-step bodies. Adding a new step = one entry here +
# one entry in EXIT_STEPS. Step names not in the dispatch (e.g. legacy
# steps from a pre-R4 state file) are tolerated: the resume loop marks
# them completed and moves on. Their original work has been moved
# elsewhere (mid-session or cron) and will re-run on schedule.
def _do_retention_tick(agent) -> None:
    """T-112: Run retention policies at session exit with a 5-second budget.

    Spawns a daemon thread so a hung policy cannot block the exit indefinitely.
    On timeout, logs via track_silent and continues — next cron tick will retry.
    """
    import threading
    from agent.retention import run_all, DEFAULT_POLICIES
    from agent.observability import track_silent

    budget_s = 5.0
    done = threading.Event()

    def _run():
        try:
            run_all(DEFAULT_POLICIES)
        except Exception as e:
            track_silent("retention.session_exit_error", e)
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True, name="retention_tick")
    t.start()
    finished = done.wait(timeout=budget_s)
    if not finished:
        track_silent(
            "retention.session_exit_timeout",
            TimeoutError(f"retention_tick exceeded {budget_s}s budget"),
        )


def _do_caretaker_full(agent) -> None:
    """T-125b: Run caretaker.full() at session exit with a 10-second budget.

    Daemon-thread bounded so a slow embedding pass cannot block exit. Failure
    is recorded via track_silent; next session-exit or daily cron will retry.
    """
    import threading
    from pathlib import Path as _Path
    from agent.observability import track_silent

    budget_s = 10.0
    done = threading.Event()

    def _run():
        try:
            from agent.caretaker import full as _full
            if agent is not None and hasattr(agent, "memory"):
                db_path = _Path(agent.memory.sqlite_path)
                _full(db_path)
        except Exception as e:
            track_silent("caretaker.session_exit_error", e)
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True, name="caretaker_full")
    t.start()
    finished = done.wait(timeout=budget_s)
    if not finished:
        track_silent(
            "caretaker.session_exit_timeout",
            TimeoutError(f"caretaker_full exceeded {budget_s}s budget"),
        )


_EXIT_STEP_BODIES = {
    "flush_logs":      _do_flush_logs,
    "session_summary": _do_session_summary,
    "retention_tick":  _do_retention_tick,
    "caretaker_full":  _do_caretaker_full,
    "finalize":        lambda _agent: None,  # state.finalize() is called by caller
}


def _run_exit_steps(agent, state: "_ExitState") -> None:
    """Run each exit step in canonical order, recording status in `state`.

    Tolerant of unknown step names (legacy state files from before the
    R4 step 9 trim): treats them as no-ops and marks completed, so they
    don't block the finalize step. Each step body is wrapped — a failure
    in one doesn't abort the others (matches the pre-R4 'non-fatal' contract).
    """
    for step_name in EXIT_STEPS:
        s = state._step(step_name)
        if s is None or s.status == "completed":
            continue
        body = _EXIT_STEP_BODIES.get(step_name)
        if body is None:
            # Legacy step from older state file — work moved elsewhere; mark done.
            state.complete(step_name)
            continue
        state.start(step_name)
        try:
            body(agent)
            state.complete(step_name)
        except Exception as e:
            print(f"[Pi] {step_name} failed (non-fatal): {e}")
            state.fail(step_name, str(e))

    # Tolerance pass: any LEGACY step from an old state file that's still
    # not in EXIT_STEPS but exists on disk needs to be marked completed
    # too, otherwise load_pending() will keep returning it as pending
    # forever. Iterates over state.steps directly (not EXIT_STEPS).
    for step in state.steps:
        if step.name in EXIT_STEPS:
            continue
        if step.status in ("pending", "in_progress"):
            state.complete(step.name)


def resume_exit_if_needed(agent) -> bool:
    """If `data/session_exit_state.json` has pending/in_progress steps from
    a prior session, finish them now. Returns True if a resume happened.

    Called from `pi_daemon._get_agent()` after PiAgent init but before
    `server.listen()` so the daemon never accepts the first connection
    while memory state is mid-write from a crashed prior session.

    Safe to call on a clean startup — `_ExitState.load_pending` returns
    None when there is nothing to do.
    """
    state = _ExitState.load_pending()
    if state is None:
        return False
    pending = state.pending_steps()
    print(f"[Daemon] Resuming exit for session {state.session_id} — "
          f"{len(pending)} step(s) left: {pending}")
    _run_exit_steps(agent, state)
    state.finalize()
    print("[Daemon] Exit resume complete.")
    return True
