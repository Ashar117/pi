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

### Schema

```json
{
  "id": "T-NNN",
  "title": "One-sentence description",
  "status": "open | closed",
  "severity": "critical | high | medium | low | p3",
  "opened": "YYYY-MM-DD",
  "closed": "YYYY-MM-DD or null",
  "phase_closed": null,
  "root_cause": "What actually went wrong (not symptoms)",
  "fix": {
    "files": ["list of files changed"],
    "description": "What was done"
  },
  "verification": {
    "tests": ["list of tests that prove the fix"],
    "notes": "any additional verification steps"
  },
  "solution_record": "S-NNN or null",
  "lesson": "L-NNN or null"
}
```

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

Mark tests that hit live APIs with `@pytest.mark.costly`. These run once per change, not in the per-commit suite. The free suite (everything not marked costly) runs automatically via `scripts/verify.py`.

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
3. Build under {module}/ following docs/templates/MODULE_TEMPLATE.py
4. Run python scripts/verify.py — everything still green
5. Run the new feature's tests — they now pass
6. Append to solutions/SOLUTIONS.jsonl
7. Update docs/ARCHITECTURE.md module list
8. Move ticket to tickets/closed/
```

### Module structure

New modules live under their own directory (e.g., `integrations/gmail/`). Follow the module template. Modules must:
- Import from `app/config.py` for credentials
- Return `{"success": bool, "error": str | None}` from every public method
- Write significant operations to memory (`tier="l3"`, `category="<module_name>"`)

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
