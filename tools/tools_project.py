"""
tools/tools_project.py — Project-level tools for Pi's self-awareness and self-improvement.

Tools:
  search_codebase   — grep Pi's own source files for symbols / text
  repo_map          — ranked symbol map of the codebase (tree-sitter or regex)
  create_ticket     — file a self-improvement ticket from within a session
  get_session_stats — introspect the current session's counters and cost
  reflect           — write a metacognitive reflection note for the current session
"""

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    import tree_sitter_python as _tsp
    from tree_sitter import Language, Parser as _TSParser
    _TS_AVAILABLE = True
except ImportError:
    _TS_AVAILABLE = False

_ROOT = Path(__file__).parent.parent

# Regex fallback: match top-level class / def / async def declarations
_DEF_RE = re.compile(r"^(class|def|async def)\s+([A-Za-z_]\w*)", re.MULTILINE)


class ProjectTools:
    """Codebase search, ticket filing, and session introspection for Pi."""

    # ── search_codebase ───────────────────────────────────────────────────────

    def search_codebase(
        self,
        query: str,
        file_pattern: str = "*.py",
        max_results: int = 20,
        context_lines: int = 2,
    ) -> Dict:
        """Search Pi's source files for a regex pattern.

        Args:
            query:         Python regex pattern to search for.
            file_pattern:  Glob to filter files (default: *.py).
            max_results:   Max matching lines to return.
            context_lines: Lines of context before/after each match (0-3).

        Returns:
            {"matches": [...], "count": int, "truncated": bool}
            Each match: {"file": str, "line": int, "text": str, "context": [str]}
        """
        context_lines = min(context_lines, 3)
        matches = []
        truncated = False

        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return {"matches": [], "count": 0, "truncated": False,
                    "error": f"Invalid regex: {e}"}

        skip_dirs = {"pi_env", "__pycache__", ".git", "node_modules", ".pytest_cache"}

        for path in sorted(_ROOT.rglob(file_pattern)):
            # Skip hidden / venv directories
            if any(part in skip_dirs for part in path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            for i, line in enumerate(lines):
                if pattern.search(line):
                    ctx_start = max(0, i - context_lines)
                    ctx_end = min(len(lines), i + context_lines + 1)
                    context = [
                        f"{'>' if j == i else ' '} {j+1}: {lines[j]}"
                        for j in range(ctx_start, ctx_end)
                    ]
                    matches.append({
                        "file": str(path.relative_to(_ROOT)),
                        "line": i + 1,
                        "text": line.rstrip(),
                        "context": context,
                    })
                    if len(matches) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

        return {"matches": matches, "count": len(matches), "truncated": truncated}

    # ── repo_map ──────────────────────────────────────────────────────────────

    def repo_map(
        self,
        query: str = "",
        file_pattern: str = "*.py",
        max_files: int = 30,
        symbols_per_file: int = 12,
    ) -> Dict:
        """Return a ranked symbol map of Pi's codebase.

        For each Python file, extracts top-level class/function names using
        tree-sitter (when available) or a regex fallback, then ranks files by
        how many symbols match ``query`` (if provided) or by symbol count.

        Args:
            query:            Optional keyword to rank files by relevance.
            file_pattern:     Glob pattern for source files (default: *.py).
            max_files:        Max files to include in the map.
            symbols_per_file: Max symbols shown per file.

        Returns:
            {"files": [...], "total_files": int, "method": "tree-sitter"|"regex"}
            Each file entry: {"path": str, "symbols": [str], "score": int}
        """
        skip_dirs = {"pi_env", "__pycache__", ".git", "node_modules", ".pytest_cache"}
        query_tokens = set(re.sub(r"[^\w\s]", " ", query.lower()).split()) if query else set()

        # Build (path, symbols) pairs
        file_symbols: List[Dict] = []
        for path in sorted(_ROOT.rglob(file_pattern)):
            if any(part in skip_dirs for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            if _TS_AVAILABLE:
                symbols = self._ts_extract_symbols(source)
            else:
                symbols = self._regex_extract_symbols(source)

            if not symbols:
                continue

            # Relevance score: query token matches in symbol names + filename
            if query_tokens:
                path_str = str(path).lower()
                score = sum(1 for sym in symbols
                            if any(t in sym.lower() for t in query_tokens))
                score += sum(2 for t in query_tokens if t in path_str)
            else:
                score = len(symbols)

            file_symbols.append({
                "path": str(path.relative_to(_ROOT)),
                "symbols": symbols[:symbols_per_file],
                "score": score,
            })

        # Rank: files with query hits first, then by symbol count
        file_symbols.sort(key=lambda x: x["score"], reverse=True)
        top = file_symbols[:max_files]

        return {
            "files": top,
            "total_files": len(file_symbols),
            "method": "tree-sitter" if _TS_AVAILABLE else "regex",
        }

    @staticmethod
    def _ts_extract_symbols(source: str) -> List[str]:
        """Extract top-level symbol names from Python source via tree-sitter."""
        try:
            lang = Language(_tsp.language())
            parser = _TSParser(lang)
            tree = parser.parse(source.encode())
            root = tree.root_node
            symbols = []
            for child in root.children:
                if child.type in ("function_definition", "class_definition",
                                  "decorated_definition"):
                    # For decorated_definition, descend to the actual def/class
                    target = child
                    if child.type == "decorated_definition":
                        for sub in child.children:
                            if sub.type in ("function_definition", "class_definition"):
                                target = sub
                                break
                    for sub in target.children:
                        if sub.type == "identifier":
                            symbols.append(sub.text.decode())
                            break
            return symbols
        except Exception:
            return _DEF_RE.findall(source) and [m[1] for m in _DEF_RE.finditer(source)]

    @staticmethod
    def _regex_extract_symbols(source: str) -> List[str]:
        """Regex fallback: extract top-level class/def names."""
        return [m.group(2) for m in _DEF_RE.finditer(source)]

    # ── create_ticket ─────────────────────────────────────────────────────────

    def create_ticket(
        self,
        title: str,
        what_failed: str,
        component: str,
        severity: str = "P3",
        where_failed: str = "",
        suggested_fix: str = "",
        _profile=None,  # T-226: guest profile routes ticket to separate directory
    ) -> Dict:
        """File a new self-improvement ticket to tickets/open/.

        Auto-generates the next ticket ID by scanning existing tickets.
        Pi should call this whenever it discovers a bug, gap, or improvement
        opportunity during a session.

        Args:
            title:         Short title describing the issue.
            what_failed:   What went wrong / what the gap is.
            component:     File(s) responsible (e.g. "tools/tools_memory.py").
            severity:      P1 | P2 | P3 | P4 (default P3).
            where_failed:  Specific function or location (optional).
            suggested_fix: Implementation hint (optional).

        Returns:
            {"id": "T-NNN", "path": "tickets/open/...", "success": bool}
        """
        ticket_id = self._next_ticket_id()
        now = datetime.now(timezone.utc).isoformat()

        # Sanitize severity
        if severity not in ("P1", "P2", "P3", "P4"):
            severity = "P3"

        ticket = {
            "id": ticket_id,
            "source": "Pi self-report (session tool call)",
            "title": title,
            "component": component,
            "what_failed": what_failed,
            "where_failed": where_failed,
            "why_likely": "",
            "severity": severity,
            "suggested_fix": suggested_fix,
            "status": "open",
            "created": now,
            "closed": None,
            "linked_solution": None,
            # T-154: a self-reported ticket's suggested_fix is a HYPOTHESIS, not
            # a verified spec. The sprint runner refuses to auto-implement
            # non-"verified" tickets, so this can't be silently "closed" by a
            # wrong self-diagnosis (see T-143). Promote to "verified" by a human
            # edit or a linked reproducing test.
            "root_cause_confidence": "hypothesis",
        }

        slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:60].strip("-")
        filename = f"{ticket_id}-{slug}.json"

        # T-226: guest tickets land in a separate dir; never auto-executed by sprint.py.
        is_guest = _profile is not None and getattr(_profile, "is_guest", False)
        if is_guest:
            profile_name = getattr(_profile, "name", "unknown")
            ticket["profile"] = profile_name
            dest_dir = _ROOT / "tickets" / "profiles" / profile_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            rel_path = f"tickets/profiles/{profile_name}/{filename}"
        else:
            dest_dir = _ROOT / "tickets" / "open"
            rel_path = f"tickets/open/{filename}"

        ticket_path = dest_dir / filename
        try:
            ticket_path.write_text(json.dumps(ticket, indent=2), encoding="utf-8")
            return {"id": ticket_id, "path": rel_path, "success": True}
        except OSError as e:
            return {"id": ticket_id, "path": "", "success": False, "error": str(e)}

    def _next_ticket_id(self) -> str:
        """Scan all ticket files and return the next T-NNN id."""
        pattern = re.compile(r"T-(\d+)")
        max_n = 0
        for directory in (_ROOT / "tickets" / "open", _ROOT / "tickets" / "closed"):
            if not directory.exists():
                continue
            for f in directory.iterdir():
                m = pattern.match(f.stem)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        return f"T-{max_n + 1:03d}"

    # ── reflect ───────────────────────────────────────────────────────────────

    def reflect(
        self,
        note: str,
        session_id: str = "",
        turn: int = 0,
        memory_tools=None,
    ) -> Dict:
        """Write a metacognitive reflection note for the current session.

        Saves to ``logs/reflections.jsonl`` (local, always) and optionally to
        L2 memory (category='session_reflection', importance=6) when
        ``memory_tools`` is provided.

        Args:
            note:         The reflection text Pi wants to record.
            session_id:   Current session ID (for provenance).
            turn:         Current turn number.
            memory_tools: Optional MemoryTools instance for L2 persistence.

        Returns:
            {"saved_local": bool, "saved_l2": bool, "note": str}
        """
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "ts": now,
            "session_id": session_id,
            "turn": turn,
            "note": note,
        }

        saved_local = False
        log_path = _ROOT / "logs" / "reflections.jsonl"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            saved_local = True
        except Exception as e:
            print(f"[Reflect] local log failed: {e}")

        saved_l2 = False
        if memory_tools:
            try:
                result = memory_tools.memory_write(
                    content=f"[Reflection turn={turn}] {note}",
                    tier="l2",
                    importance=6,
                    category="session_reflection",
                    session_id=session_id,
                )
                saved_l2 = result.get("success", False)
            except Exception as e:
                print(f"[Reflect] L2 write failed: {e}")

        return {"saved_local": saved_local, "saved_l2": saved_l2, "note": note}

    # ── run_verify / run_tests (T-181) ────────────────────────────────────────

    _VERIFY_LOCK = _ROOT / "logs" / "verify.lock"

    def run_verify(self, timeout: int = 600) -> Dict:
        """Run scripts/verify.py --quiet and return a structured result.

        Returns:
            {overall, syntax_failed, gate_failures, test_failures, tail, error}
        """
        lock = self._VERIFY_LOCK
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists():
            return {"overall": "BUSY", "error": "Another verify run is in progress (lock exists at logs/verify.lock). Wait or delete the lock if it is stale."}

        lock.write_text(str(os.getpid()), encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(_ROOT / "scripts" / "verify.py"), "--quiet"],
                capture_output=True, text=True, cwd=str(_ROOT), timeout=timeout,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        except subprocess.TimeoutExpired:
            return {"overall": "TIMEOUT", "error": f"verify.py exceeded {timeout}s limit"}
        except Exception as e:
            return {"overall": "ERROR", "error": str(e)}
        finally:
            try:
                lock.unlink(missing_ok=True)
            except Exception:
                pass

        stdout = result.stdout or ""
        lines = stdout.splitlines()
        overall = "FAIL"
        for line in lines:
            if "[verify] PASS" in line:
                overall = "PASS"
                break

        # Extract failure lines from stdout
        syntax_failed: list[str] = [l for l in lines if l.strip().startswith("SYNTAX FAIL")]
        gate_failures: list[str] = [l for l in lines if "GATE FAIL" in l or "GATE MISSING" in l]
        test_failures: list[str] = [l for l in lines if l.strip().startswith("FAIL ")]

        return {
            "overall": overall,
            "exit_code": result.returncode,
            "syntax_failed": syntax_failed,
            "gate_failures": gate_failures,
            "test_failures": test_failures,
            "tail": "\n".join(lines[-40:]),
        }

    def run_tests(self, test_file: str, timeout: int = 120) -> Dict:
        """Run a single pytest file quickly for iteration feedback.

        Args:
            test_file: Filename or path under testing/ (e.g. 'test_memory.py').
            timeout:   Max seconds (default 120).

        Returns:
            {passed, failed, exit_code, tail, error}
        """
        # Normalise: accept 'test_foo.py', 'testing/test_foo.py', full paths
        p = Path(test_file)
        if not p.is_absolute():
            candidate = _ROOT / "testing" / p.name
            if candidate.exists():
                p = candidate
            else:
                p = _ROOT / p  # relative to root
        if not p.exists():
            return {"passed": 0, "failed": 0, "exit_code": -1,
                    "error": f"Test file not found: {test_file}"}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(p), "-q", "--tb=short",
                 "-m", "not costly"],
                capture_output=True, text=True, cwd=str(_ROOT), timeout=timeout,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        except subprocess.TimeoutExpired:
            return {"passed": 0, "failed": 0, "exit_code": -1,
                    "error": f"pytest exceeded {timeout}s"}
        except Exception as e:
            return {"passed": 0, "failed": 0, "exit_code": -1, "error": str(e)}

        stdout = result.stdout or ""
        lines = stdout.splitlines()
        # Parse "X passed", "Y failed" from pytest summary line
        passed = failed = 0
        for line in reversed(lines):
            m = re.search(r"(\d+) passed", line)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+) failed", line)
            if m:
                failed = int(m.group(1))
            if passed or failed:
                break

        return {
            "passed": passed,
            "failed": failed,
            "exit_code": result.returncode,
            "tail": "\n".join(lines[-40:]),
        }

    # ── get_session_stats ─────────────────────────────────────────────────────

    def get_session_stats(self, agent) -> Dict:
        """Return live stats for the current Pi session.

        Args:
            agent: The running PiAgent instance.

        Returns:
            Dict with session_id, mode, turns, uptime_minutes, cost_session,
            cost_today, tokens_in, tokens_out, memory_writes_session.
        """
        now = datetime.now(timezone.utc)
        uptime_minutes = round(
            (now - agent.session_start).total_seconds() / 60, 1
        )

        # Cost / tokens from evolution log for this session
        recent = agent.evolution.get_recent_interactions(hours=24)
        session_interactions = [
            i for i in recent
            if i.get("metadata", {}).get("session_id") == agent.session_id
        ]
        cost_session = sum(i.get("cost", 0) for i in session_interactions)
        tokens_in = sum(i.get("tokens_in", 0) for i in session_interactions)
        tokens_out = sum(i.get("tokens_out", 0) for i in session_interactions)
        cost_today = sum(i.get("cost", 0) for i in recent)

        # Count memory_write tool calls this session
        memory_writes = sum(
            1 for i in session_interactions
            for tc in (i.get("tool_calls") or [])
            if (tc.get("name") or "") == "memory_write"
        )

        return {
            "session_id": agent.session_id,
            "mode": agent.mode,
            "turns": agent.turn_number,
            "uptime_minutes": uptime_minutes,
            "cost_session_usd": round(cost_session, 6),
            "cost_today_usd": round(cost_today, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "memory_writes_session": memory_writes,
        }


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────
#
# system_introspect is registered here for grouping; its implementation lives
# in agent/tools.py::_system_introspect (filesystem + agent state) and is
# lazy-imported in the handler to avoid a circular dep at module load.

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_search_codebase(agent, tool_input, *, memory_override=None):
    return ProjectTools().search_codebase(
        query=tool_input["query"],
        file_pattern=tool_input.get("file_pattern", "*.py"),
        max_results=tool_input.get("max_results", 20),
    )


def _handle_create_ticket(agent, tool_input, *, memory_override=None):
    _profile = getattr(agent, "current_profile", None)
    return ProjectTools().create_ticket(
        title=tool_input["title"],
        what_failed=tool_input["what_failed"],
        component=tool_input["component"],
        severity=tool_input.get("severity", "P3"),
        where_failed=tool_input.get("where_failed", ""),
        suggested_fix=tool_input.get("suggested_fix", ""),
        _profile=_profile,
    )


def _handle_get_session_stats(agent, tool_input, *, memory_override=None):
    return ProjectTools().get_session_stats(agent)


def _handle_system_introspect(agent, tool_input, *, memory_override=None):
    from agent.tools import _system_introspect
    return _system_introspect(agent, memory_override=memory_override)


def _handle_repo_map(agent, tool_input, *, memory_override=None):
    return ProjectTools().repo_map(
        query=tool_input.get("query", ""),
        max_files=tool_input.get("max_files", 30),
    )


def _handle_reflect(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    return ProjectTools().reflect(
        note=tool_input["note"],
        session_id=agent.session_id,
        turn=agent.turn_number,
        memory_tools=mem,
    )


def _handle_run_verify(agent, tool_input, *, memory_override=None):
    return ProjectTools().run_verify(timeout=tool_input.get("timeout", 600))


def _handle_run_tests(agent, tool_input, *, memory_override=None):
    return ProjectTools().run_tests(
        test_file=tool_input["test_file"],
        timeout=tool_input.get("timeout", 120),
    )


# T-183: plan-then-execute handlers
def _handle_set_plan(agent, tool_input, *, memory_override=None):
    steps = tool_input.get("steps", [])
    if not steps:
        return {"success": False, "error": "steps list is empty"}
    if not hasattr(agent, "plan_state"):
        from agent.plan_state import PlanState
        agent.plan_state = PlanState()
    agent.plan_state.set(steps)
    return {"success": True, "steps": len(agent.plan_state), "plan": agent.plan_state.render()}


def _handle_update_plan(agent, tool_input, *, memory_override=None):
    if not hasattr(agent, "plan_state") or agent.plan_state.is_empty():
        return {"success": False, "error": "no active plan — call set_plan first"}
    index = tool_input.get("index")
    status = tool_input.get("status", "done")
    text = tool_input.get("text")
    if index is None:
        return {"success": False, "error": "index is required"}
    ok = agent.plan_state.update(index, status, text)
    if not ok:
        return {"success": False, "error": f"index {index} out of range (plan has {len(agent.plan_state)} steps)"}
    return {"success": True, "plan": agent.plan_state.render()}


TOOLS = [
    ToolSpec(
        name="search_codebase",
        description=(
            "Search Pi's own source files for a regex pattern. Use to find function "
            "definitions, understand how a subsystem works, or locate where a variable "
            "is used before modifying it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query":        {"type": "string", "description": "Python regex pattern to search for"},
                "file_pattern": {"type": "string", "default": "*.py",
                                 "description": "Glob filter, e.g. '*.py' or 'agent/*.py' (default: *.py)"},
                "max_results":  {"type": "integer", "default": 20,
                                 "description": "Max matching lines to return (default 20)"},
            },
            "required": ["query"],
        },
        handler=_handle_search_codebase,
        success_predicate=lambda r: "error" not in r,
    ),
    ToolSpec(
        name="create_ticket",
        description=(
            "File a self-improvement ticket to tickets/open/. Use when you discover a "
            "bug, gap, or improvement opportunity during a session that should be "
            "tracked for future work."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title":         {"type": "string"},
                "what_failed":   {"type": "string"},
                "component":     {"type": "string", "description": "File(s) responsible, e.g. 'tools/tools_memory.py'"},
                "severity":      {"type": "string", "enum": ["P1", "P2", "P3", "P4"], "default": "P3"},
                "where_failed":  {"type": "string", "description": "Specific function or location (optional)"},
                "suggested_fix": {"type": "string", "description": "Implementation hint (optional)"},
            },
            "required": ["title", "what_failed", "component"],
        },
        handler=_handle_create_ticket,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="get_session_stats",
        description=(
            "Return live stats for the current session: turns, cost, tokens, uptime. "
            "Use to answer 'how much have we spent?' or 'what mode are we in?'"
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_handle_get_session_stats,
    ),
    ToolSpec(
        name="system_introspect",
        description=(
            "Return live system state: total interactions logged, open/closed ticket "
            "counts, solution count, last solution ID, L3 cache size, session ID, mode, "
            "and uptime. Use this — not memory — when asked about Pi's own stats or history."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_handle_system_introspect,
    ),
    ToolSpec(
        name="repo_map",
        description=(
            "Return a ranked symbol map of Pi's codebase — which files define which "
            "classes and functions. Use before editing to understand what's defined "
            "where, or to find the right file for a task without a full text search."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query":     {"type": "string",
                              "description": "Optional keyword to rank files by relevance (e.g. 'memory', 'router')"},
                "max_files": {"type": "integer", "default": 30,
                              "description": "Max files to include (default 30)"},
            },
            "required": [],
        },
        handler=_handle_repo_map,
        success_predicate=lambda r: "error" not in r,
    ),
    ToolSpec(
        name="reflect",
        description=(
            "Write a metacognitive reflection note for the current session. Use every "
            "~10 turns, after solving a complex problem, or at session end. Notes are "
            "saved locally and to L2 memory for cross-session learning."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "note": {"type": "string",
                         "description": "The reflection: what was learned, what worked, what to do differently"},
            },
            "required": ["note"],
        },
        handler=_handle_reflect,
        success_predicate=lambda r: r.get("saved_local", False),
    ),
    # T-181: self-verification tools
    ToolSpec(
        name="run_verify",
        description=(
            "Run scripts/verify.py --quiet and return a structured result "
            "{overall: PASS|FAIL|TIMEOUT|BUSY, syntax_failed, gate_failures, test_failures, tail}. "
            "Use AFTER making code edits to confirm the change is clean before claiming done. "
            "Takes up to 10 minutes — prefer run_tests for faster iteration on a single file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "timeout": {"type": "integer", "default": 600,
                            "description": "Max seconds to wait (default 600 / 10 min)"},
            },
            "required": [],
        },
        handler=_handle_run_verify,
        success_predicate=lambda r: r.get("overall") == "PASS",
    ),
    ToolSpec(
        name="run_tests",
        description=(
            "Run a single pytest test file quickly for iteration feedback. "
            "Returns {passed, failed, exit_code, tail}. "
            "Use during iteration (fast); use run_verify before claiming a fix is done."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "test_file": {"type": "string",
                              "description": "Test filename, e.g. 'test_memory.py' or 'testing/test_memory.py'"},
                "timeout": {"type": "integer", "default": 120,
                            "description": "Max seconds (default 120)"},
            },
            "required": ["test_file"],
        },
        handler=_handle_run_tests,
        success_predicate=lambda r: r.get("failed", 1) == 0 and r.get("exit_code", 1) == 0,
    ),
    # T-183: plan-then-execute tools
    ToolSpec(
        name="set_plan",
        description=(
            "Set an explicit multi-step plan that stays visible in the system prompt "
            "even after message history is compacted. Use for any task with 3+ distinct steps. "
            "Call BEFORE starting work; update each step with update_plan as you go."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered list of step descriptions",
                },
            },
            "required": ["steps"],
        },
        handler=_handle_set_plan,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="update_plan",
        description=(
            "Update the status of a step in the active plan (set with set_plan). "
            "Call after completing or failing each step to keep the plan current."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "index":  {"type": "integer",
                           "description": "Zero-based step index"},
                "status": {"type": "string",
                           "enum": ["pending", "done", "failed", "skipped"],
                           "description": "New status (default: done)"},
                "text":   {"type": "string",
                           "description": "Optional updated step description"},
            },
            "required": ["index"],
        },
        handler=_handle_update_plan,
        success_predicate=lambda r: r.get("success", False),
    ),
]
