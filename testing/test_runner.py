"""
Pi Agent Test Runner
Systematic testing framework with failure tracking
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import traceback
from datetime import datetime, timezone


class TestRunner:
    def __init__(self):
        self.results = []
        self.failures = []
        self.passed = 0
        self.failed = 0

    def run_test(self, test_func, test_name, ticket_id=None):
        """
        Run a single test and track results

        Args:
            test_func: Function to test
            test_name: Descriptive name
            ticket_id: Related ticket number (if any)
        """
        print(f"\n{'='*60}")
        print(f"Running: {test_name}")
        if ticket_id:
            print(f"Ticket: #{ticket_id}")
        print('='*60)

        try:
            result = test_func()
            if result:
                print(f"✓ PASSED: {test_name}")
                self.passed += 1
                self.results.append({
                    "test": test_name,
                    "status": "PASSED",
                    "ticket": ticket_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": None
                })
            else:
                print(f"✗ FAILED: {test_name}")
                self.failed += 1
                self.failures.append({
                    "test": test_name,
                    "ticket": ticket_id,
                    "reason": "Test returned False"
                })
                self.results.append({
                    "test": test_name,
                    "status": "FAILED",
                    "ticket": ticket_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": "Test returned False"
                })

        except Exception as e:
            print(f"✗ ERROR: {test_name}")
            print(f"   {str(e)}")
            traceback.print_exc()
            self.failed += 1
            self.failures.append({
                "test": test_name,
                "ticket": ticket_id,
                "reason": str(e),
                "traceback": traceback.format_exc()
            })
            self.results.append({
                "test": test_name,
                "status": "ERROR",
                "ticket": ticket_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
                "traceback": traceback.format_exc()
            })

    def generate_failure_tickets(self, output_file="testing/results/failure_tickets.txt"):
        """Generate failure tickets from test results"""
        if not self.failures:
            print("\n✓ No failures - no tickets to generate")
            return

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write("PI AGENT - AUTO-GENERATED FAILURE TICKETS\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
            f.write(f"Total Failures: {len(self.failures)}\n")
            f.write("=" * 80 + "\n\n")

            for idx, failure in enumerate(self.failures, 1):
                f.write(f"TICKET #{idx:03d}\n")
                f.write("-" * 80 + "\n")
                f.write(f"Test: {failure['test']}\n")
                if failure.get('ticket'):
                    f.write(f"Related Ticket: #{failure['ticket']}\n")
                f.write(f"Status: OPEN\n")
                f.write(f"Priority: P0 - CRITICAL\n")
                f.write(f"\nFAILURE REASON:\n{failure['reason']}\n")
                if 'traceback' in failure:
                    f.write(f"\nFULL TRACEBACK:\n{failure['traceback']}\n")
                f.write("\n" + "=" * 80 + "\n\n")

        print(f"\n✓ Failure tickets written to: {output_file}")

    def save_results(self, output_file="testing/results/test_results.json"):
        """Save all test results to JSON"""
        total = self.passed + self.failed
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "summary": {
                    "total": total,
                    "passed": self.passed,
                    "failed": self.failed,
                    "pass_rate": f"{(self.passed / total * 100):.1f}%" if total > 0 else "N/A"
                },
                "results": self.results
            }, f, indent=2)

        print(f"\n✓ Results saved to: {output_file}")

    def print_summary(self):
        """Print test summary"""
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print("TEST SUMMARY")
        print('='*60)
        print(f"Total: {total}")
        if total > 0:
            print(f"Passed: {self.passed} ({self.passed/total*100:.1f}%)")
            print(f"Failed: {self.failed} ({self.failed/total*100:.1f}%)")
        print('='*60)


# Singleton instance
runner = TestRunner()
