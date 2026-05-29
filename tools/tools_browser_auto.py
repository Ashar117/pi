"""tools/tools_browser_auto.py — Browser automation via Playwright (T-060).

Wraps Playwright's sync API as Pi tools. A single browser instance is kept
alive across calls (lazy-started, explicit close on session exit).

Install:
    pip install playwright
    playwright install chromium

Tools exposed:
    browser_open        — navigate to URL, return title + text preview
    browser_screenshot  — capture current page as PNG, return file path
    browser_click       — click an element by CSS selector or visible text
    browser_fill        — type into a form field
    browser_get_text    — extract text from page or element
    browser_evaluate    — run arbitrary JavaScript on the page
    browser_close       — close the browser
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

try:
    from playwright.sync_api import sync_playwright, Browser, Page, Playwright
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False

_SCREENSHOT_DIR = Path(__file__).parent.parent / "data" / "screenshots"
_DEFAULT_TIMEOUT = 15_000   # ms


class BrowserAuto:
    """Persistent Playwright browser session shared across tool calls."""

    def __init__(self) -> None:
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def _ensure_open(self) -> Page:
        """Lazily start Playwright + Chromium and return the active page."""
        if not _PW_AVAILABLE:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )
        if self._pw is None:
            self._pw = sync_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = self._pw.chromium.launch(headless=True)
        if self._page is None or self._page.is_closed():
            self._page = self._browser.new_page()
            self._page.set_default_timeout(_DEFAULT_TIMEOUT)
        return self._page

    def browser_close(self) -> Dict:
        """Close the browser and release all resources."""
        try:
            if self._page and not self._page.is_closed():
                self._page.close()
            if self._browser and self._browser.is_connected():
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self._page = None
            self._browser = None
            self._pw = None
        return {"success": True, "message": "Browser closed"}

    # ── Tools ──────────────────────────────────────────────────────────────

    def browser_open(self, url: str, wait_until: str = "domcontentloaded") -> Dict:
        """Navigate to URL. Returns title and a text preview of visible content.

        Args:
            url:        Full URL (must include http:// or https://)
            wait_until: 'load' | 'domcontentloaded' | 'networkidle' (default: domcontentloaded)

        Returns:
            {"success": bool, "url": str, "title": str, "text_preview": str}
        """
        try:
            page = self._ensure_open()
            page.goto(url, wait_until=wait_until)
            title = page.title()
            # Extract visible body text, cap at 2000 chars
            text = page.evaluate("""() => {
                const el = document.body;
                return el ? el.innerText.slice(0, 2000) : '';
            }""")
            return {
                "success": True,
                "url": page.url,
                "title": title,
                "text_preview": (text or "").strip(),
            }
        except Exception as e:
            return {"success": False, "error": str(e), "url": url}

    def browser_screenshot(self, save_path: Optional[str] = None) -> Dict:
        """Capture the current page as a PNG file.

        Args:
            save_path: Optional absolute path. Auto-named in data/screenshots/ if omitted.

        Returns:
            {"success": bool, "path": str, "url": str}
        """
        try:
            page = self._ensure_open()
            if save_path:
                out = Path(save_path)
            else:
                _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                out = _SCREENSHOT_DIR / f"browser_{ts}_{uuid.uuid4().hex[:6]}.png"
            page.screenshot(path=str(out), full_page=False)
            return {"success": True, "path": str(out), "url": page.url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def browser_click(self, selector: str, by_text: bool = False) -> Dict:
        """Click an element.

        Args:
            selector: CSS selector (e.g. '#submit') or visible text when by_text=True
            by_text:  If True, click by visible text content instead of CSS selector

        Returns:
            {"success": bool, "selector": str}
        """
        try:
            page = self._ensure_open()
            if by_text:
                page.get_by_text(selector, exact=False).first.click()
            else:
                page.click(selector)
            return {"success": True, "selector": selector}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    def browser_fill(self, selector: str, value: str) -> Dict:
        """Fill a form field and optionally press Enter.

        Args:
            selector: CSS selector for the input/textarea
            value:    Text to type into the field

        Returns:
            {"success": bool, "selector": str}
        """
        try:
            page = self._ensure_open()
            page.fill(selector, value)
            return {"success": True, "selector": selector}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    def browser_get_text(
        self,
        selector: Optional[str] = None,
        max_chars: int = 8000,
    ) -> Dict:
        """Extract visible text from the page or a specific element.

        Args:
            selector:  CSS selector for a specific element; None = full page body
            max_chars: Max characters to return (default 8000)

        Returns:
            {"success": bool, "text": str, "url": str, "chars": int}
        """
        try:
            page = self._ensure_open()
            if selector:
                text = page.locator(selector).first.inner_text()
            else:
                text = page.evaluate("() => document.body.innerText")
            text = (text or "").strip()[:max_chars]
            return {
                "success": True,
                "text": text,
                "url": page.url,
                "chars": len(text),
            }
        except Exception as e:
            return {"success": False, "error": str(e), "text": ""}

    def browser_evaluate(self, js: str) -> Dict:
        """Run arbitrary JavaScript on the current page.

        Args:
            js: JS expression to evaluate (e.g. 'document.title')

        Returns:
            {"success": bool, "result": any}
        """
        try:
            page = self._ensure_open()
            result = page.evaluate(js)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e), "result": None}

    def browser_wait(self, selector: str, timeout: int = 10000) -> Dict:
        """Wait for an element to appear on the page.

        Args:
            selector: CSS selector to wait for
            timeout:  Max wait in ms (default 10000)

        Returns:
            {"success": bool, "selector": str}
        """
        try:
            page = self._ensure_open()
            page.wait_for_selector(selector, timeout=timeout)
            return {"success": True, "selector": selector}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    @property
    def is_open(self) -> bool:
        return (
            self._page is not None
            and not self._page.is_closed()
            and self._browser is not None
            and self._browser.is_connected()
        )


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402

_browser_inst: "BrowserAuto | None" = None


def _b():
    """Lazy singleton for BrowserAuto — Playwright import is expensive."""
    global _browser_inst
    if _browser_inst is None:
        _browser_inst = BrowserAuto()
    return _browser_inst


def _handle_browser_open(agent, tool_input, *, memory_override=None):
    return _b().browser_open(
        url=tool_input["url"],
        wait_until=tool_input.get("wait_until", "domcontentloaded"),
    )


def _handle_browser_screenshot(agent, tool_input, *, memory_override=None):
    return _b().browser_screenshot(save_path=tool_input.get("save_path"))


def _handle_browser_click(agent, tool_input, *, memory_override=None):
    return _b().browser_click(
        selector=tool_input["selector"],
        by_text=tool_input.get("by_text", False),
    )


def _handle_browser_fill(agent, tool_input, *, memory_override=None):
    return _b().browser_fill(
        selector=tool_input["selector"],
        value=tool_input["value"],
    )


def _handle_browser_get_text(agent, tool_input, *, memory_override=None):
    return _b().browser_get_text(
        selector=tool_input.get("selector"),
        max_chars=tool_input.get("max_chars", 8000),
    )


def _handle_browser_close(agent, tool_input, *, memory_override=None):
    return _b().browser_close()


def _handle_browser_evaluate(agent, tool_input, *, memory_override=None):
    return _b().browser_evaluate(tool_input["js"])


def _handle_browser_wait(agent, tool_input, *, memory_override=None):
    return _b().browser_wait(
        selector=tool_input["selector"],
        timeout=tool_input.get("timeout", 10000),
    )


TOOLS = [
    ToolSpec(
        name="browser_open",
        description=(
            "Open a URL in a headless Chromium browser. Returns page title and text "
            "preview. Use for web tasks that require JS rendering, login forms, or "
            "dynamic content that web_browse/web_search can't handle."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url":        {"type": "string", "description": "Full URL including https://"},
                "wait_until": {"type": "string",
                               "enum": ["load", "domcontentloaded", "networkidle"],
                               "default": "domcontentloaded"},
            },
            "required": ["url"],
        },
        handler=_handle_browser_open,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="browser_screenshot",
        description="Capture a screenshot of the current browser page. Returns the saved PNG path.",
        input_schema={
            "type": "object",
            "properties": {
                "save_path": {"type": "string",
                              "description": "Where to save the PNG. Omit for auto temp path."},
            },
            "required": [],
        },
        handler=_handle_browser_screenshot,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="browser_click",
        description="Click an element by CSS selector. Set by_text=true to click by visible text instead.",
        input_schema={
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "by_text":  {"type": "boolean", "default": False,
                             "description": "Treat selector as visible-text string"},
            },
            "required": ["selector"],
        },
        handler=_handle_browser_click,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="browser_fill",
        description="Type a value into a form field selected by CSS.",
        input_schema={
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "value":    {"type": "string"},
            },
            "required": ["selector", "value"],
        },
        handler=_handle_browser_fill,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="browser_get_text",
        description="Extract text from the current page or a specific element.",
        input_schema={
            "type": "object",
            "properties": {
                "selector":  {"type": "string", "description": "CSS selector. Omit for whole page."},
                "max_chars": {"type": "integer", "default": 8000},
            },
            "required": [],
        },
        handler=_handle_browser_get_text,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="browser_close",
        description="Close the browser instance. Useful to free resources after a session.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_handle_browser_close,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="browser_evaluate",
        description="Run a JavaScript expression in the current page context and return its value.",
        input_schema={
            "type": "object",
            "properties": {"js": {"type": "string"}},
            "required": ["js"],
        },
        handler=_handle_browser_evaluate,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="browser_wait",
        description="Wait for an element matching the CSS selector to appear. Timeout in ms.",
        input_schema={
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "timeout":  {"type": "integer", "default": 10000},
            },
            "required": ["selector"],
        },
        handler=_handle_browser_wait,
        success_predicate=lambda r: r.get("success", False),
    ),
]
