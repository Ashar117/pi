"""T-309: enforce, don't just claim, that l3_archive can never leak into Pi's
reasoning. Every production L3 read/dedup/contradiction path is inspected for
literal references to l3_archive -- if a future edit adds one, this fails.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import MemoryTools

_PRODUCTION_READ_METHODS = [
    "_hybrid_search_l3",
    "get_l3_context",
    "_is_l3_duplicate",
    "_check_l3_overlap_and_conflict",
    "_l3_active_records",
    "_search_l3_cache",
]


def test_no_production_read_path_references_l3_archive():
    offenders = []
    for name in _PRODUCTION_READ_METHODS:
        source = inspect.getsource(getattr(MemoryTools, name))
        if "l3_archive" in source:
            offenders.append(name)
    assert not offenders, f"l3_archive referenced in production read path(s): {offenders}"


if __name__ == "__main__":
    test_no_production_read_path_references_l3_archive()
    print("OK")
