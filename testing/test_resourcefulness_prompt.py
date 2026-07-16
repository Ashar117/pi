"""testing/test_resourcefulness_prompt.py — T-233: resourcefulness principle in prompts.

prompts/consciousness*.txt are private/gitignored (the identity "recipe").
These tests skip when the real files aren't present (public/CI checkout).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent


def _read_or_skip(name: str) -> str:
    path = _ROOT / "prompts" / name
    if not path.exists():
        pytest.skip(f"prompts/{name} is private/gitignored — not present in this checkout")
    return path.read_text(encoding="utf-8")


def test_consciousness_has_resourcefulness():
    text = _read_or_skip("consciousness.txt")
    assert "RESOURCEFULNESS" in text or "resourceful" in text.lower()


def test_consciousness_normie_has_resourcefulness():
    text = _read_or_skip("consciousness_normie.txt")
    assert "resourceful" in text.lower() or "Resourcefulness" in text


def test_consciousness_resourcefulness_mentions_alternate_path():
    text = _read_or_skip("consciousness.txt")
    assert "alternate path" in text.lower() or "alternate" in text.lower()


def test_consciousness_resourcefulness_mentions_ticket():
    text = _read_or_skip("consciousness.txt")
    assert "ticket" in text.lower()


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
        import sys; sys.exit(1)
