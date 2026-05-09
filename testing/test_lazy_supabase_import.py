"""
testing/test_lazy_supabase_import.py — T-032: supabase import must be deferred
until MemoryTools is instantiated, not triggered at module import time.

Evidence: first chat run hit KeyboardInterrupt deep inside
tenacity -> pyiceberg -> storage3 -> supabase import chain.
Supabase alone takes ~1.2 s to import on cold disk.

Fix: move `from supabase import create_client` from the module top of
tools/tools_memory.py into MemoryTools.__init__. Then `import pi_agent`
no longer pays the Supabase import cost at startup.

Verification strategy: run a fresh subprocess that imports tools.tools_memory
(without instantiating MemoryTools) and checks whether 'supabase' appears in
sys.modules. Subprocess isolation avoids contamination from other test modules
that may have already imported supabase.

Offline — no API calls, no network.
"""
import subprocess
import sys
import os
import pytest


def _run_check(code: str) -> str:
    """Run code in a fresh Python process and return stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return result.stdout.strip()


# ── Test 1: importing tools.tools_memory must NOT load supabase ───────────────

def test_module_import_does_not_load_supabase():
    """Importing tools.tools_memory should not trigger the supabase import chain.

    Currently fails because `from supabase import create_client` is at the module
    top of tools/tools_memory.py. Fix: move it into MemoryTools.__init__.
    """
    code = (
        "import sys, os; "
        "sys.path.insert(0, os.getcwd()); "
        "import tools.tools_memory; "
        "print('supabase' in sys.modules)"
    )
    output = _run_check(code)
    assert output == "False", (
        "Importing tools.tools_memory eagerly loaded supabase (got 'True'). "
        "Move 'from supabase import create_client' into MemoryTools.__init__ "
        "so the heavy import only happens when the class is instantiated."
    )


# ── Test 2: importing pi_agent must NOT load supabase either ──────────────────

def test_pi_agent_import_does_not_load_supabase():
    """import pi_agent should not trigger the supabase import chain.

    pi_agent does `from tools.tools_memory import MemoryTools` at module level.
    As long as tools_memory no longer imports supabase at module level, this
    test passes.
    """
    code = (
        "import sys, os; "
        "sys.path.insert(0, os.getcwd()); "
        "import pi_agent; "
        "print('supabase' in sys.modules)"
    )
    output = _run_check(code)
    assert output == "False", (
        "import pi_agent eagerly loaded supabase (got 'True'). "
        "The lazy-import fix in tools/tools_memory.py should prevent this."
    )
