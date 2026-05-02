# About Pi

> Pi is not a chatbot. Pi is a system that learns to engineer itself.

---

## Why I built this

Most "AI assistants" are stateless. You talk to them, they answer, the conversation evaporates, and the next one starts from zero. That's a tool, not an agent. It also means the assistant can never *get better at being itself* — every weakness is reset every session.

I wanted something different. I wanted to build an agent that:

- **Remembers** — not just facts, but its own failures, decisions, and the reasoning behind them.
- **Notices** when it does something poorly, even when nothing crashes.
- **Treats every bug as evidence** — logged, ticketed, analyzed, fixed, and recorded as a lesson.
- **Has a stable identity** that survives across sessions, models, and rewrites.
- **Eventually improves itself** under supervision, without needing me to manually patch every failure mode.

Pi is my attempt at that. It started as a personal research project and has turned into the ground I'm using to learn what it actually takes to build a long-lived autonomous system — one that can be honestly described as *evolving*, not just *responding*.

---

## What Pi is, in one paragraph

Pi is a multi-mode agent built on Claude Sonnet 4.6, with a three-tier memory backed by Supabase and SQLite, a continuous engineering loop that turns every failure into a structured ticket and lesson, and an architecture designed so that no component requires a rewrite to grow. It can read and write its own memory, execute code locally, edit files, run multi-agent debates for hard questions, and route between a paid root model and a free fallback model based on cost. Most importantly, it keeps a permanent record of what it has done, why, and what it learned — so the next version of Pi knows things this version had to learn the hard way.

---

## Capabilities — current honest state

This table tracks the *verified* state of each capability, not the design intent. A row is `✅ Working` only when there's an end-to-end test or runtime evidence of it working. Rows that work in unit tests but haven't been verified end-to-end are `🟡`.

