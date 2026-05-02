# Archive — 2026-04-25 (Phase 1 docs collapse)

This folder holds documentation that was once active at the repo root but no longer reflects the current state of Pi. Nothing here is deleted — it's preserved as the historical record of how the project looked at the time of archive. Future-Ash, future-Pi, or anyone reviewing the engineering arc should be able to read these and understand what was thought at the time, and why it was superseded.

The full reasoning for this archive sweep is in [STATUS.md](../../../STATUS.md), [RECONCILIATION.md](../../../RECONCILIATION.md), and [CONTRADICTIONS.md](../../../CONTRADICTIONS.md). What follows is the per-file rationale.

## Files archived in this batch

### Stale fix-spec docs (4)
These four documents describe a P0 "tool hallucination crisis" and a fix path through new modules under `llm/` and `memory/`. The fix path was never executed: the actual tool wiring landed directly in [pi_agent.py:140-238](../../../pi_agent.py#L140-L238) and [pi_agent.py:454-482](../../../pi_agent.py#L454-L482). The architecture pivoted; the docs lagged. They are accurate as a record of what was being planned in the 24 hours before S-010 / S-011 landed, and inaccurate as a description of the codebase that resulted.

| File | Status field at archive | Why archived |
|---|---|---|
| `ARCHITECTURE_FIX.md` | "Status: CRITICAL FIX REQUIRED" | Specifies fixes via `llm/tools.py`, `llm/tool_executor.py`, modifications to `llm/routing.py`, plus a `memory` SQL table — none of those files or that table exist. The actual fix is in `pi_agent.py`. |
| `CRITICAL_FIX_TICKET.md` | "Severity: BLOCKING" | Same root cause analysis as `ARCHITECTURE_FIX.md`, narrower scope. Same architectural drift from the as-built code. |
| `ARCHITECTURE_ADDENDUM.md` | "P0 — BLOCKING ALL MEMORY FUNCTIONALITY" | Says "this addendum supersedes all previous memory architecture documentation until the fix is complete." The fix is complete — but via a different path. |
| `VSCODE_CLAUDE_PROMPT.md` | "Priority: CRITICAL" | A 1000-line implementation prompt for the un-taken fix path. Following it against the current repo would create a parallel, incompatible second tool path. |

### Superseded fix-sprint docs (5)
These five describe a 4-document, 5-ticket fix sprint from 2026-04-20 (`Tickets #001–#005` in `FAILURE_TICKETS.txt`). That sprint has been fully replaced by the current engineering loop in [analysis/](../../../analysis/), [solutions/](../../../solutions/), [tickets/closed/](../../../tickets/closed/), and the canonical [docs/ARCHITECTURE.md](../../ARCHITECTURE.md). The original 5 tickets are partially or fully resolved by the current code; the per-ticket reconciliation is in [CONTRADICTIONS.md C-011](../../../CONTRADICTIONS.md).

| File | Why archived |
|---|---|
| `VSCODE_MASTER_PROMPT.txt` | Older general-purpose anti-hallucination workflow. Superseded by [PI_MASTER_PROMPT.md](../../../PI_MASTER_PROMPT.md). |
| `DEPLOYMENT_PROTOCOL.txt` | Step-by-step guide for the abandoned 4-6h fix run. References `VSCODE_CLAUDE_FIX_COMMANDS.txt` (never existed). |
| `EXECUTIVE_SUMMARY.txt` | Marketing-style summary of the same sprint package. |
| `FAILURE_TICKETS.txt` | The 5 original tickets. Per [CONTRADICTIONS.md C-011](../../../CONTRADICTIONS.md), most are resolved by the current engineering loop. Anything still live is already represented in [analysis/tickets.jsonl](../../../analysis/tickets.jsonl) (T-017, T-018, T-019). |
| `TESTING_FRAMEWORK.txt` | Framework spec — superseded by the actual files in [testing/](../../../testing/). |

### Superseded canonical (1)
| File | Why archived |
|---|---|
| `ARCHITECTURE.v1.md` (originally `ARCHITECTURE.md`) | First-pass architecture reference. Replaced by the merged [docs/ARCHITECTURE.md](../../ARCHITECTURE.md), which folds in the canonical content from this file plus the engineering-loop rigour from `ARCHITECTURE_DIRECTION.md`. |

### Superseded canonical (2)
| File | Why archived |
|---|---|
| `ARCHITECTURE_DIRECTION.v1.md` (originally `ARCHITECTURE_DIRECTION.md`) | The 2026-04-20 canonical engineering-loop design doc. Its content is folded into the merged [docs/ARCHITECTURE.md](../../ARCHITECTURE.md). Preserved here because the version history matters: this is where the engineering loop, memory invariants, and "build now / build later" partition were first articulated. |

### Superseded canonical (3)
| File | Why archived |
|---|---|
| `USER_GUIDE.v1.md` (originally `USER_GUIDE.md` at the repo root) | Older user guide. Replaced by [docs/USER_GUIDE.md](../../USER_GUIDE.md), rewritten to match the actual commands fired by [pi_agent.py:process_input](../../../pi_agent.py#L341-L399) — including the loose-matcher mode-switch variants from S-010. |

---

## Rules for this archive folder

1. **Do not edit anything in this folder.** Per master prompt §4, files under `_archive/` are immutable.
2. **Do not delete.** Anything moved here is part of the engineering history.
3. **If you need to reference one of these files in current work, link to it from `docs/_archive/2026-04-25/`** — never copy content back into a live doc without reconciling against the current code first.

## How to find the live equivalent

| If you came looking for… | Read this instead |
|---|---|
| The current architecture | [docs/ARCHITECTURE.md](../../ARCHITECTURE.md) |
| The current command list / how to run Pi | [docs/USER_GUIDE.md](../../USER_GUIDE.md) |
| Current open bugs | [analysis/tickets.jsonl](../../../analysis/tickets.jsonl), [tickets/open/](../../../tickets/open/) |
| Current solutions and lessons | [solutions/SOLUTIONS.jsonl](../../../solutions/SOLUTIONS.jsonl), [solutions/LESSONS.md](../../../solutions/LESSONS.md) |
| One-page repo state | [STATUS.md](../../../STATUS.md) |
| Operating protocol for VS Code Claude | [PI_MASTER_PROMPT.md](../../../PI_MASTER_PROMPT.md) |
