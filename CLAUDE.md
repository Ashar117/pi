# CLAUDE.md

**Read [`PI.md`](PI.md).** That is the single bootstrap file for any AI session on this project.

Everything that used to live here — session protocol, file-touch policy, key facts, vault layout — is now in `PI.md`, sectioned and self-contained. Old phase-0 docs are under [`docs/_archive/`](docs/_archive/).

If `PI.md` is missing or unreadable, that's a P0 — file a ticket and tell Ash.

---

## Passive Skills (slash commands)

Six slash commands are available for health checks. They are **read-only** — never auto-fix, never commit.

| Command | Script | When to use |
| --- | --- | --- |
| `/pi-passive` | all 5 below | Full health check — run any time |
| `/privacy` | `privacy_publish_guard.py` | Before any `git commit` or push |
| `/session-check` | `session_exit_protocol_checker.py` | At session end |
| `/sprint-ready` | `sprint_readiness_checker.py` | Before running `sprint.py` |
| `/doc-drift` | `doc_drift_watcher.py` | When docs feel stale |
| `/consciousness-sync` | `consciousness_capability_sync.py` | After adding new tools |

Exit codes: `0`=PASS `1`=WARN `2`=FAIL. Reports written to `reports/`.
