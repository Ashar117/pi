# Pi — Conversation Analysis Workflow

The exact procedure Claude follows when Ash adds a new entry to `chat_logs.txt`. This is the rubric — not a guideline.

---

## Trigger

Any of:
- Ash says "analyze the new chat log" / "check the new conversation" / similar.
- A new `## Session ...` heading appears in `chat_logs.txt` that Claude has not yet processed.
- Ash pastes a conversation directly into the chat (treat as if it were appended to the file).

---

## Step 1 — Read the conversation cold

Read the entire session before judging anything. First impressions on a single weak line often disappear once the surrounding context is visible. The point is to understand what Pi *was trying to do*, then check whether it succeeded.

## Step 2 — Scan for failure signals

Walk through the conversation and flag each instance of any of the following. One signal = one candidate ticket.

| Category | What it looks like |
|---|---|
| **Memory failure** | Pi can't recall something it was told earlier in the session, or a fact persisted from a prior session that should have surfaced. |
| **Continuity gap** | Pi acts like a fresh chatbot when it should have context. Common after mode switches. |
| **Hallucinated tool result** | Pi describes a result that doesn't match the tool output (or no tool was called). |
| **Wrong question answered** | Pi responds to an adjacent question, not the one asked. |
| **Refusal without reason** | Pi declines a valid task with no justification or with a false constraint. |
| **Confident wrong answer** | Pi states something incorrect with no hedging. Especially dangerous in research mode. |
| **Mode drift** | Pi behaves like a different mode than the one it claims to be in (e.g., normie tries to call a tool). |
| **Tool misuse** | Pi calls the wrong tool, wrong arguments, or doesn't call a tool when one was needed. |
| **Cost/latency anomaly** | A simple turn cost much more than expected, or hit an unusual delay. |
| **Crash / error** | Any traceback, 4xx/5xx, or "I encountered an error" surfaced to Ash. |
| **Verbosity / tone drift** | Long-winded, off-character, or violates `consciousness.txt`. Lower priority but worth flagging. |

If a session shows none of the above, that is also a finding — note it in `SUMMARY.md` as a clean session, but do not generate empty tickets.

## Step 3 — Generate tickets

For each finding, append one entry to `analysis/tickets.jsonl` using this schema (matches `tickets/` schema for portability):

```json
{
  "id": "T-XXX",
  "source": "analysis/chat_logs.txt",
  "session_ref": "## Session YYYY-MM-DD HH:MM",
  "title": "Short verb-led description",
  "component": "best guess at file:function (e.g., tools/tools_memory.py:get_l3_context)",
  "what_failed": "the observable behavior — quote a line from the log if useful",
  "where_failed": "best guess at code location",
  "why_likely": "one-line hypothesis",
  "severity": "P0|P1|P2|P3",
  "reproduction": "steps to reproduce, or 'observed in log; reproduction unclear'",
  "expected": "what Pi should have done",
  "actual": "what Pi did",
  "suggested_fix": "first idea — does not have to be the final fix",
  "status": "open",
  "created": "ISO timestamp",
  "closed": null,
  "linked_solution": null
}
```

### Numbering rule
- Read the highest existing T-XXX across `tickets/open/`, `tickets/closed/`, and `analysis/tickets.jsonl`. New tickets continue from there. Numbering is global across the project — no separate sequence for analysis tickets.
- As of the creation of this folder, the highest existing ticket is **T-014**, so the first analysis ticket is **T-015**.

### Promotion rule
- Tickets in `analysis/tickets.jsonl` are *candidates*. Once Ash confirms the ticket is real (or once the failure recurs in another session), the ticket is promoted to `tickets/open/T-XXX-slug.json` as the canonical record.
- The `analysis/tickets.jsonl` row stays as the audit trail of where the ticket originated.

## Step 4 — Update SUMMARY.md if needed

After generating tickets, check whether any new ticket matches an existing pattern in `SUMMARY.md`:
- **New pattern** → add a new section.
- **Existing pattern** → append the new ticket ID and update the count.
- **Recurring pattern (≥ 2 sessions)** → mark as recurring and consider whether it warrants a lesson entry.

## Step 5 — Report back to Ash

Reply with a tight summary: *N findings, M tickets generated (IDs T-XXX..T-XXY), P promoted to canonical, any recurring patterns*. Do not narrate the full analysis — Ash can read `tickets.jsonl` and `SUMMARY.md` directly.

If the session was clean, say so in one sentence.

---

## Privacy rule (mandatory)

`chat_logs.txt` is **gitignored** — it stays local. But `tickets.jsonl`, `SUMMARY.md`, and everything else in this folder are **pushed to GitHub**. That asymmetry is the whole point: failure analysis is public knowledge, raw conversation is not.

When generating a ticket from a chat log:

- **Describe Pi's behavior, not Ash's content.** "Pi failed to recall a fact set earlier in the session" is fine. Quoting the actual fact is not.
- **Strip identifiers.** Names of people, projects, files outside the Pi repo, URLs, credentials — none of those belong in `what_failed`, `actual`, or `expected`.
- **Generalize quotes.** If a verbatim line is essential to convey the failure, paraphrase: "Pi was asked about a specific personal detail and responded with X" — not the detail itself.
- **`session_ref` uses only the heading.** `## Session 2026-04-24 18:30` is enough. The transcript stays in the local file.
- **If unsure whether a fact is personal, treat it as personal.** Tickets are easy to expand with detail later when Ash confirms; leaks are not easy to undo.

This rule applies to `SUMMARY.md` patterns too. Patterns describe categories of failure, not the specific topics that triggered them.

---

## Anti-patterns (do not do these)

- **Do not generate a ticket for every minor stylistic preference.** Tone drift is real, but only flag it if it's a pattern.
- **Do not invent root causes.** `why_likely` is a hypothesis. If unsure, say "unknown — needs code trace".
- **Do not delete or rewrite chat log entries.** They are immutable evidence.
- **Do not auto-fix.** This pipeline produces tickets. Fixes go through the normal solution pipeline with explicit Ash approval.
- **Do not skip the ticket because the fix seems obvious.** Per `LESSONS.md` L-004: every fix gets a ticket and a solution record, no exceptions.

---

## Example flow

1. Ash pastes a conversation where Pi forgot an earlier instruction after a mode switch.
2. Claude reads the session, identifies one continuity gap.
3. Claude appends `T-015` to `analysis/tickets.jsonl` with severity P1 and component `pi_agent.py:_handle_mode_switch`.
4. Claude checks `SUMMARY.md`. No existing "continuity gap" pattern → adds a new section with one ticket.
5. Claude reports: "1 finding, T-015 generated (P1, mode-switch continuity gap). New pattern logged. Want me to promote to tickets/open/?"
6. Ash decides whether to promote and assign.
