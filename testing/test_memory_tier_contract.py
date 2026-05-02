"""
testing/test_memory_tier_contract.py

T-017 reproduction + regression test:

The docstring of MemoryTools.memory_read promises: "tier: l1/l2/l3 or None for all".
The code at tools_memory.py:96 reads `if tier == "l1":` (exclusive). With tier=None,
L1 is silently skipped. Pi reasons about its capabilities from the contracts; the
contract drift directly causes wrong self-descriptions.

Master prompt §6 Phase 3 specifies the **conservative fix**: correct the docstring
to match reality ("tier=None searches L3+L2 only; use tier='l1' explicitly for L1
archive"). The aggressive fix (include L1 implicitly) is deferred to a future phase
when L1 has a faster query layer.

This test asserts the post-fix docstring contract — that what `memory_read` *says*
it does matches what it *does*. It does not require any code-behaviour change beyond
docstring text.

PRE-FIX: this test fails because the docstring still says "None for all".
POST-FIX: this test passes because the docstring now matches the code.

The test does NOT touch Supabase, Claude, or any paid API. Free to run anytime.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools  # noqa: E402


def test_docstring_does_not_promise_l1_with_tier_none():
    """
    The docstring must NOT say 'None for all' or 'None searches all tiers' —
    that promise contradicts the code at tools_memory.py:96.
    """
    doc = (MemoryTools.memory_read.__doc__ or "").lower()
    assert doc, "memory_read has no docstring"

    bad_phrases = [
        "none for all",          # what the pre-fix docstring said
        "none searches all",
        "none = all",
    ]
    for phrase in bad_phrases:
        assert phrase not in doc, (
            f"docstring still contains misleading phrase '{phrase}'. "
            f"Code at tools_memory.py excludes L1 from tier=None searches; "
            f"the docstring must match. Current docstring:\n{doc}"
        )


def test_docstring_explicitly_states_l1_is_opt_in():
    """
    The corrected docstring should explicitly tell the caller (and Pi reasoning
    from the contract) that L1 only fires when tier='l1' is passed explicitly.
    """
    doc = (MemoryTools.memory_read.__doc__ or "").lower()
    # Accept any of several reasonable phrasings — the point is the contract is honest
    indicators = [
        ("l1" in doc and "explicit" in doc),
        ("l3+l2" in doc) or ("l3 + l2" in doc) or ("l3 and l2" in doc),
        ("opt-in" in doc) or ("opt in" in doc),
        ("excludes l1" in doc) or ("does not include l1" in doc),
    ]
    assert any(indicators), (
        "docstring should explicitly state that tier=None excludes L1, "
        "or that L1 is opt-in via tier='l1'. Current:\n" + doc
    )


def test_code_behaviour_unchanged_l1_still_excluded_with_none():
    """
    The conservative fix is docstring-only. We are NOT changing code behaviour
    in this phase. Confirm by inspecting the source: the L1 branch is gated on
    `if tier == "l1":` (exclusive), not `if tier in ("l1", None):`.
    """
    src = inspect.getsource(MemoryTools.memory_read)
    # Aggressive-fix pattern (what we are NOT doing in this phase):
    aggressive = ('tier in ("l1", none)' in src.lower()) or ('tier == "l1" or tier is none' in src.lower())
    assert not aggressive, (
        "code appears to have taken the aggressive fix (include L1 with tier=None). "
        "Per master prompt §6 Phase 3.3, conservative fix only this phase."
    )
    # Conservative path: the original L1 gate must still be present
    assert 'tier == "l1"' in src, (
        "expected the original `if tier == \"l1\":` gate to remain. "
        "Got source:\n" + src
    )


def main():
    tests = [
        ("docstring no longer claims 'None for all'", test_docstring_does_not_promise_l1_with_tier_none),
        ("docstring explicitly marks L1 as opt-in",   test_docstring_explicitly_states_l1_is_opt_in),
        ("code behaviour unchanged (L1 still gated)", test_code_behaviour_unchanged_l1_still_excluded_with_none),
    ]
    print("\n=== test_memory_tier_contract.py ===\n")
    failed = []
    for name, fn in tests:
        print(f"[*] {name} ...")
        try:
            fn()
            print(f"    PASSED\n")
        except AssertionError as e:
            print(f"    FAILED: {str(e)[:300]}\n")
            failed.append(name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            failed.append(name)
    print("=" * 60)
    if failed:
        print(f"{len(failed)}/{len(tests)} failed: {failed}")
        sys.exit(1)
    print(f"All {len(tests)} tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
