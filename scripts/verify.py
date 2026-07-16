#!/usr/bin/env python3
"""
scripts/verify.py — Pi CI in a bottle.

Runs:
1. Syntax check on every .py file in the repo (ast.parse).
2. All non-costly tests under testing/ (excludes @pytest.mark.costly).
3. Writes docs/STATUS.md with date, results, and skipped tests.
4. Exits 0 if all pass, 1 otherwise.

Usage:
    python scripts/verify.py          # run everything
    python scripts/verify.py --quiet  # suppress per-file output
"""
import ast
import os
import sys
import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
TESTING_DIR = ROOT / "testing"
STATUS_OUT = ROOT / "docs" / "STATUS.md"

COSTLY_TESTS = {
    "test_normie_honesty.py",
    "test_normie_no_misfire.py",   # T-024: hits real Groq API
    "test_query_formulation.py",
    "test_query_formulation_v2.py",  # T-027: hits real Claude API
    "test_memory_roundtrip.py",   # hits Claude API
    "test_memory_tool_path.py",   # T-023: hits Supabase + Claude API
    "test_l2_content_search.py",  # standalone script (no def test_*); run directly
    "test_l1_autolog.py",         # standalone script (no def test_*); hits Supabase
    "test_runner.py",             # old harness class (TestRunner.__init__ confuses pytest)
    "test_memory.py",             # hits real Supabase + shared SQLite; covered by test_memory_roundtrip_contract.py keystone
    "test_integration.py",        # hits real Supabase + shared SQLite; covered by keystone gate
}

# ── Keystone coherence gate (T-152) ──────────────────────────────────────────
# These protect the emergent property the suite never checked until the T-148
# silent context-drop bug shipped green: across a conversation the model can see
# Pi's own prior replies. They ALWAYS run, first, and ALWAYS must pass. The gate
# also fails hard if a harness file goes missing, so the safety net that catches
# context-drop can never silently disappear from CI.
GATE_TESTS = (
    "test_conversation_golden.py",        # integration: multi-turn via real process_input
    "test_context_fidelity.py",           # unit: real {"type":"text"} block extraction
    "test_memory_roundtrip_contract.py",  # C1: tier write->read is content-preserving (L3/L1)
    "test_l2_roundtrip_contract.py",      # T-167: L2 write->read content preservation
    "test_l3_sync_ordering.py",           # T-270: _sync_l3 orders newest-first + caps rows
)


def syntax_check_all() -> tuple[list[str], list[str]]:
    """Return (passed_paths, failed_paths) for all .py files in the repo."""
    passed, failed = [], []
    for path in sorted(ROOT.rglob("*.py")):
        # skip venv, __pycache__, .git, and tool-managed worktree dirs
        if any(part in path.parts for part in ("pi_env", "__pycache__", ".git", ".claude", "_god_archive")):
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            passed.append(str(path.relative_to(ROOT)))
        except SyntaxError as e:
            failed.append(f"{path.relative_to(ROOT)}:{e.lineno}: {e.msg}")
    return passed, failed


def check_bare_except() -> list[str]:
    """T-250: FAIL on any bare 'except:' (catches KeyboardInterrupt/SystemExit).

    Walks the same file set syntax_check_all() already parses — no new
    inclusion/exclusion config.
    """
    offenders: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        # "_archive"/"_god_archive": archived code is frozen history — the
        # never-delete rule lands old files there, and old style must not fail the gate.
        if any(part in path.parts for part in ("pi_env", "__pycache__", ".git", ".claude", "_archive", "_god_archive")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue  # already reported by syntax_check_all
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return offenders


BASELINES_FILE = ROOT / "scripts" / "verify_baselines.json"


def check_swallowed_exceptions() -> tuple[list[str], int, int]:
    """T-286: ratchet on 'except X: pass' sites (silent, no telemetry, nothing
    re-raised). A handler that calls track_silent(...) has a non-Pass body and
    is never a match here — it already isn't silent.

    Returns (new_offenders, current_count, baseline_count). new_offenders is
    non-empty only when current_count > baseline — the debt ceiling may only
    go down, never up. Excludes testing/ (test doubles legitimately swallow).
    """
    offenders: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in path.parts for part in
               ("pi_env", "__pycache__", ".git", ".claude", "_archive", "_god_archive", "testing")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.ExceptHandler) and len(node.body) == 1
                    and isinstance(node.body[0], ast.Pass)):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    baseline = json.loads(BASELINES_FILE.read_text(encoding="utf-8")) if BASELINES_FILE.exists() else {}
    prev = baseline.get("swallowed_pass", 0)
    current = len(offenders)

    if current > prev:
        return offenders, current, prev
    if current < prev:
        baseline["swallowed_pass"] = current
        BASELINES_FILE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    return [], current, prev


