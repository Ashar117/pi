# Pi — Conversation Analysis Summary

Running synthesis of patterns observed across `chat_logs.txt`. Updated whenever a new pattern is identified or an existing one accumulates more evidence.

## How this file is used

- One section per **pattern** (recurring failure mode), not per session.
- Each pattern lists the ticket IDs that surfaced it.
- When a pattern is fixed by a solution, mark it resolved with the S-XXX reference.
- Trends matter more than individual incidents — three weak answers about memory recall mean a memory issue, not three coincidences.

---

## Pattern catalog (start empty, fills as logs come in)

_No patterns observed yet. First entries will appear here once Ash adds a chat log and analysis runs._

---

## Severity guide

- **Critical** — breaks a documented capability (e.g., memory write succeeds but recall fails). Always becomes a P0/P1 ticket.
- **High** — Pi gives a confidently wrong answer or hallucinates a tool result. P1.
- **Medium** — Pi answers the wrong question, drifts, or loses continuity within a session. P2.
- **Low** — verbosity, tone, or style drift. P3.

## Recurrence threshold

A pattern is *recurring* once it appears in 2+ independent sessions. Recurring patterns get escalated and may warrant an L-XXX lesson entry in `solutions/LESSONS.md`.
