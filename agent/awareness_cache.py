"""T-173: Awareness cache — extracted from PiAgent (T-041 / T-067 / T-175).

Encapsulates the six _awareness_* attributes and the TTL + background-refresh
logic that previously lived inside the PiAgent class.  PiAgent's public surface
is unchanged: agent.awareness_snapshot still works as a property.
"""
from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from typing import Optional


class AwarenessCache:
    """TTL-backed cache for the awareness snapshot with lazy first-load and
    background refresh so turns are never blocked on weather/news/stocks APIs.
    """

    DEFAULT_TTL = 1500  # 25 min (T-067: stays below the 30-min API TTL)

    def __init__(self, awareness_tools, *, ttl: int = DEFAULT_TTL) -> None:
        self._tools = awareness_tools
        self._ttl = ttl
        self._cache: Optional[str] = None
        self._last_refresh: Optional[datetime] = None
        self._refreshing = False
        self._lock = threading.Lock()
        self._failures = 0  # consecutive failure count for telemetry

        if "--eager-awareness" in sys.argv:
            self._cache = self._tools.get_awareness_snapshot()
            self._last_refresh = datetime.now(timezone.utc)

    @property
    def snapshot(self) -> str:
        """Return the current snapshot, triggering a background refresh when stale."""
        from agent.observability import track_silent as _track_silent

        now = datetime.now(timezone.utc)

        if self._cache is None:
            # First-ever load — synchronous, unavoidable (guarded by T-175).
            try:
                self._cache = self._tools.get_awareness_snapshot()
                self._last_refresh = now
            except Exception as e:
                self._failures += 1
                _track_silent("awareness.first_load", e)
                self._cache = ""
            return self._cache

        age_s = (now - self._last_refresh).total_seconds() if self._last_refresh else 9999
        if age_s >= self._ttl:
            def _refresh():
                try:
                    new_snap = self._tools.get_awareness_snapshot(force=True)
                    self._cache = new_snap
                    self._last_refresh = datetime.now(timezone.utc)
                    self._failures = 0
                except Exception as e:
                    self._failures += 1
                    if self._failures in (1, 3, 10):
                        print(
                            f"[Pi] awareness bg refresh failed ({self._failures}x): {e}",
                            file=sys.stderr, flush=True,
                        )
                finally:
                    self._refreshing = False

            with self._lock:
                if not self._refreshing:
                    self._refreshing = True
                    threading.Thread(target=_refresh, daemon=True).start()

        return self._cache

    @snapshot.setter
    def snapshot(self, value: str) -> None:
        """Allow tools (refresh_awareness) to overwrite the cache."""
        self._cache = value
        self._last_refresh = datetime.now(timezone.utc)