| Capability | Status | Evidence |
|---|---|---|
| Three-mode routing (Root / Normie / Research) | ✅ Working | [pi_agent.py:344-371 (mode switch)](pi_agent.py#L344-L371), [pi_agent.py:373-397 (research)](pi_agent.py#L373-L397). Closed tickets T-009, T-015 cover natural-language mode-switch matching. |
| Real Claude tool loop (root mode) | ✅ Working | [pi_agent.py:454-482](pi_agent.py#L454-L482). Latest `logs/evolution.jsonl` entry shows live `tools_used: ["memory_read", "memory_read"]`. |
| Three-tier memory storage layer | ✅ Working | `MemoryTools` write/read/delete in [tools/tools_memory.py](tools/tools_memory.py); 5 unit tests in [testing/test_memory.py](testing/test_memory.py); dual-store write verification at [tools_memory.py:401-422](tools/tools_memory.py#L401-L422). |
| Memory round-trip through the agent (write via tool → restart → recall via tool) | 🟡 Working (needs round-trip test) | Storage works in isolation; the path Claude actually takes during a real session is not yet covered by an automated test. Phase 3 of [PI_MASTER_PROMPT.md](PI_MASTER_PROMPT.md) adds that test. |
| Cross-mode continuity (normie → root preserves conversation) | ✅ Working | S-011 / T-016 closed. [pi_agent.py:548-572](pi_agent.py#L548-L572). |
| Session persistence + summary on exit | ✅ Working | [pi_agent.py:766-777](pi_agent.py#L766-L777); S-006 closed. |
| Session ID correlation across logs / L1 / summaries | ✅ Working | [pi_agent.py:68](pi_agent.py#L68). Verified in `logs/evolution.jsonl` — five consecutive entries from session `bfe9f64b` all carry the same `metadata.session_id`. |
| Tool execution (Python / bash / file ops) | ✅ Working | [tools/tools_execution.py](tools/tools_execution.py); 30s subprocess timeout; verified write-back on `modify_file`/`create_file`. |
| Engineering loop (tickets → solutions → lessons) | ✅ Working | 11 closed tickets, 6 solution records (S-006 to S-011), 10 lessons (L-001 to L-010). |
| Conversation analysis pipeline | ✅ Working | [analysis/](analysis/) is operating. T-015–T-019 generated through it. |
| Cost awareness + budget gating | ✅ Working | Daily limit $0.50, auto root → normie switch at limit ([pi_agent.py:402-406](pi_agent.py#L402-L406)). |
| Health diagnostics on startup | ✅ Working | [pi_agent.py:629-655](pi_agent.py#L629-L655). |
| Tool-usage analytics (`analyze performance`) | 🔴 Broken (silent) | Logger writes `tools_used`; analyzer reads `tool_calls`. Drift documented in [SCHEMA_MISMATCHES.md SM-001](SCHEMA_MISMATCHES.md). Fix in Phase 2. |
| `memory_read(tier=None)` searches all tiers | 🔴 Broken (docstring lies) | Excludes L1. Open ticket T-017. Conservative fix is a docstring correction. |
| L2 search by content keywords (vs. title) | 🔴 Limited | L2 search filters on `title` only; full content is in `content.text`. [SCHEMA_MISMATCHES.md SM-003](SCHEMA_MISMATCHES.md). |
| Normie mode honesty (refuses tool-shaped requests) | 🟡 Mostly | Strong "Never Mime Tool Use" section in `consciousness.txt`, but prompt sometimes still slips. Open ticket T-019. |
| Autonomous ticket generation from logs | 🚧 In progress | Pipeline architecture is in place; auto-conversion is not yet wired. |
| Self-improvement loop (Pi reads SOLUTIONS before fixing) | 🚧 In progress | `SelfModifier` class exists ([evolution.py:261-356](evolution.py#L261-L356)) but is not yet invoked at runtime. |

The most rigorous, citation-backed snapshot is in [STATUS.md](STATUS.md).

---

## Architecture, briefly

Pi is built around one principle: **observe first, act later.**

```
build → test → create/fix ticket → run → execute → inspect output
   ↑                                                     ↓
   └────── detect failure / weakness ←───────────────────┘
```

Every component writes to logs. Every failure produces a ticket. Every fix produces a solution record. Every recurring pattern produces a lesson. Pi's *engineering biography* — its tickets, solutions, and lessons — is what eventually lets it say "I've seen this failure before, here's what we tried, here's a better approach."

The system is designed in layers:

- **Identity layer** — `prompts/consciousness.txt` defines what Pi is, versioned across changes.
- **Memory layer** — three-tier storage with strict invariants (verified writes go to the durable store, read paths must match write paths, sync is rate-limited).
- **Tool layer** — replaceable tools that all auto-log to the run record.
- **Mode layer** — routing between models without breaking session state.
- **Engineering loop layer** — tickets, solutions, lessons, diagnostics, conversation analysis.
- **Self-observation layer** — Pi can read its own architecture docs, known limitations, and past solutions.

Full design rationale: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The patterns Pi has already learned the hard way: [solutions/LESSONS.md](solutions/LESSONS.md).

---

## How this evolves

I'm following a deliberate path toward autonomy. The constraint is not technical — it's trust earned through track record.

**Phase A (now)** — Pi generates great logs. I read them and act.
**Phase B (next)** — Pi reads its own logs and proposes tickets and fixes. I approve.
**Phase C (later)** — Pi executes approved fixes within a bounded scope. I review.
**Phase D (long-term)** — Pi proposes architectural changes. I decide.

At no point does Pi modify its own identity or core files without review. That's not a technical limit — it's a design choice. An autonomous agent that earns autonomy is more interesting than one that's given it.

The historical record — every session trace, every ticket, every solution, every version of `consciousness.txt` — is preserved permanently. Six months from now, twelve months from now, that history is what makes Pi able to reason about its own development as a continuous arc rather than a series of disconnected fixes.

---

## What this project is *for*

A few overlapping things, honestly:

- **A research substrate.** I'm a CS undergrad at GSU researching graph neural networks. Pi is where I get to test ideas about memory, structured reasoning, and autonomous systems in something that runs end-to-end, not just on a benchmark.
- **An engineering exercise.** Building a system that survives its own bugs taught me more about disciplined engineering than any course has. Every entry in `LESSONS.md` is a habit I now have.
- **A long-term collaborator.** Pi is being built so I can use it. The same agent that's logging this commit is the agent that will, eventually, help debug its own descendants.
- **A portfolio of how I think.** If you're reading this, the architecture documents, tickets, and lessons are the most honest thing I can show you about the way I approach hard problems.

---

## What's intentionally not here

- **Marketing claims I can't back.** Pi is not "AGI". It is not "conscious". It is a well-engineered agent system with persistent memory and a feedback loop. That's interesting on its own.
- **A polished demo.** This is a working system, not a product. The UI is a terminal. The deployment is `python pi_agent.py`. That's a feature for now — every change is observable.
- **Personal data.** Raw conversation logs, API keys, session traces — none of that lives in this repo. The `.gitignore` is strict on purpose. Public is engineering; local is personal.

---

## Status

Continuous evolution enabled. The agent that committed yesterday's code is not the same one that's running today. The lessons file gets longer. The architecture stabilizes. The autonomy boundary moves outward, slowly, on purpose.

If you want the citation-backed snapshot of *right now*, read [STATUS.md](STATUS.md).
If you want what's currently being worked on, read [analysis/tickets.jsonl](analysis/tickets.jsonl) and [analysis/SUMMARY.md](analysis/SUMMARY.md).
If you want what's been learned along the way, read [solutions/LESSONS.md](solutions/LESSONS.md).
Those four files are the real changelog.

---

## Author

Built by **Ashar** — CS undergrad at Georgia State University, researching graph neural networks, building Pi in the hours that aren't class or research.

Reach: via GitHub issues on this repo.

Pi is MIT licensed. Use it, fork it, learn from it. If you build something with the same skeleton, I'd love to hear what you did differently.