def run_tests() -> tuple[list[str], list[str], list[str]]:
    """Return (run_tests, skipped_tests, failures) from the non-costly suite."""
    test_files = sorted(TESTING_DIR.glob("test_*.py"))
    run, skipped, failures = [], [], []

    for tf in test_files:
        if tf.name in GATE_TESTS:
            continue  # run separately, first, as the keystone gate (run_gate)
        if tf.name in COSTLY_TESTS:
            skipped.append(tf.name)
            continue

        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tf), "-q", "--tb=short",
             "-m", "not costly"],
            capture_output=True, text=True, cwd=ROOT,
            env={**os.environ, "PYTHONUTF8": "1", "SKIP_COSTLY": "1"},
        )
        run.append(tf.name)
        if result.returncode != 0:
            failures.append(f"FAIL {tf.name}:\n{result.stdout[-800:]}\n{result.stderr[-400:]}")

    return run, skipped, failures


def run_gate() -> tuple[list[str], list[str]]:
    """Run the keystone coherence gate. Returns (ran, failures).

    A missing harness file is itself a hard failure — the net that catches
    conversation context-drop (T-148) must never silently disappear from CI.
    """
    ran, failures = [], []
    for name in GATE_TESTS:
        tf = TESTING_DIR / name
        if not tf.exists():
            failures.append(
                f"GATE MISSING: {name} — coherence harness absent; refusing to pass"
            )
            continue
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tf), "-q", "--tb=short",
             "-m", "not costly"],
            capture_output=True, text=True, cwd=ROOT,
            env={**os.environ, "PYTHONUTF8": "1", "SKIP_COSTLY": "1"},
        )
        ran.append(name)
        if result.returncode != 0:
            failures.append(f"GATE FAIL {name}:\n{result.stdout[-800:]}\n{result.stderr[-400:]}")
    return ran, failures


