"""Startup health check — verifies Supabase, SQLite, and required API keys."""
import sqlite3


def run_health_check(supabase_client, sqlite_path, anthropic_key, groq_key, supabase_key):
    """Verify all systems are operational on startup.

    Prints a check table to stdout. Mechanical lift from PiAgent._health_check
    (Phase 4) — no behaviour change. Args are passed explicitly so this function
    is testable without instantiating a full agent.
    """
    checks = []

    try:
        supabase_client.table("l3_active_memory").select("id").limit(1).execute()
        checks.append(("Supabase", "✓"))
    except Exception as e:
        checks.append(("Supabase", f"✗ {str(e)[:50]}"))

    try:
        conn = sqlite3.connect(sqlite_path)
        conn.execute("SELECT 1")
        conn.close()
        checks.append(("SQLite", "✓"))
    except Exception as e:
        checks.append(("SQLite", f"✗ {str(e)[:50]}"))

    checks.append(("Anthropic Key", "✓" if anthropic_key else "✗ Missing"))
    checks.append(("Groq Key", "✓" if groq_key else "✗ Missing"))
    checks.append(("Supabase Key", "✓" if supabase_key else "✗ Missing"))

    print("\n[Health Check]")
    for system, status in checks:
        print(f"  {system}: {status}")
    print()
