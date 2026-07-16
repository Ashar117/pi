"""agent/watchers.py — Background event watchers with Telegram alerts.

WatcherManager runs as a daemon thread inside PiAgent. Every 60 seconds it
evaluates each active watcher and fires a Telegram alert when the condition
is met.

Watcher types
  file      — alert when a file/directory is created, modified, or deleted
  schedule  — fire at a fixed cron-like interval (every N minutes/hours)
  url       — alert when a URL's content changes or matches a keyword
  keyword   — alert when a file contains a new occurrence of a keyword
  price     — alert when a stock/crypto ticker crosses a threshold (uses yfinance)
  email     — alert on unread Gmail from the last day not seen in a prior sweep (T-257)

Storage: data/watchers.db  (SQLite — gitignored runtime data)

Tools (wired in agent/tools.py):
    watcher_add     — register a new watcher
    watcher_list    — list all active watchers
    watcher_remove  — delete a watcher by name
    watcher_status  — last-check timestamps and event counts
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

_DB_PATH = Path(__file__).parent.parent / "data" / "watchers.db"
_POLL_INTERVAL = 60          # seconds between watcher sweeps
_MAX_EVENTS_PER_WATCHER = 50 # keep last N events per watcher


# ── DB helpers ─────────────────────────────────────────────────────────────────

@contextmanager
def _db(path: Path = _DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection, commit on success, rollback+close on any exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_ANALYZE_RATE_LIMIT = 6   # max analyzed watcher events per hour
_WATCHER_CONV_ID = "watchers"  # dedicated conversation — never bleeds into Ash's active chat


def _init_db(path: Path = _DB_PATH) -> None:
    with _db(path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS watchers (
                id          TEXT PRIMARY KEY,
                name        TEXT UNIQUE NOT NULL,
                type        TEXT NOT NULL,
                config      TEXT NOT NULL DEFAULT '{}',
                alert_msg   TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'active',
                last_check  TEXT,
                last_fire   TEXT,
                next_check  TEXT,
                fire_count  INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL,
                analyze     INTEGER NOT NULL DEFAULT 0,
                state       TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS watcher_events (
                id          TEXT PRIMARY KEY,
                watcher_id  TEXT NOT NULL,
                fired_at    TEXT NOT NULL,
                details     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_we_wid ON watcher_events(watcher_id);
        """)
        # T-206: idempotent migration for existing DBs missing the analyze column
        existing = {r[1] for r in conn.execute("PRAGMA table_info(watchers)").fetchall()}
        if "analyze" not in existing:
            conn.execute("ALTER TABLE watchers ADD COLUMN analyze INTEGER NOT NULL DEFAULT 0")
        # T-289: idempotent migration for existing DBs missing the state column
        if "state" not in existing:
            conn.execute("ALTER TABLE watchers ADD COLUMN state TEXT NOT NULL DEFAULT '{}'")


# ── Watcher evaluators ─────────────────────────────────────────────────────────

def _check_file(config: Dict, state: Dict) -> tuple[bool, str, Dict]:
    """Returns (triggered, detail, new_state)."""
    path = Path(config.get("path", ""))
    event = config.get("event", "any")  # created | modified | deleted | any

    exists_now = path.exists()
    existed_before = state.get("existed", None)
    mtime_before = state.get("mtime", 0)

    new_state = state.copy()
    new_state["existed"] = exists_now

    if exists_now:
        try:
            mtime_now = path.stat().st_mtime
        except OSError:
            mtime_now = 0
        new_state["mtime"] = mtime_now
    else:
        mtime_now = 0

    if existed_before is None:
        return False, "", new_state  # first check — just record state

    if event in ("created", "any") and not existed_before and exists_now:
        return True, f"File created: {path}", new_state
    if event in ("deleted", "any") and existed_before and not exists_now:
        return True, f"File deleted: {path}", new_state
    if event in ("modified", "any") and exists_now and mtime_now != mtime_before:
        return True, f"File modified: {path}", new_state

    return False, "", new_state


def _check_schedule(config: Dict, state: Dict) -> tuple[bool, str, Dict]:
    """Fire every N minutes."""
    interval_min = int(config.get("interval_minutes", 60))
    last_fire_str = state.get("last_fire")
    now = datetime.now(timezone.utc)
    if last_fire_str:
        last_fire = datetime.fromisoformat(last_fire_str)
        if (now - last_fire).total_seconds() < interval_min * 60:
            return False, "", state
    new_state = {**state, "last_fire": now.isoformat()}
    return True, f"Scheduled check (every {interval_min}m)", new_state


