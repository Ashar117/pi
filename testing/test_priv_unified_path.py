"""testing/test_god_uses_unified_path.py — T-082 step 8 acceptance test.

Validates the 5 success criteria from T-082:
  1. God mode flows through LLMRouter (not agent/god.py's private LLM call).
  2. God mode flows through agent/tools.execute_tool (not the _TOOLS dict).
  3. invalid_at column applies to data/god_memory.db (S-054 carryover).
  4. The four private paths are all listed in .gitignore.
  5. Private MemoryTools uses the _NoopSupabase shim — god memory never
     reaches the public Supabase project.

All LLM/Supabase calls are mocked; no network, no real API keys needed.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ── Test 4: .gitignore privacy invariants (no fixture, pure file check) ────────

PRIVATE_PATHS = [
    "data/god_memory.db",
    "prompts/god_consciousness.txt",
    "tickets/god/",
    "vault/.god/",
]


def _exclude_covers(path: str, exclude_lines: list[str]) -> bool:
    """Return True if `path` is covered by any line in an ignore-style file.

    A path is covered if either (a) the path itself appears as a line, or
    (b) any parent directory of the path appears as a line ending with `/`.
    Example: "data/god_memory.db" is covered by "data/" because data/ is
    the parent. This mirrors how git resolves ignore patterns.
    """
    needle = path.rstrip("/")
    parts = needle.split("/")
    candidates = {needle, needle + "/"}
    for i in range(1, len(parts)):
        prefix = "/".join(parts[:i]) + "/"
        candidates.add(prefix)
    lines = {line.strip() for line in exclude_lines if line.strip() and not line.strip().startswith("#")}
    return bool(candidates & lines)


def test_private_paths_gitignored():
    """ADR-001 invariants 1-4: every private path must be excluded from git.

    Git resolves exclusion from BOTH .gitignore (tracked, public) and
    .git/info/exclude (local-only, never committed). Pi's convention is
    to keep god-mode-specific paths in the local-only file so the public
    .gitignore never names them — that's the lesson of commit 41e37f2
    (\"gitignore: remove all private path references; move to local exclude\").
    Honors transitive coverage: `data/god_memory.db` is covered by a `data/`
    line, not just an explicit `data/god_memory.db` line.
    """
    gi_lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    local_exclude_path = ROOT / ".git" / "info" / "exclude"
    local_lines = (
        local_exclude_path.read_text(encoding="utf-8").splitlines()
        if local_exclude_path.exists() else []
    )
    for path in PRIVATE_PATHS:
        assert (_exclude_covers(path, gi_lines)
                or _exclude_covers(path, local_lines)), (
            f"Privacy invariant broken: {path!r} not covered by .gitignore "
            f"or .git/info/exclude. ADR-001 requires all four private paths "
            f"to be excluded from git via one of these mechanisms."
        )


# ── Test 3 + 5: private MemoryTools behavior (no PiAgent fixture needed) ───────

def test_god_memory_uses_noop_supabase():
    """ADR-001 invariant 5: private MemoryTools never reaches public Supabase.

    namespace='god' (or empty creds) must install the _NoopSupabase shim so
    every fluent supabase chain becomes a silent drop. Public Supabase rows
    never see god writes.
    """
    from tools.tools_memory import MemoryTools, _NoopSupabase
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "god_memory.db")
        mem = MemoryTools("", "", db_path=db, namespace="god")
        assert mem.is_private, "namespace='god' must mark instance private"
        assert isinstance(mem._supabase_client, _NoopSupabase), (
            "Private namespace must use _NoopSupabase shim — got "
            f"{type(mem._supabase_client).__name__}"
        )
        # The shim's fluent chain returns empty data and never raises.
        resp = mem.supabase.table("raw_wiki").insert({"x": 1}).execute()
        assert resp.data == [], (
            "_NoopSupabase.execute() should yield empty data; got "
            f"{resp.data!r}"
        )


def test_invalid_at_works_in_god_memory_db():
    """S-054 carryover: data/god_memory.db gets the invalid_at column.

    Built via MemoryTools(namespace='god'), the private DB must run the
    same idempotent schema migration as public pi.db so superseded facts
    can be invalidated without deletion.
    """
    from tools.tools_memory import MemoryTools
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "god_memory.db")
        MemoryTools("", "", db_path=db, namespace="god")
        conn = sqlite3.connect(db)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(l3_cache)").fetchall()}
        finally:
            conn.close()
        assert "invalid_at" in cols, (
            f"invalid_at column missing from god_memory.db l3_cache. "
            f"Got columns: {sorted(cols)}"
        )


# ── Tests 1+2: god mode flows through LLMRouter + execute_tool ────────────────


@pytest.fixture(scope="module")
def god_agent():
    """Build a real PiAgent in god mode. Heavy fixture — module-scoped.

    Mocks builtins.input so prompt-on-startup doesn't hang. Subsystem network
    calls are mocked per-test via patch.object on the agent instance.
    """
    def fake_input(_prompt=""):
        return "n"

    with patch("builtins.input", side_effect=fake_input):
        # Force PI_GOD_LEGACY off so we exercise the unified path
        os.environ.pop("PI_GOD_LEGACY", None)
        from pi_agent import PiAgent
        agent = PiAgent()
        agent.mode = "god"
    return agent


def _llm_response(text="ok", tool_calls=None, provider="groq", model="llama-3.3-70b-versatile"):
    """Build an LLMResponse dataclass instance for mocked router.chat returns."""
    from core.llm_router import LLMResponse, ToolCall
    tcs = [ToolCall(id=tc["id"], name=tc["name"], input=tc["input"])
           for tc in (tool_calls or [])]
    return LLMResponse(
        text=text,
        provider=provider,
        model=model,
        tool_calls=tcs,
        stop_reason="tool_use" if tcs else "end_turn",
        tokens_in=10,
        tokens_out=5,
    )


def test_god_routes_through_llm_router(god_agent):
    """Success criterion: god mode calls LLMRouter.chat (not god.py)."""
    with patch.object(god_agent.router, "chat", return_value=_llm_response("hi")) as mock_chat:
        response = god_agent.process_input("hello god")

    assert mock_chat.called, (
        "router.chat was never called — god mode still routes through the "
        "legacy _respond_god / agent.god path."
    )
    # Verify tier='private' was passed — the contract that keeps god on
    # Groq/Ollama only, never Anthropic/Gemini/Cerebras/OpenRouter.
    call_kwargs = mock_chat.call_args.kwargs
    assert call_kwargs.get("tier") == "private", (
        f"router.chat called without tier='private'; got tier="
        f"{call_kwargs.get('tier')!r}. T-082 router-tier contract broken."
    )
    assert response  # response surfaced through the unified path


def test_god_routes_through_execute_tool(god_agent):
    """Success criterion: god mode dispatches tools via agent.tools.execute_tool.

    Issue a tool_use response from the mocked router; verify execute_tool
    is invoked with memory_override pointing at the private MemoryTools.
    """
    first = _llm_response(
        text="reading memory",
        tool_calls=[{"id": "tu_1", "name": "memory_read", "input": {"query": "x"}}],
    )
    second = _llm_response(text="done")

    with patch.object(god_agent.router, "chat", side_effect=[first, second]), \
         patch("pi_agent.execute_tool", return_value=[]) as mock_exec:
        god_agent.process_input("recall x")

    assert mock_exec.called, (
        "execute_tool was never invoked — god mode tool dispatch still "
        "bypasses agent/tools.execute_tool."
    )
    # The memory_override kwarg must be passed and must NOT be the public
    # self.memory — it should be the private MemoryTools for namespace='god'.
    kwargs = mock_exec.call_args.kwargs
    override = kwargs.get("memory_override")
    assert override is not None, (
        "execute_tool called without memory_override — private memory "
        "routing is broken; god tools would hit the public DB."
    )
    assert override is not god_agent.memory, (
        "memory_override resolved to the public memory instance — "
        "namespace partitioning failed."
    )
    assert getattr(override, "namespace", None) == "god", (
        f"memory_override.namespace expected 'god', got "
        f"{getattr(override, 'namespace', None)!r}"
    )