def write_status(
    syntax_passed: list[str],
    syntax_failed: list[str],
    tests_run: list[str],
    tests_skipped: list[str],
    test_failures: list[str],
    gate_run: list[str] | None = None,
    gate_failures: list[str] | None = None,
) -> None:
    gate_run = gate_run or []
    gate_failures = gate_failures or []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    overall = (
        "PASS"
        if not syntax_failed and not test_failures and not gate_failures
        else "FAIL"
    )

    lines = [
        f"# docs/STATUS.md — auto-generated by scripts/verify.py",
        f"",
        f"**Last run:** {now}",
        f"**Overall:** {overall}",
        f"",
        f"---",
        f"",
        f"## Syntax check",
        f"",
        f"- Files checked: {len(syntax_passed) + len(syntax_failed)}",
        f"- Passed: {len(syntax_passed)}",
        f"- Failed: {len(syntax_failed)}",
    ]
    if syntax_failed:
        lines.append("")
        lines.append("**Failures:**")
        for f in syntax_failed:
            lines.append(f"- `{f}`")

    lines += [
        f"",
        f"---",
        f"",
        f"## Coherence gate (keystone — T-152)",
        f"",
        f"- Gate tests run: {len(gate_run)}",
        f"- Gate failures: {len(gate_failures)}",
    ]
    for t in gate_run:
        mark = "✅" if not any(t in f for f in gate_failures) else "❌"
        lines.append(f"- {mark} `{t}`")
    for f in gate_failures:
        if "GATE MISSING" in f:
            lines.append(f"- ❌ `{f.split(' — ')[0]}`")

    lines += [
        f"",
        f"---",
        f"",
        f"## Test suite (non-costly)",
        f"",
        f"- Tests run: {len(tests_run)}",
        f"- Skipped (costly): {len(tests_skipped)}",
        f"- Failures: {len(test_failures)}",
    ]
    _PRIVATE = ("god",)  # test names containing these strings are not listed publicly
    if tests_run:
        lines.append("")
        lines.append("**Run:**")
        for t in tests_run:
            if any(p in t.lower() for p in _PRIVATE):
                continue
            mark = "✅" if not any(t in f for f in test_failures) else "❌"
            lines.append(f"- {mark} `{t}`")
    if tests_skipped:
        lines.append("")
        lines.append("**Skipped (run manually — hit live APIs):**")
        for t in tests_skipped:
            if any(p in t.lower() for p in _PRIVATE):
                continue
            lines.append(f"- ⏭ `{t}`")
    all_failures = list(gate_failures) + list(test_failures)
    if all_failures:
        lines.append("")
        lines.append("**Failure detail:**")
        for f in all_failures:
            lines.append("")
            lines.append("```")
            lines.append(f)
            lines.append("```")

    STATUS_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── T-166: behavioral test-coverage check ────────────────────────────────────
# Maps source module basenames → the test file that actually covers them when
# the name doesn't follow the expected test_<module>.py pattern.
_COVERAGE_ALTERNATE: dict[str, str] = {
    "conversation.py":    "test_conversation_schema.py",   # T-161 schema tests
    "caretaker.py":       "test_caretaker_lite.py",
    "tools_memory.py":    "test_memory_roundtrip_contract.py",
    "schema_translate.py":"test_normie_tools.py",          # round-trip tested there
}

# Modules legitimately exempt: hardware-dep, pure config, or covered by integration
_COVERAGE_EXEMPT: set[str] = {
    "health.py",          # would hit live APIs; covered by startup banner test
    "review.py",          # sprint integration; tested indirectly
    "cost_tracker.py",    # pure math, covered by llm_router tests
    "tools_stt.py",       # hardware (mic) — impossible offline
    "tools_tts.py",       # hardware (speaker) — impossible offline
    "tools_wakeword.py",   # hardware (mic + wake model) — impossible offline
    "tools_scheduler.py",  # background cron service, not a tool-registry member (T-170)
    "tools_execution.py",  # OS execution — dangerous to unit-test blindly
    "tools_browse.py",     # real browser dep; covered by browser_auto tests
    "prompt.py",          # prompt assembly; tested via conversation_golden
    "tool_spec.py",       # validated via every tool registration (test_normie_tools)
    "watchers.py",        # tested in test_doc_drift_watcher.py implicitly
    "session.py",         # covered by test_session_exit_retention + resumable_exit
}


def check_test_coverage() -> list[str]:
    """T-166: return list of source modules with no behavioral test coverage.

    Currently a WARN (not FAIL) so the gate is visible without blocking CI.
    Flip to FAIL by removing the caller's 'warn only' guard once T-159 clears.
    """
    source_dirs = [
        ROOT / "agent",
        ROOT / "tools",
        ROOT / "core",
    ]
    test_files = {p.name for p in (ROOT / "testing").glob("test_*.py")}
    uncovered = []

    for src_dir in source_dirs:
        for src in sorted(src_dir.glob("*.py")):
            if src.name.startswith("__"):
                continue
            base = src.name
            if base in _COVERAGE_EXEMPT:
                continue
            direct = f"test_{base}"
            alternate = _COVERAGE_ALTERNATE.get(base)
            if direct in test_files or (alternate and alternate in test_files):
                continue
            uncovered.append(f"{src.relative_to(ROOT)}")

    return uncovered


