"""testing/test_profile_aware_prompt.py — T-281: profile-aware answering + correction capture in consciousness.txt."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def _text():
    return (_ROOT / "prompts" / "consciousness.txt").read_text(encoding="utf-8")


def test_personalized_recommendation_rule_present():
    text = _text()
    assert "Personalized Recommendations Must Apply Profile Facts" in text


def test_personalized_rule_names_trigger_phrases():
    text = _text()
    assert '"for me"' in text and '"am I eligible"' in text


def test_personalized_rule_requires_stating_constraints():
    text = _text()
    assert "filtering on" in text.lower()


def test_correction_rule_requires_memory_write_before_reply():
    text = _text()
    section = text[text.index("When Ash Corrects You"):]
    assert "memory_write" in section
    assert "BEFORE replying" in section


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
