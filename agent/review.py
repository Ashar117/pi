"""Monthly self-review prompt — checks the marker, prompts Ash, records outcome."""
import json
import os
from datetime import datetime, timezone


def check_monthly_review(evolution_tracker, project_root=None):
    """Check if monthly self-review is due and prompt Ash.

    Mechanical lift from PiAgent._check_monthly_review (Phase 4) — no behaviour
    change. evolution_tracker is the EvolutionTracker instance; project_root
    defaults to the parent of this file's directory (i.e., the repo root).
    """
    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    marker_path = os.path.join(project_root, "logs", "last_review.json")
    now = datetime.now(timezone.utc)

    data = {}
    if os.path.exists(marker_path):
        with open(marker_path, 'r') as f:
            try:
                data = json.load(f)
            except Exception:
                data = {}

    def _days_since(key):
        val = data.get(key)
        if not val:
            return 9999
        return (now - datetime.fromisoformat(val)).days

    # Don't prompt if reviewed in last 30 days or declined in last 7 days
    if _days_since("last_review") <= 30:
        return
    if _days_since("last_declined") <= 7:
        return

    print("\n" + "=" * 60)
    print("  MONTHLY SELF-REVIEW DUE")
    print("=" * 60)
    response = input("Pi has been running 30+ days. Run self-review? (yes/no): ").strip().lower()

    os.makedirs(os.path.dirname(marker_path), exist_ok=True)

    if response in ['yes', 'y']:
        analysis = evolution_tracker.analyze_performance(days=30)
        if "error" not in analysis:
            improvements = evolution_tracker.identify_improvements(analysis)
            if improvements:
                print("\nImprovement opportunities identified:")
                for imp in improvements:
                    print(f"  [{imp['severity'].upper()}] {imp['issue']}")

                proposal = evolution_tracker.propose_consciousness_update(improvements)
                if proposal:
                    print(f"\nProposed consciousness update:\n{proposal}")
                    approve = input("Approve and apply? (yes/no): ").strip().lower()
                    if approve in ['yes', 'y']:
                        print("[Pi] Auto-modification not yet implemented. Manual review required.")
            else:
                print("[Pi] No improvements needed. Performance is good.")
        else:
            print(f"[Pi] {analysis['error']}")

        data["last_review"] = now.isoformat()
    else:
        data["last_declined"] = now.isoformat()

    with open(marker_path, 'w') as f:
        json.dump(data, f)