def check_consciousness_tool_drift() -> list[str]:
    """T-192: warn when tool names mentioned in consciousness.txt don't exist in the registry.

    Extracts backtick-wrapped snake_case identifiers that look like tool names
    (contain underscores, e.g. `memory_read`, `web_search`) and checks against
    the agent.tools registry. Unknown names → WARN (potential dead reference).
    """
    import re as _re
    consciousness = ROOT / "prompts" / "consciousness.txt"
    if not consciousness.exists():
        return []

    text = consciousness.read_text(encoding="utf-8", errors="replace")
    # Find `tool_name` style references (backtick-wrapped, snake_case, len > 4)
    candidates = set(_re.findall(r"`([a-z][a-z0-9_]{3,}[a-z0-9])`", text))
    # Filter to likely tool names (must have underscore, not env/config patterns)
    tool_candidates = {c for c in candidates if "_" in c and not c.startswith("l1") and not c.startswith("l2") and not c.startswith("l3")}

    if not tool_candidates:
        return []

    try:
        sys.path.insert(0, str(ROOT))
        from agent.tools import _registry
        known = set(_registry().keys())
    except Exception as e:
        return [f"Could not load tool registry for drift check: {e}"]

    unknown = sorted(tool_candidates - known)
    # Filter out common non-tool snake_case tokens that appear in prose
    _PROSE_TOKENS = {"source_stated", "inferred_confirmed", "inferred_unconfirmed",
                     "tool_use_failed", "tool_use", "next_review", "last_modified",
                     "memory_prune", "memory_write_tool"}
    unknown = [u for u in unknown if u not in _PROSE_TOKENS]
    if unknown:
        return [f"consciousness.txt references unknown tool: `{u}`" for u in unknown]
    return []


def check_replication_divergence() -> list[str]:
    """T-174: scan the replication log for insert rows where supabase != sqlite.

    Read-only, WARN-level. Flags silent half-writes where one store succeeded
    and the other failed — these are invisible without this check.
    """
    import json as _json
    log = ROOT / "data" / "memory_replication.log"
    if not log.exists():
        return []  # no log yet; first run or clean env — not an error

    divergent: list[str] = []
    try:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if entry.get("op") != "insert":
                continue  # expire/invalidate may legitimately differ
            sb = entry.get("supabase", "ok")
            sq = entry.get("sqlite", "ok")
            if sb != sq:
                divergent.append(f"  id={entry.get('id','?')[:12]} op=insert supabase={sb} sqlite={sq} ts={entry.get('ts','?')[:16]}")
    except Exception as e:
        return [f"replication log read error: {e}"]

    return divergent


def check_about_drift() -> list[str]:
    """T-168: warn when ABOUT.md AUTO-tagged counts diverge from ground truth.

    Uses <!-- AUTO:key -->VALUE<!-- /AUTO:key --> inline markers so the counts
    are machine-readable without restructuring the prose.
    """
    import re as _re
    about = ROOT / "ABOUT.md"
    if not about.exists():
        return ["ABOUT.md missing — cannot check drift"]

    text = about.read_text(encoding="utf-8", errors="replace")
    warnings: list[str] = []

    def _extract(key: str) -> int | None:
        m = _re.search(rf"<!--\s*AUTO:{key}\s*-->(\d+)<!--\s*/AUTO:{key}\s*-->", text)
        return int(m.group(1)) if m else None

    actual_closed = sum(1 for _ in (ROOT / "tickets" / "closed").glob("*.json"))
    actual_solutions = sum(1 for line in (ROOT / "solutions" / "SOLUTIONS.jsonl")
                           .read_text(encoding="utf-8").splitlines() if line.strip())

    doc_closed = _extract("closed_tickets")
    doc_solutions = _extract("solutions")

    if doc_closed is not None and abs(doc_closed - actual_closed) > 5:
        warnings.append(
            f"ABOUT.md closed_tickets={doc_closed} but tickets/closed/ has {actual_closed}"
        )
    if doc_solutions is not None and abs(doc_solutions - actual_solutions) > 5:
        warnings.append(
            f"ABOUT.md solutions={doc_solutions} but SOLUTIONS.jsonl has {actual_solutions}"
        )
    return warnings