def _check_url(config: Dict, state: Dict) -> tuple[bool, str, Dict]:
    """Alert when URL content changes or contains a keyword."""
    import hashlib
    try:
        import requests as _req
    except ImportError:
        return False, "requests not installed", state

    url = config.get("url", "")
    keyword = config.get("keyword", "")
    check_change = config.get("check_change", False)

    try:
        resp = _req.get(url, timeout=10, headers={"User-Agent": "Pi-Watcher/1.0"})
        text = resp.text[:50_000]
    except Exception as e:
        return False, f"URL fetch error: {e}", state

    new_state = state.copy()
    triggered = False
    detail = ""

    if keyword and keyword.lower() in text.lower():
        prev_found = state.get("keyword_found", False)
        if not prev_found:
            triggered = True
            detail = f"Keyword '{keyword}' found in {url}"
        new_state["keyword_found"] = True
    elif keyword:
        new_state["keyword_found"] = False

    if check_change:
        h = hashlib.sha256(text.encode()).hexdigest()
        prev_h = state.get("content_hash")
        if prev_h and prev_h != h:
            triggered = True
            detail = f"Content changed at {url}"
        new_state["content_hash"] = h

    return triggered, detail, new_state


def _check_keyword(config: Dict, state: Dict) -> tuple[bool, str, Dict]:
    """Alert when a file contains a new line matching a regex keyword."""
    path = Path(config.get("path", ""))
    pattern = config.get("pattern", "")
    if not path.exists() or not pattern:
        return False, "", state

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False, "", state

    prev_count = state.get("line_count", 0)
    new_count = len(lines)
    new_state = {**state, "line_count": new_count}

    if new_count <= prev_count:
        return False, "", new_state

    new_lines = lines[prev_count:]
    rx = re.compile(pattern, re.IGNORECASE)
    matches = [l for l in new_lines if rx.search(l)]
    if matches:
        sample = matches[0][:120]
        return True, f"Keyword match in {path.name}: {sample}", new_state
    return False, "", new_state


_EMAIL_QUERY = "is:unread newer_than:1d"


def _check_email(config: Dict, state: Dict) -> tuple[bool, str, Dict]:
    """T-257: alert on unread mail from the last day not seen in a prior sweep.

    Fires once per message id, keyed off in-memory state like every other
    watcher type in this module (no persistence beyond a restart — matches
    the existing file/url/keyword watchers' limitation, not a new one).
    """
    try:
        from tools.tools_gmail import GmailTools
    except ImportError:
        return False, "Gmail tools not installed", state

    gmail = GmailTools()
    if not gmail.is_configured():
        return False, "Gmail not configured (data/gmail_credentials.json missing)", state

    result = gmail.gmail_search(query=_EMAIL_QUERY, max_results=10)
    if not result.get("success"):
        return False, f"Gmail search error: {result.get('error', 'unknown')}", state

    seen_ids = set(state.get("seen_ids", []))
    messages = result.get("messages", [])
    new_messages = [m for m in messages if m["id"] not in seen_ids]

    new_state = {"seen_ids": list(seen_ids | {m["id"] for m in messages})[-200:]}

    if not new_messages:
        return False, "", new_state

    m = new_messages[0]
    detail = f"New mail from {m.get('from_short', 'unknown')}: {m.get('subject', '(no subject)')}"
    if len(new_messages) > 1:
        detail += f" (+{len(new_messages) - 1} more)"
    new_state["last_fired_ids"] = [m["id"] for m in new_messages]
    return True, detail, new_state


def _check_price(config: Dict, state: Dict) -> tuple[bool, str, Dict]:
    """Alert when a ticker crosses a threshold."""
    try:
        import yfinance as yf
    except ImportError:
        return False, "yfinance not installed (pip install yfinance)", state

    ticker = config.get("ticker", "")
    above = config.get("above")
    below = config.get("below")

    try:
        price = yf.Ticker(ticker).fast_info["lastPrice"]
    except Exception as e:
        return False, f"yfinance error: {e}", state

    triggered = False
    detail = ""
    if above is not None and price > float(above):
        triggered = True
        detail = f"{ticker} @ ${price:.2f} (above ${above})"
    elif below is not None and price < float(below):
        triggered = True
        detail = f"{ticker} @ ${price:.2f} (below ${below})"

    # T-277: edge-trigger — alert on the crossing, not on every 60s sweep the
    # price stays past the threshold (mirrors _check_url's keyword_found).
    prev = state.get("was_triggered", False)
    new_state = {**state, "was_triggered": triggered}
    if triggered and prev:
        return False, "", new_state
    return triggered, detail, new_state


