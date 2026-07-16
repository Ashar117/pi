# Contributing to Pi

Pi is built around an engineering loop. Every feature, fix, and improvement follows the same cycle — this document describes how to work within it.

---

## The engineering loop

```
open ticket → write reproduction test (fail) → fix → verify green → solution record → lesson (if recurring) → close ticket
```

This is not optional ceremony. The loop is why Pi gets better instead of just getting bigger.

---

## Tickets

### When to open one

Open a ticket when:
- Something is broken or behaving incorrectly
- A known limitation needs tracking
- A new feature is planned (so it can be tested before it's built)

### Schema (current — copy a recent file in `tickets/closed/` if in doubt)

```json
{
  "id": "T-NNN",
  "title": "One-sentence description",
  "component": "file or area it touches",
  "severity": "P0 | P1 | P2 | P3",
  "source": "who/what filed it and when",
  "current_state": "what is true today, with file:line evidence",
  "target_state": "what done looks like",
  "depends_on": ["T-MMM"],
  "status": "open | closed",
  "created": "ISO timestamp",
  "migration_plan": ["concrete ordered steps a cold session can execute"],
  "risk_notes": "failure modes + constraints",
  "root_cause_confidence": "verified | hypothesis  (optional; sprint.py's auto-run gate reads it)",
  "resolution": "implemented  (set on close)",
  "solution_id": "S-NNN  (set on close)",
  "closed": "ISO timestamp  (set on close)"
}
```

**Quality bar:** a ticket must be executable by a session with zero prior context — evidence in `current_state`, steps in `migration_plan`, the proving test named. This is what lets any model, cheap or expensive, run the loop.

### Where tickets live

- Open: `tickets/open/T-NNN-{slug}.json`
- Closed: `tickets/closed/T-NNN-{slug}.json`

Candidate tickets (not yet formal) can live in `analysis/tickets.jsonl` until promoted.

---

## Solution records

Solution records are the project's institutional memory. They answer "what happened, what did we try, and what did we learn?"

### When to write one

Write a solution record every time you close a ticket via a code or prompt change. Small docstring fixes can share a record with related changes.

### Schema reference: `docs/templates/SOLUTION_TEMPLATE.json`

Key fields:
- `problem` — what was broken, with evidence (log excerpts, test output, line references)
- `countermeasure` — exactly what was changed and why
- `result` — `"worked"` or `"partial"` or `"wrong"` (be honest)
- `better_future_fix` — what a better version would look like
- `lessons` — 2-3 specific lessons as strings

Append to `solutions/SOLUTIONS.jsonl` — one JSON object per line, IDs are sequential (S-006, S-007...).

---

## Lessons

A lesson is written when a pattern recurs — the second time a class of bug bites, it graduates to `LESSONS.md`.

### When to write one

Write a lesson when:
- The same class of bug appears in two different tickets
- A solution record contains a lesson that applies beyond this specific fix
- A Phase checkpoint identifies a structural insight

### Schema reference: `docs/templates/LESSON_TEMPLATE.md`

Append to `solutions/LESSONS.md`. IDs are sequential (L-001, L-002...).

---

## Tests

### Behavioural vs unit

| Type | What it tests | Example |
|---|---|---|
| **Unit** | A single function with mocked dependencies | `MemoryTools.memory_write()` with a mocked Supabase client |
| **Behavioural** | What the agent actually does end-to-end | `PiAgent.process_input("remember X")` in normie mode → assert no banned phrases |

Unit tests catch code regressions. Behavioural tests catch prompt regressions, LLM behaviour drift, and integration failures. Both matter; neither replaces the other.

### Costly vs free

Tests that hit live APIs (real Claude/Groq/Supabase calls) are listed in the `COSTLY_TESTS` set inside [scripts/verify.py](../scripts/verify.py) and excluded from the gate — run them by hand when needed ([docs/LIVE_RETEST_CHECKLIST.md](LIVE_RETEST_CHECKLIST.md) tracks them). Everything else runs automatically via `scripts/verify.py`.

Prefer behavioural tests that drive real handlers/closures over string-greps on source files — grep-tests pass forever and prove nothing (the Telegram T-244–T-248 bug cluster shipped straight through green grep-tests).

### Naming convention

```
testing/test_{what_it_tests}.py
```

Each test file has a module-level docstring explaining what it tests, why, and whether it's costly.

### Reproduction-first rule

Before writing the fix, write the test that proves the bug exists. Run it. Watch it fail. Then fix. Then watch it pass. This is not optional — a fix without a failing test is unverifiable.

---

## Adding a new feature

```
1. Open tickets/open/T-NNN-{feature}.json with the feature spec
2. Write the tests first — they will fail, that's the point
3. Build it (new tool = a ToolSpec entry in the owning tools/tools_*.py module's TOOLS list — ADR-002)
4. Run python scripts/verify.py — must print PASS (never pipe it, T-214)
5. Append to solutions/SOLUTIONS.jsonl
6. Move ticket to tickets/closed/; run python scripts/refresh_pi.py
7. If user-visible, update the ABOUT.md capability table honestly (✅ only if live-verified)
```

### Tool/module conventions

- New tools live in the owning `tools/tools_*.py` module — do **not** create a new directory or touch a central dispatch table.
- Credentials come from `app/config.py` / `.env` (never hard-coded; never edit those files without Ash's explicit go).
- Every public method returns `{"success": bool, "error": str | None, ...}`.
- Swallowed exceptions must call `track_silent(category, e)` ([agent/observability.py](../agent/observability.py)); a bare `except:` fails verify.py outright.

---

## Archiving vs deleting

**Never delete.** Archive.

- Stale docs → `docs/_archive/YYYY-MM-DD/`
- Dead code modules → `archive/YYYY-MM-DD/`
- Old test files → rename with `_archived_YYYYMMDD` suffix

Deleted files lose git history. Archived files keep it and can be bisected.

---

## The CHANGELOG habit

Every session that modifies a runtime file appends one line to `docs/CHANGELOG.md`:

```
YYYY-MM-DD | file.py | one-sentence summary of what changed
```

When starting a new Claude chat session working on this project, paste the last 10 lines of `docs/CHANGELOG.md` into the first turn. This gives every chat a shared ground truth without replaying full history.

---

## Running verify.py

```bash
python scripts/verify.py
```

Run this:
- After every session that modifies runtime code or prompts
- Before opening a PR or pushing to main
- Any time you're unsure if something regressed

The script writes `docs/STATUS.md` with the full result. If it exits non-zero, do not push.
