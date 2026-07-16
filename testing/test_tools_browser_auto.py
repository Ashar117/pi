"""T-159 — tests for tools/tools_browser_auto.py (no real browser).

Asserts the graceful {success: False, error} contract when Playwright is
unavailable, so no browser is ever launched.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.tools_browser_auto as ba
from tools.tools_browser_auto import BrowserAuto


def _tool():
    return BrowserAuto()


def test_open_without_playwright():
    with patch.object(ba, "_PW_AVAILABLE", False):
        out = _tool().browser_open("https://example.com")
    assert out["success"] is False and "error" in out


def test_screenshot_without_playwright():
    with patch.object(ba, "_PW_AVAILABLE", False):
        out = _tool().browser_screenshot()
    assert out["success"] is False


def test_click_without_playwright():
    with patch.object(ba, "_PW_AVAILABLE", False):
        out = _tool().browser_click("#btn")
    assert out["success"] is False


def test_get_text_without_playwright():
    with patch.object(ba, "_PW_AVAILABLE", False):
        out = _tool().browser_get_text()
    assert out["success"] is False


def test_close_is_safe_when_nothing_open():
    # Closing with no active browser must not raise.
    out = _tool().browser_close()
    assert isinstance(out, dict)
