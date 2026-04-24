"""
Master Test Runner
Runs all test suites in sequence and generates master report
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import subprocess
from datetime import datetime, timezone

TESTING_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(TESTING_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


def run_test_suite(test_file, suite_name):
    """Run a test suite via subprocess and return (success, output)"""
    print(f"\n{'='*60}")
    print(f"RUNNING: {suite_name}")
    print('='*60)

    result = subprocess.run(
        [sys.executable, test_file],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(TESTING_DIR)
    )

    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[:500])

    return result.returncode == 0, result.stdout, result.stderr


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("PI AGENT - COMPLETE TEST SUITE")
    print(f"Run time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 80)

    suites = [
        (os.path.join(TESTING_DIR, "test_requirements.py"), "Requirements Check"),
        (os.path.join(TESTING_DIR, "test_memory.py"), "Memory System Tests (Ticket #001)"),
        (os.path.join(TESTING_DIR, "test_persistence.py"), "Session Persistence Tests (Ticket #002)"),
        (os.path.join(TESTING_DIR, "test_modes.py"), "Mode Switching Tests (Ticket #003)"),
        (os.path.join(TESTING_DIR, "test_integration.py"), "Integration Tests"),
    ]

    all_results = []
    for test_file, suite_name in suites:
        success, stdout, stderr = run_test_suite(test_file, suite_name)
        all_results.append({
            "suite": suite_name,
            "file": test_file,
            "passed": success,
            "stdout": stdout,
            "stderr": stderr
        })

    # Print overall summary
    print("\n" + "=" * 80)
    print("OVERALL RESULTS")
    print("=" * 80)
    passed_count = sum(1 for r in all_results if r["passed"])
    for r in all_results:
        symbol = "✓" if r["passed"] else "✗"
        status = "PASSED" if r["passed"] else "FAILED"
        print(f"{symbol} {r['suite']}: {status}")

    print(f"\nSuites Passed: {passed_count}/{len(all_results)}")

    # Write master results
    master_results_path = os.path.join(RESULTS_DIR, "MASTER_RESULTS.json")
    with open(master_results_path, 'w') as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_suites": len(all_results),
                "suites_passed": passed_count,
                "suites_failed": len(all_results) - passed_count
            },
            "suites": [
                {"suite": r["suite"], "passed": r["passed"], "stderr_preview": r["stderr"][:200]}
                for r in all_results
            ]
        }, f, indent=2)
    print(f"\n✓ Master results: {master_results_path}")

    # Write master failure report
    failures = [r for r in all_results if not r["passed"]]
    if failures:
        master_failures_path = os.path.join(RESULTS_DIR, "MASTER_FAILURES.txt")
        with open(master_failures_path, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write("PI AGENT - MASTER FAILURE REPORT\n")
            f.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"Failed Suites: {len(failures)}/{len(all_results)}\n")
            f.write("=" * 80 + "\n\n")
            for r in failures:
                f.write(f"FAILED: {r['suite']}\n")
                f.write(f"File: {r['file']}\n")
                f.write(f"Output:\n{r['stdout']}\n")
                if r['stderr']:
                    f.write(f"Errors:\n{r['stderr']}\n")
                f.write("\n" + "-" * 80 + "\n\n")
        print(f"✓ Master failures: {master_failures_path}")

    print("\n✓ All test suites complete")
    sys.exit(0 if passed_count == len(all_results) else 1)
