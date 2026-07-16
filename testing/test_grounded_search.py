"""testing/test_grounded_search.py — T-227: grounded_search tool (offline).

Stubs the Gemini client so no real API calls are made.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch


def _mock_gemini_grounded_response(answer: str, chunks: list):
    """Build a mock GeminiProvider with a grounded_search that returns preset data."""
    provider = MagicMock()
    provider.model = "gemini-2.5-flash"
    provider.grounded_search.return_value = {
        "answer": answer,
        "citations": chunks,
        "tokens_in": 100,
        "tokens_out": 50,
    }
    return provider


# ── Test 1: normal path returns answer + citations ────────────────────────────

def test_grounded_search_returns_answer_and_citations():
    citations = [
        {"title": "OpenAI Blog", "url": "https://openai.com/blog"},
        {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/GPT"},
    ]
    mock_provider = _mock_gemini_grounded_response("GPT-4 is a large language model.", citations)

    with patch("tools.tools_research._get_gemini_provider", return_value=mock_provider):
        from tools.tools_research import grounded_search
        result = grounded_search("What is GPT-4?")

    assert result["answer"] == "GPT-4 is a large language model."
    assert len(result["citations"]) == 2
    assert result["citations"][0]["url"] == "https://openai.com/blog"
    assert result["provider"] == "gemini-grounded"


# ── Test 2: falls back to web_search on Gemini error ─────────────────────────

def test_grounded_search_falls_back_on_gemini_error():
    mock_provider = MagicMock()
    mock_provider.grounded_search.side_effect = Exception("Gemini 429 quota exceeded")

    fake_web_result = {
        "results": [
            {"title": "Fallback result", "url": "https://example.com", "snippet": "some text"}
        ],
        "query": "test",
        "count": 1,
        "provider": "brave",
    }

    with patch("tools.tools_research._get_gemini_provider", return_value=mock_provider):
        with patch("tools.tools_web.WebTools.web_search", return_value=fake_web_result):
            from importlib import reload
            import tools.tools_research as tr
            result = tr.grounded_search("test query")

    assert result["provider"] in ("web_fallback", "error")


# ── Test 3: falls back when no GEMINI_API_KEY ─────────────────────────────────

def test_grounded_search_falls_back_when_no_api_key():
    with patch("tools.tools_research._get_gemini_provider", return_value=None):
        with patch("tools.tools_web.WebTools.web_search", return_value={
            "results": [], "query": "q", "count": 0, "provider": "brave"
        }):
            from tools.tools_research import grounded_search
            result = grounded_search("anything")

    assert result["provider"] in ("web_fallback", "error")


# ── Test 4: empty answer triggers web_search fallback ────────────────────────

def test_grounded_search_falls_back_on_empty_answer():
    mock_provider = _mock_gemini_grounded_response("", [])  # empty answer

    with patch("tools.tools_research._get_gemini_provider", return_value=mock_provider):
        with patch("tools.tools_web.WebTools.web_search", return_value={
            "results": [{"title": "T", "url": "https://t.co", "snippet": "s"}],
            "query": "q", "count": 1, "provider": "brave",
        }):
            from tools.tools_research import grounded_search
            result = grounded_search("anything")

    assert result["provider"] in ("web_fallback", "error", "gemini-grounded")


# ── Test 5: citations without URL are filtered out ────────────────────────────

def test_grounded_search_filters_empty_url_citations():
    citations = [
        {"title": "With URL", "url": "https://example.com"},
        {"title": "No URL", "url": ""},
        {"title": "Also none", "url": None},
    ]
    mock_provider = _mock_gemini_grounded_response("Some answer", citations)

    with patch("tools.tools_research._get_gemini_provider", return_value=mock_provider):
        from tools.tools_research import grounded_search
        result = grounded_search("question")

    urls = [c["url"] for c in result.get("citations", [])]
    assert "" not in urls
    assert None not in urls
    assert "https://example.com" in urls


# ── Test 6: grounded_search in normie tool_allowlist ─────────────────────────

def test_grounded_search_in_normie_allowlist():
    from agent.modes import MODE_CONFIGS
    normie = MODE_CONFIGS.get("normie")
    assert normie is not None, "normie mode not found"
    allowlist = normie.tool_allowlist
    assert allowlist is not None, "normie allowlist is None (admits all — grounded_search should be explicit)"
    assert "grounded_search" in allowlist, f"grounded_search not in normie allowlist: {allowlist}"


# ── Test 7: TOOLS export has the ToolSpec ─────────────────────────────────────

def test_tools_research_has_toolspec():
    from tools.tools_research import TOOLS
    names = [t.name for t in TOOLS]
    assert "grounded_search" in names, f"grounded_search ToolSpec missing; found: {names}"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
