# KNOWN_DEBT.md
*Catalogue of known issues, duplicate paths, and deferred work as of 2026-05-03.*

---

## 1. Duplicated runtime paths — `pi_agent.py` vs `agent/respond.py`

**Severity:** High (maintenance risk)
**Impact:** Every future normie/root fix must be applied twice or the dead copy silently diverges.

`pi_agent.py` contains `_respond_root()` and `_respond_normie()` as instance methods (~lines 267–472).
`agent/respond.py` contains `respond_root()` and `respond_normie()` as module functions.

The agent calls `self._respond_root()` / `self._respond_normie()` directly. `agent/respond.py` is **never imported** anywhere (confirmed: `grep -r "from agent.respond" .` → 0 matches outside the file itself).

Both copies were kept in sync for T-025 (Groq typed error handling). Future changes to error handling, L1 logging, message history, or the tool loop must be applied to `pi_agent.py` — not `agent/respond.py`.

**Correct future fix:** Make `pi_agent.py::_respond_root` call `agent/respond.py::respond_root(self, ...)` and delete the inline implementation. Then `agent/respond.py` becomes the single source of truth. Requires careful testing — do not attempt mid-session.

---

## 2. Stale tool count in root mode system prompt

**Severity:** Low (cosmetic, no functional impact)
**File:** `agent/prompt.py` line 38

```python
MODE: ROOT | MODEL: Claude Sonnet 4.6 | TOOLS: All 7 ENABLED
```

There are now 22 tools registered (confirmed: `get_tool_definitions()` returns 22 entries including `system_introspect` added in T-028). The "7" is from an earlier phase.

Claude ignores this number — it sees the actual tool list in the API call. But it's misleading in logs and when reading the prompt.

**Fix:** Change `All 7` to `All` or the actual count. Safe change, 1 line. Deferred because prompt changes require re-running costly normie tests to confirm no regression.

---

## 3. Pre-existing failing test — `test_analyze_performance_command`

**Severity:** Medium (CI always shows 1 failure)
**File:** `testing/test_agent_golden.py::test_analyze_performance_command`
**Error type:** `json....` (truncated — likely `json.JSONDecodeError`)

**Root cause (not yet confirmed):** `evolution.analyze_performance()` in `evolution.py` line 77 calls `datetime.fromisoformat(entry["timestamp"])` on every log entry. Older entries may use timezone-naive timestamps or the literal `Z` suffix that older Python didn't support. A single bad entry causes the whole method to raise, which `_performance_report()` doesn't catch — it only catches `"error"` key in the return value.

**How to investigate next session:**
```python
from evolution import EvolutionTracker
e = EvolutionTracker()
e.analyze_performance(days=7)  # should raise or return error
```
Then inspect `logs/evolution.jsonl` around lines that have unusual timestamp formats.

**Fix path:** Either wrap the `fromisoformat` call in a try/except (continue on bad entries), or normalise timestamps when writing in `log_interaction()`.

---

## 4. `llm/routing.py` — confirmed dead code

**File:** `llm/routing.py`
**Status:** Orphaned router from early architecture. NOT imported anywhere.
**Evidence from docstring:** `grep -r "from llm.routing|import routing|from llm import" . → 0 matches`

The `\|` SyntaxWarning in that file was fixed (changed to `|` in the docstring) but the file itself still imports `ollama` and `anthropic` at module level, uses `claude-haiku-4-6` (old model name), and contains a dead `__main__` smoke-test block.

**Safe to delete** the entire implementation body (keep only the `__main__` block or delete all). Do not delete until confirmed nothing in `.claude/`, `testing/`, or any config file references it.

---

## 5. ~~`scripts/verify.py` scans `.claude/worktrees/`~~ FIXED

**Resolved 2026-05-03.** Added `".claude"` to the exclusion tuple in `scripts/verify.py` line 45. Verify now scans 57 true project .py files (was inflated to 97 by worktree copies). No stderr warnings.

---

## 6. `_prefetch_memory` stop-words list is hand-accumulated

**File:** `pi_agent.py` lines 230–243
**Issue:** Stop-words set is a flat inline literal with no documentation on which words were added by which ticket. T-027 appended 14 words. Future tickets may add more, making it increasingly hard to audit.

**Deferred fix:** Extract to a named module-level constant with a comment per group. No behavior change — pure organisation.

---

## 7. `test_normie_honesty.py` in COSTLY_TESTS without corresponding closed ticket

**File:** `testing/test_normie_honesty.py`, `scripts/verify.py` COSTLY_TESTS
**Issue:** This test was in COSTLY_TESTS before the T-024 batch. It's unclear what bug it covers or whether T-024's prompt rewrite makes it redundant.

**Action needed:** Read `test_normie_honesty.py`, identify what it asserts, determine if it overlaps with `test_normie_no_misfire.py`. If redundant, remove it. If complementary, add a ticket cross-reference.
