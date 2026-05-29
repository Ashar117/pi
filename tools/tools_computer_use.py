"""tools/tools_computer_use.py — Desktop control via Anthropic Computer Use API.

Two layers:
  1. Low-level primitives (pyautogui + mss):
       computer_screenshot, computer_click, computer_double_click,
       computer_type, computer_key, computer_scroll, computer_move

  2. High-level agentic task runner (computer_run_task):
       Sends a screenshot to Claude with computer_use beta enabled.
       Claude responds with actions; we execute them and loop until done
       or max_steps reached.

Install:
    pip install pyautogui mss

Notes:
  - computer_run_task requires ANTHROPIC_API_KEY in .env
  - All click/type operations act on the real desktop — confirm before use
  - Works on Windows (pyautogui has Windows support)
"""
from __future__ import annotations

import base64
import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pyautogui
    import pyautogui as _pag
    _PYAUTOGUI = True
    pyautogui.FAILSAFE = True   # move mouse to top-left corner to abort
    pyautogui.PAUSE = 0.05      # slight pause between actions
except ImportError:
    _PYAUTOGUI = False

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

_SCREENSHOT_DIR = Path(__file__).parent.parent / "data" / "screenshots"
_MAX_STEPS = 20   # safety cap for agentic loop


# ── Screenshot helper ──────────────────────────────────────────────────────────

def _take_screenshot(save_path: Optional[str] = None) -> str:
    """Take a full-screen screenshot. Returns the file path."""
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    if save_path:
        out = save_path
    else:
        import uuid
        out = str(_SCREENSHOT_DIR / f"cu_{ts}_{uuid.uuid4().hex[:6]}.png")

    if _MSS:
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            img = sct.grab(monitor)
            mss.tools.to_png(img.rgb, img.size, output=out)
    elif _PYAUTOGUI:
        _pag.screenshot(out)
    else:
        raise RuntimeError("Neither mss nor pyautogui is installed")

    return out


def _screenshot_base64() -> str:
    """Return current screen as base64 PNG string (for Anthropic API)."""
    if _MSS:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
            buf = io.BytesIO()
            mss.tools.to_png(img.rgb, img.size, output=buf)
            return base64.standard_b64encode(buf.getvalue()).decode()
    elif _PYAUTOGUI:
        img = _pag.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.standard_b64encode(buf.getvalue()).decode()
    raise RuntimeError("Neither mss nor pyautogui is installed")


# ── Low-level primitives ───────────────────────────────────────────────────────

