"""agent/retention.py — T-109: unified policy engine for log rotation and DB pruning.

Policies declare intent (what, when, how); run_policy() dispatches to a
kind-specific handler. run_all() executes a list and returns a summary.
State (last_run, last_applied, last_stats) is persisted to
data/retention_state.json under a cross-process filelock so cron and
session-end hooks cannot race each other.

Crash-safety for jsonl_rotate:
    rename → gzip → truncate (all atomic, each step is idempotent)
    If the process dies mid-flow the archive exists and the source is
    still readable — no data loss.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from filelock import FileLock

from agent.observability import track_silent

_ROOT = Path(__file__).parent.parent
_STATE_PATH = _ROOT / "data" / "retention_state.json"
_LOCK_PATH = _ROOT / "data" / "retention_state.lock"
_GLOBAL_LOCK = threading.Lock()  # in-process guard; filelock handles cross-process


@dataclass
class Policy:
    name: str
    path: str | Path
    kind: Literal["jsonl_rotate", "sqlite_table_prune", "log_size_rotate", "sqlite_vacuum", "l3_decay_archive"]
    max_age_days: Optional[int] = None
    max_size_mb: Optional[int] = None
    keep_archives: Optional[int] = None
    archive_dir: str = "logs/archive"
    table: Optional[str] = None
    timestamp_col: Optional[str] = None
    vacuum_after: bool = False
    schedule: Literal["daily", "weekly", "on_demand"] = "daily"

    def resolved_path(self) -> Path:
        p = Path(self.path)
        return p if p.is_absolute() else _ROOT / p

    def resolved_archive_dir(self) -> Path:
        d = Path(self.archive_dir)
        return d if d.is_absolute() else _ROOT / d


# ── State file I/O ────────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ── Schedule guard ────────────────────────────────────────────────────────────

def _due(policy: Policy, state_entry: Dict[str, Any]) -> tuple[bool, str]:
    """Return (True, "") if policy should run now, (False, reason) otherwise."""
    if policy.schedule == "on_demand":
        return True, ""
    last_applied = _parse_iso(state_entry.get("last_applied"))
    if last_applied is None:
        return True, ""
    age = (datetime.now(timezone.utc) - last_applied).total_seconds()
    if policy.schedule == "daily" and age < 86400:
        return False, f"last applied {int(age/3600)}h ago, daily cadence"
    if policy.schedule == "weekly" and age < 7 * 86400:
        return False, f"last applied {int(age/3600)}h ago, weekly cadence"
    return True, ""


# ── Kind handlers ─────────────────────────────────────────────────────────────

def _handle_jsonl_rotate(policy: Policy, dry_run: bool) -> Dict[str, Any]:
    """Rotate a jsonl log file if its mtime crossed UTC midnight."""
    src = policy.resolved_path()
    stats: Dict[str, Any] = {"rotated": False, "archive": None, "archives_pruned": 0}

    if not src.exists():
        return {"applied": False, "reason": f"{src} not found", "stats": stats}

    mtime = datetime.fromtimestamp(src.stat().st_mtime, tz=timezone.utc)
    today_utc = datetime.now(timezone.utc).date()
    if mtime.date() >= today_utc:
        return {"applied": False, "reason": "file mtime is today, no rotation due", "stats": stats}

    archive_dir = policy.resolved_archive_dir()
    date_str = mtime.strftime("%Y-%m-%d")
    raw_name = f"{policy.name}-{date_str}.jsonl"
    gz_name = raw_name + ".gz"
    archive_raw = archive_dir / raw_name
    archive_gz = archive_dir / gz_name

    if dry_run:
        stats["rotated"] = True
        stats["archive"] = str(archive_gz)
        return {"applied": True, "reason": "dry_run", "stats": stats}

    archive_dir.mkdir(parents=True, exist_ok=True)

    # rename → gzip → truncate (crash-safe ordering)
    shutil.copy2(str(src), str(archive_raw))  # copy first; source intact on crash
    with open(str(archive_raw), "rb") as f_in, gzip.open(str(archive_gz), "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    archive_raw.unlink()
    # truncate source (keep the file, clear contents)
    src.open("w").close()

    stats["rotated"] = True
    stats["archive"] = str(archive_gz)

    # prune old archives
    if policy.keep_archives is not None:
        archives = sorted(archive_dir.glob(f"{policy.name}-*.jsonl.gz"))
        to_delete = archives[: max(0, len(archives) - policy.keep_archives)]
        for old in to_delete:
            old.unlink()
        stats["archives_pruned"] = len(to_delete)

    return {"applied": True, "reason": "rotated", "stats": stats}


def _handle_sqlite_table_prune(policy: Policy, dry_run: bool) -> Dict[str, Any]:
    """DELETE rows older than max_age_days from a table; optionally VACUUM."""
    src = policy.resolved_path()
    stats: Dict[str, Any] = {"rows_deleted": 0, "vacuumed": False}

    if not src.exists():
        return {"applied": False, "reason": f"{src} not found", "stats": stats}
    if not policy.table or not policy.timestamp_col or not policy.max_age_days:
        return {"applied": False, "reason": "missing table/timestamp_col/max_age_days", "stats": stats}

    if dry_run:
        # COUNT without deleting
        conn = sqlite3.connect(str(src))
        try:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM {policy.table} "
                f"WHERE {policy.timestamp_col} < datetime('now', '-{policy.max_age_days} days')"
            )
            stats["rows_deleted"] = cur.fetchone()[0]
        finally:
            conn.close()
        return {"applied": True, "reason": "dry_run", "stats": stats}

    conn = sqlite3.connect(str(src))
    try:
        cur = conn.execute(
            f"DELETE FROM {policy.table} "
            f"WHERE {policy.timestamp_col} < datetime('now', '-{policy.max_age_days} days')"
        )
        rows_deleted = cur.rowcount
        conn.commit()
        stats["rows_deleted"] = rows_deleted

        if policy.vacuum_after and rows_deleted > 0:
            conn.execute("VACUUM")
            stats["vacuumed"] = True
    finally:
        conn.close()

    return {"applied": True, "reason": f"deleted {stats['rows_deleted']} rows", "stats": stats}


def _handle_log_size_rotate(policy: Policy, dry_run: bool) -> Dict[str, Any]:
    """Rotate a log file when it exceeds max_size_mb."""
    src = policy.resolved_path()
    stats: Dict[str, Any] = {"rotated": False, "size_mb": 0.0}

    if not src.exists():
        return {"applied": False, "reason": f"{src} not found", "stats": stats}
    if not policy.max_size_mb:
        return {"applied": False, "reason": "max_size_mb not set", "stats": stats}

    size_mb = src.stat().st_size / (1024 * 1024)
    stats["size_mb"] = round(size_mb, 2)

    if size_mb <= policy.max_size_mb:
        return {"applied": False, "reason": f"size {size_mb:.1f} MB <= limit {policy.max_size_mb} MB", "stats": stats}

    if dry_run:
        stats["rotated"] = True
        return {"applied": True, "reason": "dry_run", "stats": stats}

    keep = policy.keep_archives or 5
    # shift: .{keep-1} is deleted, .{n} → .{n+1} for n in keep-2..0, then src → .1
    for i in range(keep - 1, 0, -1):
        old = Path(f"{src}.{i}")
        new = Path(f"{src}.{i + 1}")
        if old.exists():
            old.rename(new)

    backup = Path(f"{src}.1")
    src.rename(backup)
    src.open("w").close()  # recreate empty source
    stats["rotated"] = True
    return {"applied": True, "reason": f"rotated at {size_mb:.1f} MB", "stats": stats}


def _handle_sqlite_vacuum(policy: Policy, dry_run: bool, state_entry: Dict[str, Any]) -> Dict[str, Any]:
    """VACUUM a SQLite database, respecting the schedule window."""
    src = policy.resolved_path()
    stats: Dict[str, Any] = {"vacuumed": False}

    if not src.exists():
        return {"applied": False, "reason": f"{src} not found", "stats": stats}

    # extra schedule check: weekly means skip if last_applied < 7d ago
    is_due, skip_reason = _due(policy, state_entry)
    if not is_due:
        return {"applied": False, "reason": skip_reason, "stats": stats}

    if dry_run:
        stats["vacuumed"] = True
        return {"applied": True, "reason": "dry_run", "stats": stats}

    conn = sqlite3.connect(str(src))
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()

    stats["vacuumed"] = True
    return {"applied": True, "reason": "vacuumed", "stats": stats}


def _handle_l3_decay_archive(policy: Policy, dry_run: bool) -> Dict[str, Any]:
    """Archive L3 rows whose effective_importance dropped below 1.0.

    T-300: default-ON (opt-OUT via PI_DECAY_ARCHIVE=off) — this is Pi's
    "use it or lose it" forgetting: unpinned rows nobody has touched decay
    per-category (memory/salience.py) and archive once effective_importance
    (importance * exp(-decay_rate * days_since_access)) drops below 1.0.
    Conservative by construction: an importance-5 row at the default 0.01/day
    rate needs ~160 untouched days to cross the threshold. Pinned rows and
    already-invalid rows are skipped.

    T-309: 'archive' means moving the row to l3_archive (memory.archive) —
    physically out of l3_cache, so no production read path can ever surface
    it again, not just setting active_until=now in place (which used to get
    hard-deleted by the very next daily prune_l3_expired sweep — the exact
    'archived means deleted tomorrow' bug this ticket fixes).
    """
    if os.environ.get("PI_DECAY_ARCHIVE", "").lower() in ("0", "off", "false", "no"):
        return {"applied": False, "reason": "PI_DECAY_ARCHIVE=off", "stats": {"archived": 0}}

    src = policy.resolved_path()
    stats: Dict[str, Any] = {"archived": 0, "scanned": 0}

    if not src.exists():
        return {"applied": False, "reason": f"{src} not found", "stats": stats}

    try:
        from memory.salience import effective_importance
        from memory.archive import archive_l3_row
    except ImportError as exc:
        return {"applied": False, "reason": f"memory.salience unavailable: {exc}", "stats": stats}

    threshold = 1.0
    now_iso = _now_iso()

    conn = sqlite3.connect(str(src))
    try:
        cur = conn.execute(
            "SELECT id, importance, decay_rate, last_accessed_at, pinned "
            "FROM l3_cache "
            "WHERE invalid_at IS NULL "
            "  AND (active_until IS NULL OR active_until > ?) "
            "  AND (superseded_by IS NULL OR superseded_by = '')",
            [now_iso],
        )
        rows = cur.fetchall()
        stats["scanned"] = len(rows)

        to_archive = [
            row_id
            for row_id, importance, decay_rate, last_accessed_at, pinned in rows
            if not (pinned or 0)
            and effective_importance(importance, decay_rate, last_accessed_at, 0) < threshold
        ]

        if not dry_run:
            for row_id in to_archive:
                archive_l3_row(conn, row_id, "decay", now_iso)
            conn.commit()

        stats["archived"] = len(to_archive)
    finally:
        conn.close()

    return {
        "applied": True,
        "reason": f"archived {stats['archived']} of {stats['scanned']} rows",
        "stats": stats,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def run_policy(policy: Policy, dry_run: bool = False) -> Dict[str, Any]:
    """Run a single retention policy and return a result dict.

    Never raises — failures are caught, reported via track_silent, and
    returned as {'applied': False, 'reason': <str>}.
    """
    t0 = time.monotonic()
    state = _load_state()
    entry = state.get(policy.name, {})

    # schedule guard (sqlite_vacuum has its own check inside handler too)
    if policy.kind != "sqlite_vacuum":
        is_due, skip_reason = _due(policy, entry)
        if not is_due:
            return {
                "name": policy.name,
                "applied": False,
                "reason": skip_reason,
                "stats": {},
                "duration_s": round(time.monotonic() - t0, 3),
            }

    try:
        if policy.kind == "jsonl_rotate":
            result = _handle_jsonl_rotate(policy, dry_run)
        elif policy.kind == "sqlite_table_prune":
            result = _handle_sqlite_table_prune(policy, dry_run)
        elif policy.kind == "log_size_rotate":
            result = _handle_log_size_rotate(policy, dry_run)
        elif policy.kind == "sqlite_vacuum":
            result = _handle_sqlite_vacuum(policy, dry_run, entry)
        elif policy.kind == "l3_decay_archive":
            result = _handle_l3_decay_archive(policy, dry_run)
        else:
            result = {"applied": False, "reason": f"unknown kind: {policy.kind}", "stats": {}}
    except Exception as exc:
        track_silent(f"retention.{policy.name}", exc)
        result = {"applied": False, "reason": str(exc), "stats": {}}

    duration = round(time.monotonic() - t0, 3)
    now_iso = _now_iso()

    # update state (even on skip, record last_run)
    if not dry_run:
        entry["last_run"] = now_iso
        if result.get("applied"):
            entry["last_applied"] = now_iso
            entry["last_stats"] = result.get("stats", {})
        state[policy.name] = entry
        _save_state(state)

    return {
        "name": policy.name,
        "applied": result.get("applied", False),
        "reason": result.get("reason", ""),
        "stats": result.get("stats", {}),
        "duration_s": duration,
    }


def run_all(
    policies: Optional[List[Policy]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run all policies under a cross-process filelock.

    If a second call arrives while the lock is held, it blocks until the
    first completes. The filelock timeout is 30 s; on timeout the caller
    receives errors=1 with a reason string.
    """
    if policies is None:
        policies = DEFAULT_POLICIES

    lock = FileLock(str(_LOCK_PATH), timeout=30)
    try:
        with lock, _GLOBAL_LOCK:
            details = [run_policy(p, dry_run=dry_run) for p in policies]
    except TimeoutError:
        return {
            "policies_run": 0,
            "applied": 0,
            "errors": 1,
            "details": [{"name": "lock", "applied": False, "reason": "lock timeout after 30s", "stats": {}}],
        }

    applied = sum(1 for d in details if d["applied"])
    errors = sum(1 for d in details if not d["applied"] and d.get("reason", "").startswith("Exception"))
    return {
        "policies_run": len(details),
        "applied": applied,
        "errors": errors,
        "details": details,
    }


