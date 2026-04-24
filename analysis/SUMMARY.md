# Pi — Conversation Analysis Summary

Running synthesis of patterns observed across `chat_logs.txt`. Updated whenever a new pattern is identified or an existing one accumulates more evidence.

## How this file is used

- One section per **pattern** (recurring failure mode), not per session.
- Each pattern lists the ticket IDs that surfaced it.
- When a pattern is fixed by a solution, mark it resolved with the S-XXX reference.
- Trends matter more than individual incidents — three weak answers about memory recall mean a memory issue, not three coincidences.

---

## Pattern catalog

### P1 — Silent intent-parse failures cascading into hallucinated capabilities
**Tickets:** T-015 (closed), T-019 (open)
**First seen:** 2026-04-24 (session e9197064)
**Status:** Root cause fixed (T-015). Secondary mitigation pending (T-019 — prompt hardening so normie refuses tool-shaped requests instead of miming).
**Pattern:** When the agent cannot recognise a command or capability request, the underlying LLM does not refuse — it mimes the action in text, producing a fluent, structured, completely fake response. The user trusts the response and the system silently diverges from reality.
**Where this shows up:** Mode switches (T-015), memory writes in normie (T-019). Likely also tool-shaped requests in normie generally.
**Recurring:** Once. Watch for more sightings before declaring a class-wide fix.

### P2 — Cross-mode continuity breaks
**Tickets:** T-016 (closed)
**First seen:** 2026-04-24 (session e9197064)
**Status:** Fixed by S-011.
**Pattern:** Behaviour that looks correct inside a single mode breaks at mode boundaries because the two paths share state through different stores. Diagnosable only by holding a real conversation across a switch.
**Recurring:** Once.

### P3 — Memory tier behaviour does not match documented contract
**Tickets:** T-017 (open)
**First seen:** 2026-04-24 (session e9197064)
**Status:** Open. Conservative fix is a docstring correction; aggressive fix is including L1 in implicit searches.
**Pattern:** Function contracts (docstrings, schemas) describe ideal behaviour; the code implements a subset. Pi reasons about its own capabilities from the contracts, so contract drift directly causes wrong self-descriptions.
**Recurring:** Once.

---

## Severity guide

- **Critical** — breaks a documented capability (e.g., memory write succeeds but recall fails). Always becomes a P0/P1 ticket.
- **High** — Pi gives a confidently wrong answer or hallucinates a tool result. P1.
- **Medium** — Pi answers the wrong question, drifts, or loses continuity within a session. P2.
- **Low** — verbosity, tone, or style drift. P3.

## Recurrence threshold

A pattern is *recurring* once it appears in 2+ independent sessions. Recurring patterns get escalated and may warrant an L-XXX lesson entry in `solutions/LESSONS.md`.
