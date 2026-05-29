"""tools/tools_web.py — Web search router with SQLite cache (T-053).

Priority: Brave → Tavily → DuckDuckGo
1-hour SQLite cache keyed on (provider, query, max_results).

Environment:
  BRAVE_API_KEY   — Brave Search API key (https://api.search.brave.com)
  TAVILY_API_KEY  — Tavily API key (https://tavily.com)
  (no key needed for DuckDuckGo fallback)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

_TIMEOUT = 12
_CACHE_TTL = 3600  # 1 hour
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "web_cache.db"


# ── SQLite cache ──────────────────────────────────────────────────────────────

def _cache_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS web_cache "
        "(key TEXT PRIMARY KEY, results TEXT, ts REAL)"
    )
    conn.commit()
    return conn


def _cache_key(provider: str, query: str, max_results: int) -> str:
    raw = f"{provider}|{query.lower().strip()}|{max_results}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[List[Dict]]:
    try:
        with _cache_db() as conn:
            row = conn.execute(
                "SELECT results, ts FROM web_cache WHERE key = ?", (key,)
            ).fetchone()
        if row and time.time() - row[1] < _CACHE_TTL:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _cache_set(key: str, results: List[Dict]) -> None:
    try:
        with _cache_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO web_cache (key, results, ts) VALUES (?,?,?)",
                (key, json.dumps(results), time.time()),
            )
    except Exception:
        pass


# ── Brave Search ──────────────────────────────────────────────────────────────

def _brave_search(query: str, max_results: int) -> Optional[List[Dict]]:
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        return None
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={**_HEADERS, "Accept": "application/json", "X-Subscription-Token": api_key},
            params={"q": query, "count": min(max_results, 10), "safesearch": "moderate"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        items = []
        for w in (data.get("web", {}) or {}).get("results", [])[:max_results]:
            items.append({
                "title": w.get("title", ""),
                "snippet": w.get("description", ""),
                "url": w.get("url", ""),
            })
        return items or None
    except Exception:
        return None


# ── Tavily Search ─────────────────────────────────────────────────────────────

def _tavily_search(query: str, max_results: int) -> Optional[List[Dict]]:
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return None
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": min(max_results, 10)},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        items = []
        for w in r.json().get("results", [])[:max_results]:
            items.append({
                "title": w.get("title", ""),
                "snippet": w.get("content", "")[:300],
                "url": w.get("url", ""),
            })
        return items or None
    except Exception:
        return None


# ── DuckDuckGo (always-on fallback) ──────────────────────────────────────────

_DDG_URL = "https://html.duckduckgo.com/html/"


def _ddg_search(query: str, max_results: int) -> Optional[List[Dict]]:
    try:
        resp = requests.post(
            _DDG_URL,
            data={"q": query, "kp": "1"},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return _parse_ddg_html(resp.text, max_results) or None
    except Exception:
        return None


def _parse_ddg_html(html: str, max_results: int) -> List[Dict]:
    results = []
    blocks = re.findall(
        r'<div class="result[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html,
        re.DOTALL,
    )
    for block in blocks:
        if len(results) >= max_results:
            break
        title_m = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        snip_m = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_m:
            continue
        url = _real_url(title_m.group(1))
        title = _strip_tags(title_m.group(2)).strip()
        snippet = _strip_tags(snip_m.group(1)).strip() if snip_m else ""
        if title and url:
            results.append({"title": title, "snippet": snippet, "url": url})
    return results


def _real_url(ddg_url: str) -> str:
    if ddg_url.startswith("http"):
        return ddg_url
    m = re.search(r"uddg=([^&]+)", ddg_url)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1))
    return ddg_url

# backward-compat alias used by existing tests
_extract_real_url = _real_url


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace(
        "&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\s+", " ", text).strip()


# ── Router ────────────────────────────────────────────────────────────────────

# Names only — resolved at call time so tests can patch them
_PROVIDER_NAMES = ["brave", "tavily", "ddg"]
_PROVIDER_FNS = {
    "brave": _brave_search,
    "tavily": _tavily_search,
    "ddg": _ddg_search,
}

import sys as _sys
_THIS_MODULE = _sys.modules[__name__]


class WebTools:
    """Web search router: Brave → Tavily → DuckDuckGo, with 1-hour SQLite cache."""

    def web_search(
        self,
        query: str,
        max_results: int = 5,
        safe_search: bool = True,
    ) -> Dict:
        """Search the web and return top results.

        Args:
            query: Search string.
            max_results: Number of results to return (max 10).
            safe_search: Enable safe search filtering.

        Returns:
            {"results": [...], "query": str, "count": int, "provider": str}
        """
        max_results = max(1, min(max_results, 10))

        for provider_name in _PROVIDER_NAMES:
            # Resolve function at call time so tests can patch module-level names
            provider_fn = getattr(_THIS_MODULE, f"_{provider_name}_search")

            key = _cache_key(provider_name, query, max_results)
            cached = _cache_get(key)
            if cached is not None:
                return {
                    "results": cached,
                    "query": query,
                    "count": len(cached),
                    "provider": f"{provider_name}(cached)",
                }

            results = provider_fn(query, max_results)
            if results:
                _cache_set(key, results)
                return {
                    "results": results,
                    "query": query,
                    "count": len(results),
                    "provider": provider_name,
                }

        return {"results": [], "query": query, "count": 0, "error": "All search providers failed"}


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_web_search(agent, tool_input, *, memory_override=None):
    return WebTools().web_search(
        query=tool_input["query"],
        max_results=tool_input.get("max_results", 5),
    )


TOOLS = [
    ToolSpec(
        name="web_search",
        description=(
            "Search the web via DuckDuckGo for current information. Use when you need "
            "facts beyond your training cutoff (Aug 2025), live prices, recent events, "
            "or anything that may have changed."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query":       {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 5,
                                "description": "Max results to return (default 5)"},
            },
            "required": ["query"],
        },
        handler=_handle_web_search,
        success_predicate=lambda r: r.get("count", 0) > 0 or "error" not in r,
    ),
]
