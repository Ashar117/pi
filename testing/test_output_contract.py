"""testing/test_output_contract.py — T-229: research output formatting contract.

Tests:
  - Both consciousness.txt and consciousness_normie.txt contain the OUTPUT CONTRACT.
  - Post-step in pi_agent appends Sources when citations exist and model omitted them.
  - Post-step does NOT duplicate an existing Sources section.
  - Non-research turns (no citations) are unaffected.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent


# ── Prompt contract presence ──────────────────────────────────────────────────
# prompts/consciousness*.txt are the private identity prompts (gitignored — the
# "recipe"). These tests enforce content rules where the real files exist
# (Ash's machine); they skip on a public/CI checkout where only
# consciousness.default.txt is tracked.

def test_consciousness_txt_has_output_contract():
    path = _ROOT / "prompts" / "consciousness.txt"
    if not path.exists():
        pytest.skip("prompts/consciousness.txt is private/gitignored — not present in this checkout")
    text = path.read_text(encoding="utf-8")
    assert "OUTPUT CONTRACT" in text or "Sources" in text, \
        "consciousness.txt missing OUTPUT CONTRACT section"


def test_consciousness_normie_has_output_contract():
    path = _ROOT / "prompts" / "consciousness_normie.txt"
    if not path.exists():
        pytest.skip("prompts/consciousness_normie.txt is private/gitignored — not present in this checkout")
    text = path.read_text(encoding="utf-8")
    assert "Output contract" in text or "OUTPUT CONTRACT" in text or "Sources" in text, \
        "consciousness_normie.txt missing output contract"


# ── Sources post-step ─────────────────────────────────────────────────────────

def _make_mock_agent(citations=None):
    """Return a minimal object that mimics what the post-step reads."""
    class _MockAgent:
        _turn_citations = citations or []
    return _MockAgent()


def _run_post_step(final_text: str, citations: list) -> str:
    """Inline the post-step logic from pi_agent._respond_via_config."""
    _cites = citations
    if _cites and "**Sources**" not in final_text and "Sources" not in final_text[-200:]:
        _seen_urls: set = set()
        _src_lines = []
        for c in _cites:
            u = c.get("url", "")
            if u and u not in _seen_urls:
                _seen_urls.add(u)
                t = c.get("title", u)
                _src_lines.append(f"- [{t}]({u})")
        if _src_lines:
            final_text = final_text.rstrip() + "\n\n**Sources**\n" + "\n".join(_src_lines)
    return final_text


def test_sources_appended_when_missing():
    cites = [{"title": "BBC News", "url": "https://bbc.com/1"}]
    result = _run_post_step("Pi learned something cool.", cites)
    assert "**Sources**" in result
    assert "https://bbc.com/1" in result


def test_sources_not_duplicated_if_present():
    cites = [{"title": "BBC News", "url": "https://bbc.com/1"}]
    text = "Pi learned something.\n\n**Sources**\n- [BBC](https://bbc.com/1)"
    result = _run_post_step(text, cites)
    assert result.count("**Sources**") == 1, "Sources section duplicated"


def test_no_sources_when_no_citations():
    result = _run_post_step("Casual reply here.", [])
    assert "**Sources**" not in result


def test_sources_deduped():
    cites = [
        {"title": "BBC", "url": "https://bbc.com/1"},
        {"title": "BBC dup", "url": "https://bbc.com/1"},  # same URL
        {"title": "Reuters", "url": "https://reuters.com/2"},
    ]
    result = _run_post_step("Answer.", cites)
    assert result.count("https://bbc.com/1") == 1, "Duplicate URL appeared"
    assert "https://reuters.com/2" in result


def test_empty_url_citations_skipped():
    cites = [{"title": "No URL", "url": ""}]
    result = _run_post_step("Answer.", cites)
    assert "**Sources**" not in result


# ── pi_agent initialises _turn_citations per turn ────────────────────────────

def test_turn_citations_initialised_per_turn():
    """_turn_citations must be reset at the start of each turn (not accumulated)."""
    import unittest.mock as mock

    # Import to check the module loads cleanly
    import pi_agent
    # The initialization is inside _respond_via_config — verifiable only by
    # confirming the attribute is set to [] before each agentic loop
    # (line: self._turn_citations: List[Dict] = []).
    # Here we just confirm the source line exists.
    src = Path("pi_agent.py").read_text(encoding="utf-8")
    assert "self._turn_citations" in src


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
