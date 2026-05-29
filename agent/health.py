"""Startup health check — verifies Supabase, SQLite, and required API keys."""
import sqlite3


def run_health_check(supabase_client, sqlite_path, anthropic_key, groq_key, supabase_key,
                     verbose: bool = False):
    """Verify all systems are operational on startup.

    Default behaviour (post Phase A): silent unless something fails. Pass
    ``verbose=True`` to restore the full check-table output for debugging.

    Returns a list of (system, status) tuples so callers can render their own
    summary if needed.
    """
    checks = []

    try:
        supabase_client.table("l3_active_memory").select("id").limit(1).execute()
        checks.append(("Supabase", "OK"))
    except Exception as e:
        checks.append(("Supabase", f"FAIL {str(e)[:50]}"))

    try:
        conn = sqlite3.connect(sqlite_path)
        conn.execute("SELECT 1")
        conn.close()
        checks.append(("SQLite", "OK"))
    except Exception as e:
        checks.append(("SQLite", f"FAIL {str(e)[:50]}"))

    checks.append(("Anthropic Key", "OK" if anthropic_key else "FAIL Missing"))
    checks.append(("Groq Key", "OK" if groq_key else "FAIL Missing"))
    checks.append(("Supabase Key", "OK" if supabase_key else "FAIL Missing"))

    failures = [(s, st) for s, st in checks if not st.startswith("OK")]

    if verbose:
        print("\n[Health Check]")
        for system, status in checks:
            print(f"  {system}: {status}")
        print()
    elif failures:
        print("[Pi] Health check failures:")
        for system, status in failures:
            print(f"  {system}: {status}")

    return checks