class ComputerUseTools:
    """Low-level desktop control + high-level Anthropic agentic loop."""

    def computer_screenshot(self, save_path: Optional[str] = None) -> Dict:
        """Take a full-screen screenshot and save it.

        Returns:
            {"success": bool, "path": str}
        """
        try:
            out = _take_screenshot(save_path)
            return {"success": True, "path": out}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def computer_click(self, x: int, y: int, button: str = "left") -> Dict:
        """Click at screen coordinates (x, y).

        Args:
            x, y:   Screen coordinates in pixels
            button: 'left' | 'right' | 'middle'

        Returns:
            {"success": bool, "x": int, "y": int}
        """
        if not _PYAUTOGUI:
            return {"success": False, "error": "pyautogui not installed"}
        try:
            _pag.click(x, y, button=button)
            return {"success": True, "x": x, "y": y, "button": button}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def computer_double_click(self, x: int, y: int) -> Dict:
        """Double-click at screen coordinates."""
        if not _PYAUTOGUI:
            return {"success": False, "error": "pyautogui not installed"}
        try:
            _pag.doubleClick(x, y)
            return {"success": True, "x": x, "y": y}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def computer_type(self, text: str, interval: float = 0.03) -> Dict:
        """Type text at the current cursor position.

        Args:
            text:     Text to type (supports Unicode)
            interval: Seconds between keystrokes (default 0.03)

        Returns:
            {"success": bool, "chars": int}
        """
        if not _PYAUTOGUI:
            return {"success": False, "error": "pyautogui not installed"}
        try:
            _pag.typewrite(text, interval=interval)
            return {"success": True, "chars": len(text)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def computer_key(self, key: str) -> Dict:
        """Press a keyboard shortcut.

        Args:
            key: Key name or combo, e.g. 'enter', 'ctrl+c', 'alt+tab', 'win'
                 For combos, use '+' separator: 'ctrl+shift+t'

        Returns:
            {"success": bool, "key": str}
        """
        if not _PYAUTOGUI:
            return {"success": False, "error": "pyautogui not installed"}
        try:
            parts = [k.strip() for k in key.split("+")]
            if len(parts) == 1:
                _pag.press(parts[0])
            else:
                _pag.hotkey(*parts)
            return {"success": True, "key": key}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def computer_scroll(
        self, x: int, y: int, direction: str = "down", clicks: int = 3
    ) -> Dict:
        """Scroll at screen coordinates.

        Args:
            x, y:      Screen coordinates to scroll at
            direction: 'up' | 'down'
            clicks:    Number of scroll clicks

        Returns:
            {"success": bool}
        """
        if not _PYAUTOGUI:
            return {"success": False, "error": "pyautogui not installed"}
        try:
            amount = clicks if direction == "up" else -clicks
            _pag.scroll(amount, x=x, y=y)
            return {"success": True, "direction": direction, "clicks": clicks}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def computer_move(self, x: int, y: int, duration: float = 0.2) -> Dict:
        """Move the mouse to (x, y) without clicking.

        Args:
            x, y:     Target coordinates
            duration: Move duration in seconds (smooth movement)
        """
        if not _PYAUTOGUI:
            return {"success": False, "error": "pyautogui not installed"}
        try:
            _pag.moveTo(x, y, duration=duration)
            return {"success": True, "x": x, "y": y}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── High-level agentic loop ────────────────────────────────────────────

    def computer_run_task(
        self,
        task: str,
        max_steps: int = 10,
        anthropic_key: str = "",
    ) -> Dict:
        """Run a natural-language desktop task via Anthropic Computer Use API.

        Takes a screenshot, sends it to Claude with the task description and
        computer_use tools enabled, then executes the returned actions in a
        loop until Claude reports completion or max_steps is reached.

        Args:
            task:          What to do, e.g. "Open Notepad and type Hello World"
            max_steps:     Safety cap on action steps (default 10, max 20)
            anthropic_key: Override API key (uses ANTHROPIC_API_KEY env var by default)

        Returns:
            {"success": bool, "steps": int, "result": str, "screenshots": [str]}
        """
        if not (_PYAUTOGUI or _MSS):
            return {"success": False, "error": "pyautogui or mss required"}

        try:
            import anthropic as _anthropic
        except ImportError:
            return {"success": False, "error": "anthropic package not installed"}

        api_key = anthropic_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return {"success": False, "error": "ANTHROPIC_API_KEY not set"}

        client = _anthropic.Anthropic(api_key=api_key)
        max_steps = min(max_steps, _MAX_STEPS)
        screenshots: List[str] = []
        messages: List[Dict] = []
        steps = 0

        # Computer use tool definition (Anthropic beta spec)
        cu_tool = {
            "type": "computer_20241022",
            "name": "computer",
            "display_width_px": _pag.size()[0] if _PYAUTOGUI else 1920,
            "display_height_px": _pag.size()[1] if _PYAUTOGUI else 1080,
            "display_number": 1,
        }

        # Initial screenshot
        img_b64 = _screenshot_base64()
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "init",
                    "content": [{"type": "image", "source": {"type": "base64",
                                  "media_type": "image/png", "data": img_b64}}],
                },
                {"type": "text", "text": f"Task: {task}\n\nA screenshot of the current desktop is attached. Please complete the task."},
            ],
        })

        result_text = ""

        while steps < max_steps:
            try:
                resp = client.beta.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=1024,
                    tools=[cu_tool],
                    messages=messages,
                    betas=["computer-use-2024-10-22"],
                )
            except Exception as e:
                return {"success": False, "error": f"Anthropic API error: {e}",
                        "steps": steps, "screenshots": screenshots}

            # Collect text output
            for block in resp.content:
                if hasattr(block, "text"):
                    result_text = block.text

            # Check stop condition
            if resp.stop_reason == "end_turn":
                break

            # Execute computer actions
            tool_results = []
            for block in resp.content:
                if block.type != "tool_use" or block.name != "computer":
                    continue
                action = block.input.get("action", "")
                steps += 1

                try:
                    if action == "screenshot":
                        img_b64 = _screenshot_base64()
                        ss_path = _take_screenshot()
                        screenshots.append(ss_path)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "image", "source": {
                                "type": "base64", "media_type": "image/png",
                                "data": img_b64,
                            }}],
                        })

                    elif action in ("left_click", "right_click", "middle_click"):
                        coords = block.input.get("coordinate", [0, 0])
                        btn = action.replace("_click", "")
                        self.computer_click(coords[0], coords[1], button=btn)
                        time.sleep(0.3)
                        img_b64 = _screenshot_base64()
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "image", "source": {
                                "type": "base64", "media_type": "image/png",
                                "data": img_b64,
                            }}],
                        })

                    elif action == "double_click":
                        coords = block.input.get("coordinate", [0, 0])
                        self.computer_double_click(coords[0], coords[1])
                        time.sleep(0.3)
                        img_b64 = _screenshot_base64()
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": [{"type": "image", "source": {
                                "type": "base64", "media_type": "image/png",
                                "data": img_b64,
                            }}],
                        })

                    elif action == "type":
                        text = block.input.get("text", "")
                        self.computer_type(text)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Typed: {text[:50]}",
                        })

                    elif action == "key":
                        key = block.input.get("text", "")
                        self.computer_key(key)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Key: {key}",
                        })

                    elif action == "scroll":
                        coords = block.input.get("coordinate", [0, 0])
                        direction = block.input.get("direction", "down")
                        amount = block.input.get("amount", 3)
                        self.computer_scroll(coords[0], coords[1], direction, amount)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Scrolled {direction}",
                        })

                    elif action == "mouse_move":
                        coords = block.input.get("coordinate", [0, 0])
                        self.computer_move(coords[0], coords[1])
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "Mouse moved",
                        })

                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Unknown action: {action}",
                        })

                except Exception as e:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": True,
                        "content": str(e),
                    })

            # Append assistant message + tool results to conversation
            messages.append({"role": "assistant", "content": resp.content})
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            else:
                break  # No tool calls and not end_turn → safety exit

        return {
            "success": True,
            "steps": steps,
            "result": result_text or f"Completed {steps} steps",
            "screenshots": screenshots,
        }


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402

