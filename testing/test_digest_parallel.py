"""testing/test_digest_parallel.py — parallel execution in passive_daily_digest."""
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_digest_runs_skills_in_parallel(tmp_path):
    """Wall-clock time should be << sum of skill durations when parallel=True."""
    from scripts.passive import passive_daily_digest as digest
    from scripts.passive.common import Status

    call_threads = set()
    lock = threading.Lock()

    def fake_run_skill(module_name, strict, root, reports):
        with lock:
            call_threads.add(threading.current_thread().name)
        time.sleep(0.2)  # simulate slow skill
        return module_name, Status.PASS

    fake_modules = [(f"skill_{i}", f"Test Skill {i}") for i in range(8)]

    with patch.object(digest, "_run_skill", fake_run_skill), \
         patch.object(digest, "SKILL_MODULES", fake_modules), \
         patch.object(digest, "write_report", lambda *a, **kw: None):
        t0 = time.monotonic()
        digest.run_check(strict=False, root=tmp_path, reports=tmp_path, parallel=True, max_workers=4)
        elapsed = time.monotonic() - t0

    # 8 skills × 0.2s = 1.6s sequential. With 4 workers should be ~0.4-0.6s.
    assert elapsed < 1.0, f"Parallel run took {elapsed:.2f}s — not parallelised?"
    # Multiple threads must have been used
    assert len(call_threads) > 1, f"Only one thread used: {call_threads}"


def test_digest_sequential_fallback(tmp_path):
    """parallel=False should use a single thread."""
    from scripts.passive import passive_daily_digest as digest
    from scripts.passive.common import Status

    call_threads = set()

    def fake_run_skill(module_name, strict, root, reports):
        call_threads.add(threading.current_thread().name)
        return module_name, Status.PASS

    fake_modules = [(f"skill_{i}", f"Test {i}") for i in range(3)]

    with patch.object(digest, "_run_skill", fake_run_skill), \
         patch.object(digest, "SKILL_MODULES", fake_modules), \
         patch.object(digest, "write_report", lambda *a, **kw: None):
        digest.run_check(strict=False, root=tmp_path, reports=tmp_path, parallel=False)

    assert len(call_threads) == 1


def test_digest_preserves_scorecard_order(tmp_path):
    """Even with parallel execution, scorecard rows match SKILL_MODULES order."""
    from scripts.passive import passive_daily_digest as digest
    from scripts.passive.common import Status

    # Simulate skills returning in random order
    completion_order = ["skill_b", "skill_a", "skill_c"]

    def fake_run_skill(module_name, strict, root, reports):
        # Delay later-listed skills less so they complete first
        delays = {"skill_a": 0.2, "skill_b": 0.05, "skill_c": 0.1}
        time.sleep(delays.get(module_name, 0))
        return module_name, Status.PASS

    fake_modules = [("skill_a", "Skill A"), ("skill_b", "Skill B"), ("skill_c", "Skill C")]
    written: list = []

    def fake_write_report(filename, content, status):
        written.append(content)

    with patch.object(digest, "_run_skill", fake_run_skill), \
         patch.object(digest, "SKILL_MODULES", fake_modules), \
         patch.object(digest, "write_report", fake_write_report):
        digest.run_check(strict=False, root=tmp_path, reports=tmp_path, parallel=True, max_workers=3)

    content = written[0]
    # Order must follow fake_modules ordering, not completion order
    idx_a = content.find("Skill A")
    idx_b = content.find("Skill B")
    idx_c = content.find("Skill C")
    assert idx_a < idx_b < idx_c, f"Order broken: A={idx_a} B={idx_b} C={idx_c}"


def test_digest_skill_exception_does_not_abort(tmp_path):
    """One skill failing should not block others."""
    from scripts.passive import passive_daily_digest as digest
    from scripts.passive.common import Status

    def fake_run_skill(module_name, strict, root, reports):
        if module_name == "skill_b":
            raise RuntimeError("boom")
        return module_name, Status.PASS

    fake_modules = [("skill_a", "A"), ("skill_b", "B"), ("skill_c", "C")]
    written: list = []

    with patch.object(digest, "_run_skill", fake_run_skill), \
         patch.object(digest, "SKILL_MODULES", fake_modules), \
         patch.object(digest, "write_report", lambda *a, **kw: written.append(a[1])):
        status = digest.run_check(strict=False, root=tmp_path, reports=tmp_path, parallel=True)

    # b should be BLOCKED but a + c still in scorecard
    assert "A" in written[0]
    assert "C" in written[0]
