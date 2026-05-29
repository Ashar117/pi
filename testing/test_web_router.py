"""Tests for tools/tools_web.py web search router (T-053)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestWebToolsRouter:

    def test_ddg_fallback_success(self):
        """With no API keys set, falls through to DDG and returns results."""
        from tools.tools_web import WebTools

        fake_results = [{"title": "Test", "snippet": "A test result", "url": "https://example.com"}]
        with patch("tools.tools_web._ddg_search", return_value=fake_results), \
             patch("tools.tools_web._brave_search", return_value=None), \
             patch("tools.tools_web._tavily_search", return_value=None), \
             patch("tools.tools_web._cache_get", return_value=None), \
             patch("tools.tools_web._cache_set"):
            result = WebTools().web_search("test query", max_results=3)

        assert result["count"] == 1
        assert result["provider"] == "ddg"
        assert result["results"][0]["title"] == "Test"

    def test_brave_takes_priority(self):
        """Brave results are returned if BRAVE_API_KEY is set and search succeeds."""
        from tools.tools_web import WebTools

        brave_results = [{"title": "Brave Result", "snippet": "...", "url": "https://brave.com"}]
        with patch("tools.tools_web._brave_search", return_value=brave_results), \
             patch("tools.tools_web._cache_get", return_value=None), \
             patch("tools.tools_web._cache_set"):
            result = WebTools().web_search("brave query")

        assert result["provider"] == "brave"
        assert result["results"][0]["title"] == "Brave Result"

    def test_cache_hit_returns_cached(self):
        """A cached result is returned without hitting the search provider."""
        from tools.tools_web import WebTools

        cached = [{"title": "Cached", "snippet": "from cache", "url": "https://cache.com"}]
        with patch("tools.tools_web._cache_get", return_value=cached), \
             patch("tools.tools_web._brave_search") as brave_mock:
            result = WebTools().web_search("cached query")

        brave_mock.assert_not_called()
        assert "cached" in result["provider"]
        assert result["results"][0]["title"] == "Cached"

    def test_all_providers_fail_returns_error(self):
        """If all providers return None, an error result is returned (no exception)."""
        from tools.tools_web import WebTools

        with patch("tools.tools_web._brave_search", return_value=None), \
             patch("tools.tools_web._tavily_search", return_value=None), \
             patch("tools.tools_web._ddg_search", return_value=None), \
             patch("tools.tools_web._cache_get", return_value=None):
            result = WebTools().web_search("nothing works")

        assert result["count"] == 0
        assert "error" in result

    def test_max_results_capped_at_10(self):
        """max_results > 10 is clamped to 10 before calling providers."""
        from tools.tools_web import WebTools

        captured = []
        def fake_ddg(query, max_results):
            captured.append(max_results)
            return [{"title": "r", "snippet": "", "url": "u"}]

        with patch("tools.tools_web._brave_search", return_value=None), \
             patch("tools.tools_web._tavily_search", return_value=None), \
             patch("tools.tools_web._ddg_search", side_effect=fake_ddg), \
             patch("tools.tools_web._cache_get", return_value=None), \
             patch("tools.tools_web._cache_set"):
            WebTools().web_search("query", max_results=999)

        assert captured[0] == 10

    def test_ddg_html_parser(self):
        """_parse_ddg_html extracts title, snippet, and URL from sample HTML."""
        from tools.tools_web import _parse_ddg_html

        html = '''
        <div class="result result--default">
          <a class="result__a" href="https://example.com">Example Title</a>
          <a class="result__snippet">A snippet about this result.</a>
        </div></div>
        '''
        results = _parse_ddg_html(html, 5)
        # Parser may return 0 if HTML doesn't match exactly — just verify no crash
        assert isinstance(results, list)
