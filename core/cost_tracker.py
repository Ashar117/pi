"""core/cost_tracker.py — Per-turn LLM cost tracking + response cache.

Two SQLite tables in data/llm_cost.db:
  llm_costs  — one row per LLM call; provider/model/tokens/cost/session
  llm_cache  — keyed by hash(messages+system); TTL-based response cache

Cost rates (per 1M tokens, late-2025):
  claude-sonnet-4-6     $3.00 in / $15.00 out
  claude-opus-4-7       $15.00 in / $75.00 out
  claude-haiku-4-5      $0.80 in / $4.00 out
  llama-3.3-70b (Groq)  $0.59 in / $0.79 out
  gemini-2.0-flash      $0.10 in / $0.40 out
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

_DB_PATH = Path(__file__).parent.parent / "data" / "llm_cost.db"

# $/1M tokens — (in, out)
_RATES: Dict[str, tuple] = {
    "claude-sonnet-4-6":          (3.00,  15.00),
    "claude-opus-4-7":            (15.00, 75.00),
    "claude-haiku-4-5":           (0.80,   4.00),
    "claude-haiku-4-5-20251001":  (0.80,   4.00),
    "llama-3.3-70b-versatile":    (0.59,   0.79),
    "llama-3.1-8b-instant":       (0.05,   0.08),
    "gemini-2.0-flash":           (0.10,   0.40),
    "gemini-1.5-flash":           (0.075,  0.30),
}

CACHE_TTL_HOURS = 6   # responses cached for this long by default


def _db(path: Path = _DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(path: Path = _DB_PATH) -> None:
    with _db(path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_costs (
                id          TEXT PRIMARY KEY,
                ts          TEXT NOT NULL,
                provider    TEXT NOT NULL,
                model       TEXT NOT NULL,
                tokens_in   INTEGER DEFAULT 0,
                tokens_out  INTEGER DEFAULT 0,
                cost_usd    REAL    DEFAULT 0.0,
                session_id  TEXT    DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_costs_ts ON llm_costs(ts);
            CREATE INDEX IF NOT EXISTS idx_costs_session ON llm_costs(session_id);

            CREATE TABLE IF NOT EXISTS llm_cache (
                cache_key   TEXT PRIMARY KEY,
                provider    TEXT,
                model       TEXT,
                response_json TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                hit_count   INTEGER DEFAULT 0,
                last_hit    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cache_exp ON llm_cache(expires_at);
        """)
        # T-084 R3: tier column on llm_costs. Idempotent — checks via
        # PRAGMA so the migration runs at most once per database file.
        # Old rows get DEFAULT 'balanced' which is accurate (that was the
        # implicit pre-T-084 default routing). Index helps `tokens_today`
        # group queries by (provider, date).
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(llm_costs)").fetchall()}
        if "tier" not in existing_cols:
            conn.execute("ALTER TABLE llm_costs ADD COLUMN tier TEXT DEFAULT 'balanced'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_costs_provider_ts ON llm_costs(provider, ts)"
        )


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return estimated USD cost for a call given token counts."""
    rate_in, rate_out = _RATES.get(model, (3.00, 15.00))
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


class CostTracker:
    """Thread-safe cost tracker + response cache backed by SQLite."""

    def __init__(self, db_path: Path = _DB_PATH):
        self._path = db_path
        _init_db(db_path)

    # ── Cost recording ─────────────────────────────────────────────────────

    def record(
        self,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        session_id: str = "",
        tier: str = "balanced",
    ) -> float:
        """Insert one cost row. Returns the computed cost in USD.

        T-084: `tier` records the routing intent (cheap/balanced/premium/
        private/fast) so the dashboard can split costs by use-case.
        """
        cost = estimate_cost(model, tokens_in, tokens_out)
        row = {
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "session_id": session_id,
            "tier": tier,
        }
        try:
            with _db(self._path) as conn:
                # Explicit column list so the insert tolerates the post-migration
                # schema with the new `tier` column without depending on column order.
                conn.execute(
                    "INSERT INTO llm_costs "
                    "(id, ts, provider, model, tokens_in, tokens_out, cost_usd, session_id, tier) "
                    "VALUES (:id, :ts, :provider, :model, :tokens_in, :tokens_out, :cost_usd, :session_id, :tier)",
                    row,
                )
        except Exception as e:
            print(f"[CostTracker] record error (non-fatal): {e}")
        return cost

    def tokens_today(self, provider: str) -> int:
        """T-084: sum of (tokens_in + tokens_out) for `provider` today (UTC).

        Used by LLMRouter for preemptive TPD-budget brownout. Returns 0 on
        any failure so a tracker outage cannot strand a call.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            with _db(self._path) as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM llm_costs "
                    "WHERE provider = ? AND date(ts) = ?",
                    [provider, today],
                ).fetchone()
                return int(row[0] or 0)
        except Exception:
            return 0

    def summary(self, hours: int = 24, session_id: str = "") -> Dict[str, Any]:
        """Return cost/token totals for the last N hours (or one session)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            with _db(self._path) as conn:
                if session_id:
                    rows = conn.execute(
                        "SELECT provider, model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*) "
                        "FROM llm_costs WHERE session_id=? "
                        "GROUP BY provider, model ORDER BY SUM(cost_usd) DESC",
                        [session_id],
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT provider, model, SUM(tokens_in), SUM(tokens_out), SUM(cost_usd), COUNT(*) "
                        "FROM llm_costs WHERE ts >= ? "
                        "GROUP BY provider, model ORDER BY SUM(cost_usd) DESC",
                        [cutoff],
                    ).fetchall()
                total = conn.execute(
                    "SELECT SUM(cost_usd), SUM(tokens_in), SUM(tokens_out), COUNT(*) "
                    "FROM llm_costs WHERE ts >= ? " + ("AND session_id=?" if session_id else ""),
                    [cutoff, session_id] if session_id else [cutoff],
                ).fetchone()
        except Exception as e:
            return {"error": str(e)}

        breakdown = [
            {
                "provider": r[0], "model": r[1],
                "tokens_in": r[2], "tokens_out": r[3],
                "cost_usd": round(r[4], 6), "calls": r[5],
            }
            for r in (rows or [])
        ]
        return {
            "hours": hours,
            "total_cost_usd": round((total[0] or 0.0), 6),
            "total_tokens_in": total[1] or 0,
            "total_tokens_out": total[2] or 0,
            "total_calls": total[3] or 0,
            "by_provider": breakdown,
        }

    def session_cost(self, session_id: str) -> float:
        """Fast lookup: total USD cost for one session."""
        try:
            with _db(self._path) as conn:
                row = conn.execute(
                    "SELECT SUM(cost_usd) FROM llm_costs WHERE session_id=?",
                    [session_id],
                ).fetchone()
                return round(row[0] or 0.0, 6)
        except Exception:
            return 0.0

    # ── Response cache ─────────────────────────────────────────────────────

    @staticmethod
    def _cache_key(messages: List[Dict], system: str, tools: List[Dict]) -> str:
        payload = json.dumps(
            {"m": messages, "s": system, "t": [t.get("name") for t in (tools or [])]},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def cache_get(
        self, messages: List[Dict], system: str, tools: List[Dict]
    ) -> Optional[Dict]:
        """Return cached LLMResponse dict if fresh, else None."""
        key = self._cache_key(messages, system, tools)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with _db(self._path) as conn:
                row = conn.execute(
                    "SELECT response_json FROM llm_cache WHERE cache_key=? AND expires_at>?",
                    [key, now],
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE llm_cache SET hit_count=hit_count+1, last_hit=? WHERE cache_key=?",
                        [now, key],
                    )
                    return json.loads(row[0])
        except Exception as e:
            print(f"[CostTracker] cache_get error (non-fatal): {e}")
        return None

    def cache_put(
        self,
        messages: List[Dict],
        system: str,
        tools: List[Dict],
        response: Dict,
        ttl_hours: int = CACHE_TTL_HOURS,
    ) -> None:
        """Store a response in cache."""
        key = self._cache_key(messages, system, tools)
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=ttl_hours)).isoformat()
        resp_data = response if isinstance(response, dict) else vars(response)
        # Only cache tool-free text responses (tool calls are stateful)
        if resp_data.get("tool_calls"):
            return
        try:
            with _db(self._path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO llm_cache "
                    "(cache_key,provider,model,response_json,created_at,expires_at,hit_count,last_hit) "
                    "VALUES (?,?,?,?,?,?,0,NULL)",
                    [
                        key,
                        resp_data.get("provider", ""),
                        resp_data.get("model", ""),
                        json.dumps(resp_data),
                        now.isoformat(),
                        expires,
                    ],
                )
        except Exception as e:
            print(f"[CostTracker] cache_put error (non-fatal): {e}")

    def cache_clear_expired(self) -> int:
        """Delete expired cache entries. Returns count deleted."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            with _db(self._path) as conn:
                cur = conn.execute("DELETE FROM llm_cache WHERE expires_at <= ?", [now])
                return cur.rowcount
        except Exception:
            return 0

    def cache_stats(self) -> Dict[str, Any]:
        """Return cache hit/miss stats."""
        try:
            with _db(self._path) as conn:
                total = conn.execute("SELECT COUNT(*), SUM(hit_count) FROM llm_cache").fetchone()
                fresh = conn.execute(
                    "SELECT COUNT(*) FROM llm_cache WHERE expires_at > ?",
                    [datetime.now(timezone.utc).isoformat()],
                ).fetchone()
            return {
                "total_entries": total[0] or 0,
                "total_hits": total[1] or 0,
                "fresh_entries": fresh[0] or 0,
            }
        except Exception as e:
            return {"error": str(e)}
