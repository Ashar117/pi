"""testing/test_resourcefulness_prompt.py — T-233: resourcefulness principle in prompts."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def test_consciousness_has_resourcefulness():
    text = (_ROOT / "prompts" / "consciousness.txt").read_text(encoding="utf-8")
    assert "RESOURCEFULNESS" in text or "resourceful" in text.lower()


def test_consciousness_normie_has_resourcefulness():
    text = (_ROOT / "prompts" / "consciousness_normie.txt").read_text(encoding="utf-8")
    assert "resourceful" in text.lower() or "Resourcefulness" in text


def test_consciousness_resourcefulness_mentions_alternate_path():
    text = (_ROOT / "prompts" / "consciousness.txt").read_text(encoding="utf-8")
    assert "alternate path" in text.lower() or "alternate" in text.lower()


def test_consciousness_resourcefulness_mentions_ticket():
    text = (_ROOT / "prompts" / "consciousness.txt").read_text(encoding="utf-8")
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