# ── Default policies ──────────────────────────────────────────────────────────

DEFAULT_POLICIES: List[Policy] = [
    # #1 — turns.jsonl: rotate daily, keep 90 days of archives
    Policy(
        name="turns_jsonl",
        path="logs/turns.jsonl",
        kind="jsonl_rotate",
        keep_archives=90,
        archive_dir="logs/archive",
        schedule="daily",
    ),
    # #2 — evolution.jsonl: rotate daily, keep 180 days of archives
    Policy(
        name="evolution_jsonl",
        path="logs/evolution.jsonl",
        kind="jsonl_rotate",
        keep_archives=180,
        archive_dir="logs/archive",
        schedule="daily",
    ),
    # #3 — watcher_events table: prune rows older than 30 days
    Policy(
        name="watcher_events_prune",
        path="data/watchers.db",
        kind="sqlite_table_prune",
        table="watcher_events",
        timestamp_col="timestamp",
        max_age_days=30,
        vacuum_after=True,
        schedule="daily",
    ),
    # #4 — memory_replication.log: rotate when >50 MB
    Policy(
        name="memory_replication_log",
        path="data/memory_replication.log",
        kind="log_size_rotate",
        max_size_mb=50,
        keep_archives=5,
        schedule="daily",
    ),
    # #5 — pi.db: VACUUM weekly to reclaim space after L3 deletes
    Policy(
        name="pi_db_vacuum",
        path="data/pi.db",
        kind="sqlite_vacuum",
        schedule="weekly",
    ),
    # #6 — T-135/T-300: decay-archive L3 rows whose effective importance < 1.0
    #      Default-on (opt-out via PI_DECAY_ARCHIVE=off). Daily — "timely" forgetting.
    Policy(
        name="l3_decay_archive",
        path="data/pi.db",
        kind="l3_decay_archive",
        schedule="daily",
    ),
]