_cu_inst: "ComputerUseTools | None" = None


def _cu():
    """Lazy singleton for ComputerUseTools — pyautogui/mss imports are expensive."""
    global _cu_inst
    if _cu_inst is None:
        _cu_inst = ComputerUseTools()
    return _cu_inst


def _handle_computer_screenshot(agent, tool_input, *, memory_override=None):
    return _cu().computer_screenshot()


def _handle_computer_click(agent, tool_input, *, memory_override=None):
    return _cu().computer_click(
        x=tool_input["x"],
        y=tool_input["y"],
        button=tool_input.get("button", "left"),
    )


def _handle_computer_type(agent, tool_input, *, memory_override=None):
    return _cu().computer_type(text=tool_input["text"])


def _handle_computer_key(agent, tool_input, *, memory_override=None):
    return _cu().computer_key(key=tool_input["key"])


def _handle_computer_scroll(agent, tool_input, *, memory_override=None):
    return _cu().computer_scroll(
        x=tool_input["x"],
        y=tool_input["y"],
        direction=tool_input.get("direction", "down"),
        clicks=tool_input.get("clicks", 3),
    )


def _handle_computer_run_task(agent, tool_input, *, memory_override=None):
    return _cu().computer_run_task(
        task=tool_input["task"],
        max_steps=min(tool_input.get("max_steps", 10), 20),
        anthropic_key=os.getenv("ANTHROPIC_API_KEY", ""),
    )


TOOLS = [
    ToolSpec(
        name="computer_screenshot",
        description="Take a screenshot of Ash's desktop. Returns the saved PNG path.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_handle_computer_screenshot,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="computer_click",
        description="Click at (x, y) on Ash's desktop. Button: 'left' (default), 'right', or 'middle'.",
        input_schema={
            "type": "object",
            "properties": {
                "x":      {"type": "integer"},
                "y":      {"type": "integer"},
                "button": {"type": "string", "enum": ["left","right","middle"], "default": "left"},
            },
            "required": ["x", "y"],
        },
        handler=_handle_computer_click,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="computer_type",
        description="Type text into the currently focused field on Ash's desktop.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=_handle_computer_type,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="computer_key",
        description="Press a single key or key combination (e.g. 'enter', 'ctrl+c', 'tab').",
        input_schema={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
        handler=_handle_computer_key,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="computer_scroll",
        description="Scroll at (x, y). Direction: 'up'/'down'/'left'/'right'. Clicks = scroll units.",
        input_schema={
            "type": "object",
            "properties": {
                "x":         {"type": "integer"},
                "y":         {"type": "integer"},
                "direction": {"type": "string", "enum": ["up","down","left","right"], "default": "down"},
                "clicks":    {"type": "integer", "default": 3},
            },
            "required": ["x", "y"],
        },
        handler=_handle_computer_scroll,
        success_predicate=lambda r: r.get("success", False),
    ),
    # T-083 R2.2 Merger D: computer_run_task removed from tool registry.
    # It is a recursive agent, not a tool. Use scripts/sprint.py instead.
]