_EVALUATORS = {
    "file":     _check_file,
    "schedule": _check_schedule,
    "url":      _check_url,
    "keyword":  _check_keyword,
    "price":    _check_price,
    "email":    _check_email,
}


# ── WatcherManager ─────────────────────────────────────────────────────────────

class WatcherManager:
    """Background thread that polls watchers and sends Telegram alerts."""

    def __init__(
        self,
        db_path: Path = _DB_PATH,
        telegram_send_fn=None,
        telegram_buttons_fn=None,
        agent=None,
    ) -> None:
        self._path = db_path
        self._telegram = telegram_send_fn
        self._telegram_buttons = telegram_buttons_fn  # T-258: (text, [(label, callback_data)]) -> dict
        self._agent = agent  # T-206: optional PiAgent for analyzed events
        self._analyze_timestamps: list = []  # rate-limit rolling window
        self._state: Dict[str, Dict] = {}  # watcher_id → per-type state dict
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        _init_db(db_path)

    # ── Daemon ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background polling thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="PiWatchers"
        )
        self._thread.start()
        print("[Watchers] background thread started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sweep()
            except Exception as e:
                print(f"[Watchers] sweep error (non-fatal): {e}")
            self._stop_event.wait(_POLL_INTERVAL)

    def _sweep(self) -> None:
        now = datetime.now(timezone.utc)
        with _db(self._path) as conn:
            rows = conn.execute(
                "SELECT * FROM watchers WHERE status='active'"
            ).fetchall()

        for row in rows:
            wid = row["id"]
            wtype = row["type"]
            config = json.loads(row["config"] or "{}")
            alert_msg = row["alert_msg"] or ""
            # T-289: seed from the persisted column on first encounter after a
            # restart, so watchers don't re-fire against a blank baseline.
            if wid not in self._state:
                try:
                    self._state[wid] = json.loads(row["state"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    self._state[wid] = {}
            state = self._state[wid]

            evaluator = _EVALUATORS.get(wtype)
            if not evaluator:
                continue

            try:
                triggered, detail, new_state = evaluator(config, state)
            except Exception as e:
                print(f"[Watchers] {row['name']} evaluator error: {e}")
                continue

            self._state[wid] = new_state
            ts = now.isoformat()

            with _db(self._path) as conn:
                conn.execute(
                    "UPDATE watchers SET last_check=?, state=? WHERE id=?",
                    [ts, json.dumps(new_state), wid],
                )

            if triggered:
                analyze = bool(row["analyze"]) if "analyze" in row.keys() else False
                email_message_id = None
                if wtype == "email" and new_state.get("last_fired_ids"):
                    email_message_id = new_state["last_fired_ids"][0]
                self._fire(wid, row["name"], alert_msg or detail, detail, analyze=analyze,
                           wtype=wtype, email_message_id=email_message_id)
                with _db(self._path) as conn:
                    conn.execute(
                        "UPDATE watchers SET last_fire=?, fire_count=fire_count+1 WHERE id=?",
                        [ts, wid],
                    )
                    conn.execute(
                        "INSERT INTO watcher_events (id,watcher_id,fired_at,details) VALUES (?,?,?,?)",
                        [str(uuid.uuid4()), wid, ts, detail],
                    )
                    # Prune old events
                    conn.execute(
                        "DELETE FROM watcher_events WHERE watcher_id=? AND id NOT IN "
                        "(SELECT id FROM watcher_events WHERE watcher_id=? "
                        "ORDER BY fired_at DESC LIMIT ?)",
                        [wid, wid, _MAX_EVENTS_PER_WATCHER],
                    )

    def _fire(self, wid: str, name: str, alert_msg: str, detail: str, *, analyze: bool = False,
              wtype: str = "", email_message_id: Optional[str] = None) -> None:
        print(f"[Watchers] FIRED: {name} — {detail}")

        if analyze and self._agent is not None and self._within_rate_limit():
            msg = self._analyzed_fire(name, alert_msg, detail)
        else:
            msg = f"[Pi Watcher] *{name}*\n{alert_msg}"
            if detail and detail != alert_msg:
                msg += f"\n_{detail}_"

        # T-258: email watcher alerts get triage buttons instead of plain text.
        if wtype == "email" and email_message_id and self._telegram_buttons:
            try:
                self._telegram_buttons(msg, [
                    ("Draft reply", f"emailtriage:reply:{email_message_id}"),
                    ("Add to calendar", f"emailtriage:cal:{email_message_id}"),
                    ("Ignore", f"emailtriage:ignore:{email_message_id}"),
                ])
                return
            except Exception as e:
                print(f"[Watchers] Telegram buttons send failed: {e}")
                # fall through to plain send below

        if self._telegram:
            try:
                self._telegram(msg)
            except Exception as e:
                print(f"[Watchers] Telegram send failed: {e}")

    def _within_rate_limit(self) -> bool:
        """True if fewer than _ANALYZE_RATE_LIMIT analyzed events in the last hour."""
        now = time.time()
        cutoff = now - 3600
        self._analyze_timestamps = [t for t in self._analyze_timestamps if t > cutoff]
        if len(self._analyze_timestamps) >= _ANALYZE_RATE_LIMIT:
            return False
        self._analyze_timestamps.append(now)
        return True

    def _analyzed_fire(self, name: str, alert_msg: str, detail: str) -> str:
        """T-206: run the watcher event through the agent in the dedicated watchers conversation."""
        try:
            from agent.conversation import conversation_switch
            prompt = (
                f"[Watcher '{name}' fired]\n{alert_msg}\n\nDetail: {detail}\n\n"
                "Briefly explain why this matters and what (if anything) should be done. "
                "Keep it under 3 sentences."
            )
            with conversation_switch(self._agent, _WATCHER_CONV_ID):
                analysis = self._agent.process_input(prompt) or ""
            return f"[Pi Watcher] *{name}*\n{analysis or detail}"
        except Exception as e:
            print(f"[Watchers] analysis failed, falling back to raw: {e}")
            msg = f"[Pi Watcher] *{name}*\n{alert_msg}"
            if detail and detail != alert_msg:
                msg += f"\n_{detail}_"
            return msg

    # ── Public API ─────────────────────────────────────────────────────────

    def watcher_add(
        self,
        name: str,
        type: str,
        config: Dict,
        alert_msg: str = "",
        analyze: bool = False,
    ) -> Dict:
        """Register a new watcher.

        Args:
            name:      Unique display name (e.g. 'pi-log-errors')
            type:      'file' | 'schedule' | 'url' | 'keyword' | 'price' | 'email'
            config:    Type-specific config dict (see module docstring)
            alert_msg: Custom Telegram message on trigger (optional)
            analyze:   T-206 — if True, route event through agent for analysis
                       before sending to Telegram (rate-limited, normie tier).

        Config examples:
            file:     {"path": "/path/to/file", "event": "modified"}
            schedule: {"interval_minutes": 30}
            url:      {"url": "https://...", "keyword": "error", "check_change": false}
            keyword:  {"path": "/path/to/log", "pattern": "ERROR|CRITICAL"}
            price:    {"ticker": "NVDA", "above": 200, "below": null}
            email:    {} (no config — fixed query "is:unread newer_than:1d")

        Returns:
            {"id": str, "name": str, "success": bool}
        """
        if type not in _EVALUATORS:
            return {"success": False, "error": f"Unknown type '{type}'. Valid: {list(_EVALUATORS)}"}
        now = datetime.now(timezone.utc).isoformat()
        wid = str(uuid.uuid4())
        try:
            with _db(self._path) as conn:
                conn.execute(
                    "INSERT INTO watchers (id,name,type,config,alert_msg,status,created_at,analyze) "
                    "VALUES (?,?,?,?,?,'active',?,?)",
                    [wid, name, type, json.dumps(config), alert_msg, now, int(analyze)],
                )
            return {"id": wid, "name": name, "type": type, "success": True}
        except sqlite3.IntegrityError:
            return {"success": False, "error": f"Watcher '{name}' already exists"}

    def watcher_list(self) -> List[Dict]:
        """Return all watchers with their status."""
        with _db(self._path) as conn:
            rows = conn.execute(
                "SELECT id,name,type,status,last_check,last_fire,fire_count,config FROM watchers "
                "ORDER BY created_at"
            ).fetchall()
        return [
            {
                "id": r["id"], "name": r["name"], "type": r["type"],
                "status": r["status"], "last_check": r["last_check"],
                "last_fire": r["last_fire"], "fire_count": r["fire_count"],
                "config": json.loads(r["config"] or "{}"),
            }
            for r in rows
        ]

    def watcher_remove(self, name: str) -> Dict:
        """Delete a watcher by name."""
        with _db(self._path) as conn:
            cur = conn.execute("DELETE FROM watchers WHERE name=?", [name])
            deleted = cur.rowcount
        if deleted:
            return {"success": True, "name": name, "deleted": True}
        return {"success": False, "name": name, "error": "Watcher not found"}

    def watcher_status(self) -> Dict:
        """Return summary stats for the watcher system."""
        with _db(self._path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM watchers").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM watchers WHERE status='active'"
            ).fetchone()[0]
            total_fires = conn.execute(
                "SELECT SUM(fire_count) FROM watchers"
            ).fetchone()[0] or 0
            recent = conn.execute(
                "SELECT watcher_id, fired_at, details FROM watcher_events "
                "ORDER BY fired_at DESC LIMIT 5"
            ).fetchall()
        return {
            "total_watchers": total,
            "active_watchers": active,
            "total_fires": total_fires,
            "thread_alive": self._thread is not None and self._thread.is_alive(),
            "recent_events": [
                {"fired_at": r["fired_at"], "details": r["details"]}
                for r in recent
            ],
        }


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _wm(agent):
    """Resolve the WatcherManager on the agent; returns None if unavailable."""
    return getattr(agent, "watchers", None)


def _handle_watcher_add(agent, tool_input, *, memory_override=None):
    wm = _wm(agent)
    if wm is None:
        return {"error": "WatcherManager unavailable"}
    return wm.watcher_add(
        name=tool_input["name"],
        type=tool_input["type"],
        config=tool_input.get("config", {}),
        alert_msg=tool_input.get("alert_msg", ""),
        analyze=bool(tool_input.get("analyze", False)),  # T-277: was silently dropped
    )


def _handle_watcher_list(agent, tool_input, *, memory_override=None):
    wm = _wm(agent)
    if wm is None:
        return {"error": "WatcherManager unavailable"}
    return {"watchers": wm.watcher_list()}


def _handle_watcher_remove(agent, tool_input, *, memory_override=None):
    wm = _wm(agent)
    if wm is None:
        return {"error": "WatcherManager unavailable"}
    return wm.watcher_remove(name=tool_input["name"])


def _handle_watcher_status(agent, tool_input, *, memory_override=None):
    wm = _wm(agent)
    if wm is None:
        return {"error": "WatcherManager unavailable"}
    return wm.watcher_status()


def _handle_watcher(agent, tool_input, *, memory_override=None):
    """Merged handler for watcher_add/list/remove/status.

    Routes by 'action' field; auto-detects from field presence for legacy aliases.
    """
    action = tool_input.get("action", "").lower()

    if not action:
        if "type" in tool_input:
            action = "add"
        elif "name" in tool_input:
            action = "remove"
        else:
            action = "status"

    if action == "add":
        return _handle_watcher_add(agent, tool_input, memory_override=memory_override)
    if action == "remove":
        return _handle_watcher_remove(agent, tool_input, memory_override=memory_override)
    if action == "list":
        return _handle_watcher_list(agent, tool_input, memory_override=memory_override)
    # action == "status" or anything else
    return _handle_watcher_status(agent, tool_input, memory_override=memory_override)


TOOLS = [
    ToolSpec(
        name="watcher",
        description=(
            "Manage background watchers that alert Ash when events fire. "
            "action='add': register watcher (name, type, config, alert_msg). "
            "action='remove': delete watcher by name. "
            "action='list': list all watchers. "
            "action='status': show system stats and recent events. "
            "Types: file, schedule, url, keyword, price, email."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action":    {"type": "string",
                              "enum": ["add", "remove", "list", "status"],
                              "description": "Operation to perform"},
                "name":      {"type": "string", "description": "Unique display name (add/remove)"},
                "type":      {"type": "string",
                              "enum": ["file", "schedule", "url", "keyword", "price", "email"],
                              "description": "Watcher type (add only)"},
                "config":    {"type": "object",
                              "description": "Type-specific config (add only)"},
                "alert_msg": {"type": "string",
                              "description": "Custom Telegram message (add, optional)"},
                "analyze":   {"type": "boolean",
                              "description": "Route fired events through Pi for a short "
                                             "analysis before alerting (add, optional; "
                                             "rate-limited to 6/hour)"},
            },
            "required": ["action"],
        },
        handler=_handle_watcher,
        # T-277: list/status results carry no "success" key — treat their shapes
        # as success instead of logging every read call as a failed tool use.
        success_predicate=lambda r: bool(r.get("success", "watchers" in r or "total_watchers" in r)),
        aliases=("watcher_add", "watcher_list", "watcher_remove", "watcher_status"),
    ),
]