def main() -> int:
    quiet = "--quiet" in sys.argv

    print("[verify] Syntax check...")
    syn_pass, syn_fail = syntax_check_all()
    if not quiet:
        for f in syn_fail:
            print(f"  SYNTAX FAIL: {f}")
    print(f"  {len(syn_pass)} ok, {len(syn_fail)} failed")

    print("[verify] Bare-except check (T-250)...")
    bare_except = check_bare_except()
    if bare_except:
        for f in bare_except:
            print(f"  BARE EXCEPT: {f}")
    print(f"  {len(bare_except)} bare except(s) found")

    print("[verify] Swallowed-exception ratchet (T-286)...")
    new_swallowed, swallowed_count, swallowed_baseline = check_swallowed_exceptions()
    if new_swallowed:
        for f in new_swallowed:
            print(f"  NEW SWALLOWED EXCEPTION: {f}")
        print(f"  {swallowed_count} found, baseline is {swallowed_baseline} — regression")
    else:
        print(f"  {swallowed_count} found (baseline {swallowed_baseline}, never increases)")

    print("[verify] Coherence gate (keystone, T-152)...")
    g_run, g_fail = run_gate()
    for f in g_fail:                       # always loud — this is the keystone
        print(f"\n  ⛔ {f}")
    print(f"  gate: {len(g_run)} run, {len(g_fail)} failed")

    print("[verify] Running non-costly tests...")
    t_run, t_skip, t_fail = run_tests()
    if not quiet:
        for f in t_fail:
            print(f"\n{f}")
    print(f"  {len(t_run)} run, {len(t_skip)} skipped, {len(t_fail)} failed")

    # T-166: behavioral test-coverage check (WARN only until T-159 clears)
    print("[verify] Test-coverage check (T-166, WARN)...")
    uncovered = check_test_coverage()
    if uncovered:
        if not quiet:
            print(f"  WARN: {len(uncovered)} source modules lack behavioral tests:")
            for m in uncovered:
                print(f"    {m}")
        else:
            print(f"  WARN: {len(uncovered)} modules uncovered (run without --quiet to list)")
    else:
        print(f"  all behavioral modules covered")

    # T-192: consciousness.txt tool-name drift check (WARN only)
    print("[verify] Consciousness tool-drift check (T-192, WARN)...")
    consciousness_warns = check_consciousness_tool_drift()
    if consciousness_warns:
        for w in consciousness_warns:
            print(f"  WARN: {w}")
    else:
        print(f"  no tool drift in consciousness.txt")

    # T-174: replication log divergence check (WARN only)
    print("[verify] Replication-log divergence check (T-174, WARN)...")
    rep_warns = check_replication_divergence()
    if rep_warns:
        if not quiet:
            print(f"  WARN: {len(rep_warns)} divergent insert(s) in memory_replication.log:")
            for w in rep_warns[:10]:
                print(w)
        else:
            print(f"  WARN: {len(rep_warns)} divergent insert(s) (run without --quiet to list)")
    else:
        print(f"  no divergence detected")

    # T-168: ABOUT.md doc-drift check (WARN only)
    print("[verify] ABOUT.md drift check (T-168, WARN)...")
    drift_warns = check_about_drift()
    if drift_warns:
        for w in drift_warns:
            print(f"  WARN: {w}")
    else:
        print(f"  ABOUT.md counts match ground truth")

    print(f"[verify] Writing {STATUS_OUT.relative_to(ROOT)}...")
    write_status(syn_pass, syn_fail + [f"bare except: {f}" for f in bare_except]
                 + [f"new swallowed exception: {f}" for f in new_swallowed],
                 t_run, t_skip, t_fail, g_run, g_fail)

    ok = not syn_fail and not bare_except and not new_swallowed and not t_fail and not g_fail
    verdict = "PASS" if ok else "FAIL"
    if g_fail:
        verdict += "  ⛔ coherence gate FAILED"
    print(f"[verify] {verdict}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
