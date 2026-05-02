# CHECKPOINT — phase-1-complete

**Phase:** 1 — Docs collapse
**Session ID:** N/A (no `pi_agent.py` runs in this session)
**Date:** 2026-04-25
**Duration:** continuation of the Phase-0 session (no break)

## Did

- Created [docs/](../docs/) and [docs/_archive/2026-04-25/](../docs/_archive/2026-04-25/) directory structure.
- Wrote new canonical documents:
  - [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) — merged from `ARCHITECTURE.md` v1 + `ARCHITECTURE_DIRECTION.md` v1; corrects all overclaims against [STATUS.md](../STATUS.md) findings; structured around the engineering loop, with file-responsibility table, memory invariants, mode system, tool list, and a §11 honest naming of the round-trip test gap.
  - [docs/USER_GUIDE.md](../docs/USER_GUIDE.md) — rewritten to match actual commands fired by [pi_agent.py:process_input](../pi_agent.py#L341-L399), including loose-matcher mode-switch variants from S-010, the daily cost auto-switch, and explicit naming of the SM-001/SM-003/SM-004/T-019 limitations users will hit.
  - [docs/_archive/2026-04-25/README.md](../docs/_archive/2026-04-25/README.md) — per-file rationale for every archived file, with cross-links to where the live equivalent now lives.
  - [.env.example](../.env.example) — every variable [app/config.py](../app/config.py) reads, marked required / optional / reserved.
- Proposed a 12-file archive batch with BEFORE / AFTER / rationale / verifications. Ash approved with "go".
- Executed all 12 moves: 8 via `git mv` (preserves history — show as `R` in `git status`), 4 untracked stale fix docs via `mv`.
- Ran all 5 verifications — every one passed.
- Rewrote [README.md](../README.md): canonical/legacy/audit-trail repo map; status section names what's working / broken / unverified with citations to [STATUS.md](../STATUS.md) and [SCHEMA_MISMATCHES.md](../SCHEMA_MISMATCHES.md); points at [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) and [STATUS.md](../STATUS.md) as the entry points.
- Rewrote [ABOUT.md](../ABOUT.md): capability table downgraded — every row now backed by either runtime evidence (`logs/evolution.jsonl` excerpt, file:line citation) or a closed ticket. Added `🟡 Working (needs round-trip test)` for memory recall through the agent (per master prompt §1.10 instruction). Added 4 explicitly broken rows (SM-001, T-017, SM-003, T-019) with severity flags.

## Verified

| Check | Result |
|---|---|
| 12 source paths gone from root | `12` (count) |
| 12 destination paths populated under `docs/_archive/2026-04-25/` | confirmed via `ls` |
| `git status --short`: 8 tracked moves show as `R`, 4 untracked appear as new under archive path | confirmed |
| No live root doc still references the moved file paths | confirmed via Grep — only references in [docs/_archive/2026-04-25/README.md](../docs/_archive/2026-04-25/README.md), Phase-0 audit deliverables, and [solutions/LESSONS.md](../solutions/LESSONS.md) (audit trail), all of which are intentional |
| Runtime files untouched | `git diff --stat` on `pi_agent.py`/`tools/`/`evolution.py`/`app/`/`core/`/`llm/`/`prompts/system.txt` shows only the pre-existing modification on `pi_agent.py` from before this session |
| Repo root file count | 13 files (down from 25): 11 markdown/text + `pi_dna.txt` + `test_progress.txt` |

## Modified

| Path | Change |
|---|---|
| [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) | new — merged canonical |
| [docs/USER_GUIDE.md](../docs/USER_GUIDE.md) | new — rewritten to match runtime |
| [docs/_archive/2026-04-25/README.md](../docs/_archive/2026-04-25/README.md) | new — archive rationale |
| [.env.example](../.env.example) | new — env var template |
| [README.md](../README.md) | rewrite — honest claims, new repo map |
| [ABOUT.md](../ABOUT.md) | rewrite — capability table downgrades |
| 12 archived files | moved to [docs/_archive/2026-04-25/](../docs/_archive/2026-04-25/) (see batch table in `phase-0-complete.md`) |

No runtime code modified. No tests modified. No prompts modified. No tickets / solutions / lessons modified.

## Blocked / Open

- **`pi_dna.txt` (167 KB)** still at repo root, unread. Master prompt §6.3 (Phase 6) salvages `MODULE_TEMPLATE.py` from §18, then archives. Not on the Phase 1 archive list, so it stays for now.
- **`test_progress.txt`** still at repo root. Phase-0 [DEAD_CODE.md DC-009](../DEAD_CODE.md) flagged it for archive/removal but it's not on the master prompt's Phase 1 list. Per §2.4 ("one phase at a time"), leaving as-is.
- **`tickets/open/`** still empty; T-017, T-018, T-019 still in [analysis/tickets.jsonl](../analysis/tickets.jsonl). Promotion to canonical `tickets/open/` is not part of Phase 1's scope per master prompt — it falls under Phase 3 (T-017) and Phase 5 (T-019). Leave for those phases.
- **No regression to runtime caused by Phase 1.** All changes are documentation-only or moves of documentation files. The agent will run identically before and after this commit.

## Acceptance gate (master prompt §6 Phase 1)

> Ash reads rewritten `README.md` + `ABOUT.md` + `docs/ARCHITECTURE.md` and confirms the claim-to-reality mapping.

Awaiting that confirmation before Phase 2 begins.

## Next session's first step

Send Ash the Phase 1 acceptance ask:
> Phase 1 complete. The 12 archived files and the rewritten/merged docs are in place. Read `README.md`, `ABOUT.md`, `docs/ARCHITECTURE.md`. Say "phase 2" to begin the `evolution.py` schema-drift fix (SM-001), or push back on any specific claim.

## Notes to self

- The grep verification (#4) found references to archived doc names in [solutions/LESSONS.md](../solutions/LESSONS.md). Those are inside L-XXX entries and are part of the audit trail — they correctly describe the tickets and solutions written *at that time*. Do not edit them. Future me: if you see "FAILURE_TICKETS.txt" in `LESSONS.md`, that's the historical reference, not a stale link to fix.
- The 4 stale fix docs (`ARCHITECTURE_FIX.md` etc.) being untracked when archived means git won't show their full content as a delete-then-add — they'll show as new files in the archive. That's fine, but worth noting if anyone ever bisects through this repo.
- The merged `docs/ARCHITECTURE.md` is the place I leaned hardest into "honest about the gaps." §11 explicitly names the testing gap rather than burying it. If a future doc rewrite sands that down, it's a regression. Keep it pointed.
- README's repo map intentionally lists [STATUS.md](../STATUS.md) and the four other Phase-0 deliverables as a separate section ("Phase-0 audit deliverables"). They are not "permanent canonical" — once Phase 6 lands the live `STATUS.md` auto-update, the audit-time deliverables can move to `docs/_archive/` themselves. That's not a Phase 1 concern; flagging for the future.
