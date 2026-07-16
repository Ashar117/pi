"""testing/test_deep_research.py — T-228: deep_research multi-source synthesis.

All tests are offline — web_search, grounded_search, and awareness are stubbed.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock


def _stub_web_search(query, max_results=6):
    return {
        "results": [
            {"title": "BBC News", "url": "https://bbc.com/news/1", "snippet": "Pi is a constant 3.14"},
            {"title": "Reuters",  "url": "https://reuters.com/science/2", "snippet": "Pi equals approximately 3.14159"},
            {"title": "NYT",      "url": "https://nytimes.com/3", "snippet": "Mathematicians study pi"},
        ]
    }


def _stub_grounded_search(q):
    return {
        "answer": "Pi is the ratio of circumference to diameter.",
        "citations": [
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Pi"},
            {"title": "MathWorld", "url": "https://mathworld.wolfram.com/Pi.html"},
        ],
        "provider": "gemini-grounded",
    }


def _stub_awareness_news(max_items=5):
    return {
        "items": [
            {"title": "Pi Day 2026", "url": "https://piday.org/2026", "summary": "Celebrating pi day"},
        ]
    }


def _patch_all(fn):
    """Decorator: patch web_search + grounded_search + awareness + groq."""
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with patch("tools.tools_web.WebTools.web_search", side_effect=_stub_web_search):
            with patch("tools.tools_research.grounded_search", side_effect=_stub_grounded_search):
                with patch("tools.tools_awareness.AwarenessTools.get_news", side_effect=_stub_awareness_news):
                    with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
                        return fn(*args, **kwargs)
    return wrapper


# ── Shape tests ───────────────────────────────────────────────────────────────

@_patch_all
def test_deep_research_returns_required_fields():
    from tools.tools_research import deep_research
    result = deep_research("what is pi")
    assert "summary" in result
    assert "key_points" in result
    assert "sources" in result
    assert "agreement_notes" in result
    assert "confidence" in result
    assert "source_count" in result


@_patch_all
def test_sources_are_well_formed():
    from tools.tools_research import deep_research
    result = deep_research("what is pi")
    for s in result["sources"]:
        assert "title" in s
        assert "url" in s
        assert s["url"].startswith("http")


@_patch_all
def test_sources_are_deduped():
    from tools.tools_research import deep_research
    result = deep_research("what is pi")
    urls = [s["url"] for s in result["sources"]]
    assert len(urls) == len(set(urls)), "Duplicate URLs in sources"


# ── Cross-validation tests ────────────────────────────────────────────────────

@_patch_all
def test_multiple_sources_noted_in_agreement():
    from tools.tools_research import deep_research
    result = deep_research("what is pi")
    # With 3+ unique domains, the agreement_notes should mention source count
    assert "source" in result["agreement_notes"].lower() or result["source_count"] >= 1


def test_cross_validate_corroborated_with_two_domains():
    from tools.tools_research import _cross_validate
    sources = [
        {"url": "https://bbc.com/1", "title": "BBC"},
        {"url": "https://reuters.com/2", "title": "Reuters"},
    ]
    result = _cross_validate(sources)
    assert result["source_count"] == 2


def test_cross_validate_single_source():
    from tools.tools_research import _cross_validate
    sources = [{"url": "https://bbc.com/1", "title": "BBC"}]
    result = _cross_validate(sources)
    assert result["source_count"] == 1


# ── News query routing ────────────────────────────────────────────────────────

def test_news_query_detection():
    from tools.tools_research import _looks_like_news
    assert _looks_like_news("what is happening today")
    assert _looks_like_news("latest news on AI")
    assert not _looks_like_news("what is the speed of light")


# ── Normie allowlist ──────────────────────────────────────────────────────────

def test_deep_research_in_normie_allowlist():
    from agent.modes import MODE_CONFIGS
    normie = MODE_CONFIGS.get("normie")
    assert normie is not None
    assert "deep_research" in (normie.tool_allowlist or [])


# ── Tool registry ─────────────────────────────────────────────────────────────

def test_deep_research_tool_registered():
    from tools.tools_research import TOOLS
    names = [t.name for t in TOOLS]
    assert "deep_research" in names


# ── Fallback: empty sources ───────────────────────────────────────────────────

def test_deep_research_handles_no_sources():
    from tools.tools_research import deep_research
    with patch("tools.tools_research._gather_sources", return_value=[]):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            result = deep_research("impossible query xyzzy")
    assert result["summary"]  # should always return something
    assert result["sources"] == []
    assert result["confidence"] == "low"


# ── T-262: deep_debate tool ───────────────────────────────────────────────────

def test_deep_debate_tool_registered():
    from tools.tools_research import TOOLS
    names = [t.name for t in TOOLS]
    assert "deep_debate" in names


def test_deep_debate_calls_research_mode_non_interactively():
    from tools.tools_research import _handle_deep_debate
    with patch("core.research_mode.run_research_mode", return_value="final synthesis") as mock_run:
        result = _handle_deep_debate(None, {"question": "Is X better than Y?"})
    assert result == {"success": True, "synthesis": "final synthesis"}
    mock_run.assert_called_once_with(question="Is X better than Y?", rounds=2, interactive=False)


def test_deep_debate_passes_custom_rounds():
    from tools.tools_research import _handle_deep_debate
    with patch("core.research_mode.run_research_mode", return_value="x") as mock_run:
        _handle_deep_debate(None, {"question": "Q?", "rounds": 1})
    mock_run.assert_called_once_with(question="Q?", rounds=1, interactive=False)


if __name__ == "__main__":
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
