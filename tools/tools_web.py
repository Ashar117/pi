"""
tools/tools_web.py — Web search for Pi.

Uses DuckDuckGo's public HTML endpoint (no API key, free, no rate-limit
registration required).  Returns the top N result titles + snippets +
URLs so Claude can synthesize an answer from current information.
"""

import re
from typing import List, Dict, Optional

import requests

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 10


class WebTools:
    """Lightweight DuckDuckGo search wrapper."""

    def web_search(
        self,
        query: str,
        max_results: int = 5,
        safe_search: bool = True,
    ) -> Dict:
        """Search the web via DuckDuckGo and return top results.

        Args:
            query:       Search string.
            max_results: How many results to return (max 10).
            safe_search: If True, adds safeSearch=strict parameter.

        Returns:
            {"results": [...], "query": str, "count": int}
            Each result: {"title": str, "snippet": str, "url": str}
        """
        max_results = min(max_results, 10)
        params = {"q": query}
        if safe_search:
            params["kp"] = "1"  # DDG safe-search strict

        try:
            resp = requests.post(
                _DDG_URL,
                data=params,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            return {"results": [], "query": query, "count": 0, "error": str(e)}

        results = _parse_ddg_html(resp.text, max_results)
        return {"results": results, "query": query, "count": len(results)}


# ── HTML parser ───────────────────────────────────────────────────────────────

def _parse_ddg_html(html: str, max_results: int) -> List[Dict]:
    """Extract results from DDG's HTML response without a full HTML parser."""
    results = []

    # DDG HTML results live inside <div class="result"> blocks.
    # Each block contains an <a class="result__a"> (title+url) and
    # <a class="result__snippet"> (snippet).
    result_blocks = re.findall(
        r'<div class="result[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html,
        re.DOTALL,
    )

    for block in result_blocks:
        if len(results) >= max_results:
            break

        # Title and URL
        title_match = re.search(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        # Snippet
        snippet_match = re.search(
            r'class="result__snippet"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )

        if not title_match:
            continue

        raw_url = title_match.group(1)
        # DDG wraps URLs in a redirect — extract the actual URL
        url = _extract_real_url(raw_url)
        title = _strip_tags(title_match.group(2)).strip()
        snippet = _strip_tags(snippet_match.group(1)).strip() if snippet_match else ""

        if title and url:
            results.append({"title": title, "snippet": snippet, "url": url})

    return results


def _extract_real_url(ddg_url: str) -> str:
    """Pull the real URL out of a DDG redirect like /l/?uddg=https%3A..."""
    if ddg_url.startswith("http"):
        return ddg_url
    match = re.search(r"uddg=([^&]+)", ddg_url)
    if match:
        from urllib.parse import unquote
        return unquote(match.group(1))
    return ddg_url


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace(
        "&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\s+", " ", text).strip()
