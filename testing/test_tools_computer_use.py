"""T-159 — tests for tools/tools_computer_use.py (no real input control).

Asserts the contract that every primitive returns {success: False} (not a
raise, not an actual click) when pyautogui is unavailable. We force the
unavailable path so no real mouse/keyboard events are ever generated.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.tools_computer_use as cu
from tools.tools_computer_use import ComputerUseTools


def _tool():
    return ComputerUseTools()


def test_click_without_pyautogui():
    with patch.object(cu, "_PYAUTOGUI", False):
        out = _tool().computer_click(10, 20)
    assert out["success"] is False and "pyautogui" in out["error"].lower()


def test_double_click_without_pyautogui():
    with patch.object(cu, "_PYAUTOGUI", False):
        out = _tool().computer_double_click(10, 20)
    assert out["success"] is False


def test_type_without_pyautogui():
    with patch.object(cu, "_PYAUTOGUI", False):
        out = _tool().computer_type("hello")
    assert out["success"] is False


def test_key_without_pyautogui():
    with patch.object(cu, "_PYAUTOGUI", False):
        out = _tool().computer_key("enter")
    assert out["success"] is False


def test_scroll_without_pyautogui():
    with patch.object(cu, "_PYAUTOGUI", False):
        out = _tool().computer_scroll(x=5, y=5, direction="down", clicks=3)
    assert out["success"] is False


def test_move_without_pyautogui():
    with patch.object(cu, "_PYAUTOGUI", False):
        out = _tool().computer_move(5, 5)
    assert out["success"] is False
