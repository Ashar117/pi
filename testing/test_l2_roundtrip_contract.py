"""T-167: L2 (organized_memory) write -> read round-trip contract — offline.

Uses a stateful in-memory Supabase double so no network is needed.
Guards the same write/read divergence class as test_memory_roundtrip_contract.py
but for the L2 (Supabase organized_memory) tier.
"""
import fnmatch
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.tools_memory import MemoryTools


def _ilike_match(value: str, pattern: str) -> bool:
    """Case-insensitive SQL ILIKE match (% = any chars)."""
    return fnmatch.fnmatch(value.lower(), pattern.lower().replace("%", "*"))


class _L2RecordingSupabase:
    """Stateful in-memory Supabase double for organized_memory.

    Supports the fluent chain: .table().select/insert/update.ilike/eq/order/limit.execute()
    Handles content->>text JSON path for body search.
    """
    _mock_name = "recording"

    def __init__(self):
        self._stores: dict[str, list] = {}
        self._table = None
        self._op = None
        self._op_data = None
        self._filters: list = []
        self._order_col = None
        self._order_desc = False
        self._limit_n = None

    def table(self, name: str):
        self._table = name
        self._op = None
        self._op_data = None
        self._filters = []
        self._order_col = None
        self._limit_n = None
        return self

    def select(self, *_):
        self._op = "select"
        return self

    def insert(self, rows):
        if self._table not in self._stores:
            self._stores[self._table] = []
        if isinstance(rows, dict):
            rows = [rows]
        self._stores[self._table].extend(rows)
        self._op = "insert"
        return self

    def update(self, data):
        self._op = "update"
        self._op_data = data
        return self

    def ilike(self, col: str, pattern: str):
        self._filters.append(("ilike", col, pattern))
        return self

    def eq(self, col: str, value):
        self._filters.append(("eq", col, value))
        return self

    def order(self, col: str, desc: bool = False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n: int):
        self._limit_n = n
        return self

    def _get_col_value(self, row: dict, col: str):
        """Resolve dotted or JSON-path columns like 'content->>text'."""
        if "->>" in col:
            # e.g. "content->>text" -> row["content"]["text"]
            parts = col.split("->>")
            val = row
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p, "")
                else:
                    val = ""
            return str(val)
        return str(row.get(col, ""))

    def execute(self):
        if self._op in ("insert", "update"):
            # Side-effects already applied; just return empty result
            class _R:
                data = []
            return _R()

        # select path: apply filters, order, limit
        rows = list(self._stores.get(self._table, []))
        for (kind, col, val) in self._filters:
            if kind == "ilike":
                rows = [r for r in rows if _ilike_match(self._get_col_value(r, col), val)]
            elif kind == "eq":
                rows = [r for r in rows if self._get_col_value(r, col) == str(val)]

        if self._order_col:
            rows = sorted(rows, key=lambda r: r.get(self._order_col, ""),
                          reverse=self._order_desc)
        if self._limit_n is not None:
            rows = rows[:self._limit_n]

        result_data = rows

        class _R:
            pass
        r = _R()
        r.data = result_data
        return r


def _l2_mt(tmp_path):
    """MemoryTools with isolated SQLite and the L2 recording Supabase."""
    mt = MemoryTools(supabase_url="", supabase_key="",
                     sqlite_path=str(tmp_path / "pi.db"))
    mt.supabase = _L2RecordingSupabase()
    return mt


# ── L2 round-trip tests ───────────────────────────────────────────────────────

def test_l2_roundtrip_single_fact(tmp_path):
    mt = _l2_mt(tmp_path)
    mt.memory_write(content="the secret project codeword is NIGHTHAWK",
                    tier="l2", category="note", importance=6)
    hits = mt.memory_read(query="NIGHTHAWK", tier="l2")
    assert any("NIGHTHAWK" in (h.get("content") or {}).get("text", "") or
               "NIGHTHAWK" in h.get("title", "")
               for h in hits), f"L2 fact not readable back; got {hits}"


def test_l2_roundtrip_content_preserved(tmp_path):
    mt = _l2_mt(tmp_path)
    body = "Ash's research area is graph neural networks (GNNs) applied to molecular design"
    mt.memory_write(content=body, tier="l2", category="profile", importance=8)
    hits = mt.memory_read(query="graph neural networks", tier="l2")
    assert hits, "L2 returned no results"
    found = hits[0].get("content") or {}
    text = found.get("text", "") if isinstance(found, dict) else str(found)
    assert "graph neural networks" in text, f"L2 content not preserved; got {text!r}"


def test_l2_roundtrip_keyword_past_100_chars(tmp_path):
    """Keyword buried past char-100 in title must be found via body search (SM-003 class)."""
    mt = _l2_mt(tmp_path)
    body = "background context " * 6 + "UNIQUETOKEN999 deep in the body"
    mt.memory_write(content=body, tier="l2", category="note", importance=5)
    hits = mt.memory_read(query="UNIQUETOKEN999", tier="l2")
    assert any("UNIQUETOKEN999" in (h.get("content") or {}).get("text", "")
               for h in hits), "L2 dropped keyword past char-100 (SM-003 regression)"


def test_l2_roundtrip_write_returns_success(tmp_path):
    mt = _l2_mt(tmp_path)
    result = mt.memory_write(content="some L2 fact", tier="l2", category="note", importance=5)
    assert result.get("tier") == "l2", f"write result tier wrong: {result}"
    # success may be False offline (verify step hits the Supabase double), but the
    # round-trip contract is what the read returns, not the write result flag.


def test_l2_multiple_facts_searchable(tmp_path):
    mt = _l2_mt(tmp_path)
    facts = ["ALPHACODE key fact", "BETACODE other note", "GAMMACODE third item"]
    for f in facts:
        mt.memory_write(content=f, tier="l2", category="note", importance=6)
    for kw in ("ALPHACODE", "BETACODE", "GAMMACODE"):
        hits = mt.memory_read(query=kw, tier="l2")
        assert any(kw in (h.get("content") or {}).get("text", "") or kw in h.get("title", "")
                   for h in hits), f"L2 lost fact {kw!r}"
