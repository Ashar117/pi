"""T-159 — tests for tools/tools_briefing.py (no network).

Builds BriefingTools with fake awareness/memory and asserts generate() returns
a well-formed markdown briefing, degrades gracefully when every source fails,
and (regression) does not use the Windows-incompatible %-d strftime code.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_briefing import BriefingTools


class _FakeAwareness:
    def __init__(self, loc=None):
        self._loc = loc or {}
    def get_location(self):
        return self._loc
    def get_weather(self, force=False):
        return {"success": False}
    def get_news(self, category="global", count=5, force=False):
        return {"success": False}
    def get_stocks(self, force=False):
        return {"success": False}
    def get_tech_updates(self, count=4, force=False):
        return {"success": False}


class _FakeMemory:
    def __init__(self, l3=""):
        self._l3 = l3
    def get_l3_context(self, max_tokens=300):
        return self._l3


def test_generate_returns_markdown_header():
    b = BriefingTools(awareness=_FakeAwareness(), memory=_FakeMemory())
    out = b.generate(save_to_obsidian=False)
    assert isinstance(out, str)
    assert out.startswith("# Daily Briefing")


def test_generate_no_crash_when_all_sources_fail():
    """Every awareness source returns failure → still a valid (header-only) brief."""
    b = BriefingTools(awareness=_FakeAwareness(), memory=_FakeMemory())
    out = b.generate(save_to_obsidian=False)
    assert "Daily Briefing" in out
    # none of the optional sections should appear
    for sect in ("## Weather", "## World", "## Markets"):
        assert sect not in out


def test_generate_includes_location_when_present():
    b = BriefingTools(awareness=_FakeAwareness(loc={"city": "Atlanta", "country": "US"}),
                      memory=_FakeMemory())
    out = b.generate(save_to_obsidian=False)
    assert "Atlanta" in out


def test_generate_includes_l3_active_context():
    b = BriefingTools(awareness=_FakeAwareness(),
                      memory=_FakeMemory(l3="=== ACTIVE CONTEXT ===\nAsh ships Pi"))
    out = b.generate(save_to_obsidian=False)
    assert "Active Context" in out and "Ash ships Pi" in out


def test_generate_date_is_platform_safe():
    """Regression for the %-d (Linux-only) bug — must not raise on Windows."""
    b = BriefingTools(awareness=_FakeAwareness(), memory=_FakeMemory())
    out = b.generate(save_to_obsidian=False)  # would ValueError on Windows pre-fix
    import re
    # header carries a 'Weekday, Month D, YYYY' date with no leading-zero artifact
    assert re.search(r"# Daily Briefing — \w+, \w+ \d{1,2}, \d{4}", out)


# ── T-160: CNBC-led business/markets news in the briefing ──────────────────────

class _BizAwareness(_FakeAwareness):
    def get_news(self, category="global", count=5, force=False):
        if category == "business":
            return {"success": True, "items": [
                {"title": "Fed holds rates steady", "url": "https://cnbc.com/x"},
                {"title": "Nvidia earnings beat", "url": "https://cnbc.com/y"},
            ]}
        return {"success": False}


def test_briefing_includes_business_section():
    b = BriefingTools(awareness=_BizAwareness(), memory=_FakeMemory())
    out = b.generate(save_to_obsidian=False)
    assert "## Business" in out
    assert "Fed holds rates steady" in out


def test_business_feed_includes_cnbc():
    import tools.tools_awareness as aw
    feeds = aw._NEWS_FEEDS["business"]
    assert any("cnbc.com" in f for f in feeds), "CNBC not in business feeds"
    # CNBC should lead (markets-focused primary)
    assert "cnbc.com" in feeds[0]
