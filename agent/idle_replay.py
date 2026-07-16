"""agent/idle_replay.py — T-136: idle replay (sleep-consolidation analogue).

After a stretch of user inactivity, replay a few past episodes and look for
cross-session patterns, writing a meta-fact when an entity recurs across
sessions. This is the hippocampal-replay analogue: Pi has the raw episodes
(L1) but only ever consolidated them per-session; replay connects them.

Design (per T-136):
- Daemon thread polls; fires when idle > threshold (default 5 min).
- Hard caps: PI_REPLAY_PER_HOUR (1), PI_REPLAY_PER_DAY (10).
- Halts within one poll interval of user input (notify_activity()).
- Skips when the router's TPD budget is low (< 20%).
- Default OFF (PI_IDLE_REPLAY); high cost-risk per the ticket.
- Privacy (Inv. 4): all memory access goes through injected callables, which
  the caller binds to the correct (namespace-isolated) memory — the manager
  itself never reaches across streams.

The class takes its dependencies as callables so it is pure Python and unit-
testable in isolation with a mocked clock and mocked memory (migration step 2).
"""
from __future__ import annotations

import os
import threading
from typing import Callable, List, Optional, Any, Dict


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "on", "yes")


class IdleReplayManager:
    """Replay past episodes during idle and surface cross-session patterns.

    All collaborators are injected (no hard dependency on PiAgent / MemoryTools),
    so the manager unit-tests cleanly with a fake clock and fake callables:

        fetch_episodes()  -> list of episode dicts (salience-weighted by caller)
        replay_episode(ep) -> None   (re-distills one episode; side-effecting)
        detect_patterns() -> list of pattern dicts (entity recurrence)
        write_meta_fact(pattern) -> None  (writes a pattern_observation to L2)
        tpd_remaining()   -> float in 0..1 (fraction of daily token budget left)
        clock()           -> float (monotonic seconds; injectable for tests)
    """

    def __init__(
        self,
        *,
        fetch_episodes: Callable[[], List[Dict[str, Any]]],
        replay_episode: Callable[[Dict[str, Any]], None],
        detect_patterns: Callable[[], List[Dict[str, Any]]],
        write_meta_fact: Callable[[Dict[str, Any]], None],
        tpd_remaining: Optional[Callable[[], float]] = None,
        clock: Callable[[], float] = None,
        enabled: Optional[bool] = None,
        idle_threshold_s: int = 300,
        poll_interval_s: int = 5,
        episodes_per_replay: int = 3,
        per_hour_cap: Optional[int] = None,
        per_day_cap: Optional[int] = None,
        min_tpd_fraction: float = 0.20,
    ):
        import time as _time
        self._fetch_episodes = fetch_episodes
        self._replay_episode = replay_episode
        self._detect_patterns = detect_patterns
        self._write_meta_fact = write_meta_fact
        self._tpd_remaining = tpd_remaining
        self._clock = clock or _time.monotonic
        self.enabled = _env_on("PI_IDLE_REPLAY") if enabled is None else enabled
        self.idle_threshold_s = idle_threshold_s
        self.poll_interval_s = poll_interval_s
        self.episodes_per_replay = episodes_per_replay
        self.per_hour_cap = _env_int("PI_REPLAY_PER_HOUR", 1) if per_hour_cap is None else per_hour_cap
        self.per_day_cap = _env_int("PI_REPLAY_PER_DAY", 10) if per_day_cap is None else per_day_cap
        self.min_tpd_fraction = min_tpd_fraction

        self._last_activity = self._clock()
        self._replay_times: List[float] = []
        self._stop = threading.Event()
        self._paused = threading.Event()  # set by notify_activity → halts in-flight replay
        self._thread: Optional[threading.Thread] = None

    # ── activity tracking ────────────────────────────────────────────────────

    def notify_activity(self) -> None:
        """Record user activity; halt any in-flight replay within one iteration."""
        self._last_activity = self._clock()
        self._paused.set()

    def _idle_seconds(self) -> float:
        return self._clock() - self._last_activity

    # ── caps ──────────────────────────────────────────────────────────────────

    def _prune_replay_times(self, now: float) -> None:
        day_ago = now - 86400
        self._replay_times = [t for t in self._replay_times if t >= day_ago]

    def _under_caps(self, now: float) -> bool:
        self._prune_replay_times(now)
        last_hour = sum(1 for t in self._replay_times if t >= now - 3600)
        last_day = len(self._replay_times)
        return last_hour < self.per_hour_cap and last_day < self.per_day_cap

    def _budget_ok(self) -> bool:
        if self._tpd_remaining is None:
            return True
        try:
            return self._tpd_remaining() >= self.min_tpd_fraction
        except Exception:
            return True

    def should_replay(self, now: Optional[float] = None) -> bool:
        now = self._clock() if now is None else now
        return (
            self.enabled
            and self._idle_seconds() >= self.idle_threshold_s
            and self._under_caps(now)
            and self._budget_ok()
        )

    # ── one replay cycle ───────────────────────────────────────────────────────

    def run_once(self) -> bool:
        """Run a single replay cycle if conditions allow. Returns True if it ran.

        Never raises — replay is best-effort background work.
        """
        now = self._clock()
        if not self.enabled or self._idle_seconds() < self.idle_threshold_s:
            return False
        if not self._under_caps(now):
            self._track("cap_reached")
            return False
        if not self._budget_ok():
            self._track("tpd_budget_low")
            return False

        self._paused.clear()
        try:
            episodes = self._fetch_episodes() or []
            for ep in episodes[: self.episodes_per_replay]:
                if self._paused.is_set():  # user activity → halt
                    self._track("halted_on_activity")
                    break
                self._replay_episode(ep)

            if not self._paused.is_set():
                for pattern in (self._detect_patterns() or []):
                    self._write_meta_fact(pattern)
        except Exception as e:
            self._track("replay_failed", e)
            return False

        self._replay_times.append(now)
        return True

    # ── daemon lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="idle-replay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        import time as _time
        while not self._stop.is_set():
            try:
                if self.should_replay():
                    self.run_once()
            except Exception as e:
                self._track("loop_error", e)
            self._stop.wait(self.poll_interval_s)

    def _track(self, event: str, exc: Optional[Exception] = None) -> None:
        try:
            from agent.observability import track_silent
            track_silent(f"idle_replay.{event}", exc)
        except Exception:
            pass
