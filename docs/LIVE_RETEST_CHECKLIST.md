# LIVE_RETEST_CHECKLIST.md
*Tests that require live API quota. Run manually when quota is available.*

---

## T-024 — Normie greeting misfire

**Test file:** `testing/test_normie_no_misfire.py`
**Why costly:** hits real Groq Llama 3.3 70B API (no mock)
**Quota concern:** Groq free-tier daily limit (~14,400 RPD, ~1M TPD). Hits limit quickly with 12 parametrised tests.

**Command:**
```
cd e:\pi
python -m pytest testing/test_normie_no_misfire.py -v
```

**Pass criteria:**
- 12/12 tests pass
- No test returns a response that contains "normie", "root mode", "I can't", "don't have access", or any unprompted limitation announcement
- Greetings like "hey", "sup", "what's good", "morning" get a normal conversational reply

**Fail criteria:**
- Any test fails — meaning Groq still misfires on that greeting style
- Rate limit hit before all 12 tests run → re-schedule for next day

**If it fails:** The normie `mode_block` in `agent/prompt.py` needs further tuning. Check which specific greeting triggered the misfire and add it to the test parametrize list.

---

## T-027 — Memory query keyword formulation

**Test file:** `testing/test_query_formulation_v2.py`
**Why costly:** hits real Claude API (claude-sonnet-4-6 or similar)
**Quota concern:** Uses Claude API — token cost per run. Low volume (4 tests, ~4 Claude calls).

**Command:**
```
cd e:\pi
python -m pytest testing/test_query_formulation_v2.py -v -m costly
```

**Pass criteria:**
- 4/4 tests pass
- Statements like "I followed up" and "I'm planning a trip" produce `query=""` (no prefetch fired)
- A question like "what are my deadlines?" produces query `"deadline"` (singular normalised, not `"deadlines"`)
- Zero results on first try triggers a retry with synonym/singular

**Fail criteria:**
- Any test returns a non-empty query for a statement input
- Plural not normalised (query is `"deadlines"` not `"deadline"`)
- Prefetch fires on generic filler words like "location" or "things"

**If it fails:** Re-examine `_prefetch_memory` in `pi_agent.py` — the stop-words set or RECALL_SIGNALS check.

---

## General notes

- Both tests are in `COSTLY_TESTS` in `scripts/verify.py` so `python scripts/verify.py` will skip them.
- To run all costly tests at once: `python -m pytest testing/ -v -m costly`
- Groq rate limit resets at midnight Pacific time.
- Do not run costly tests in the same session as a heavy root-mode conversation — they share quota.
