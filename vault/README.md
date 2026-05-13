# Pi Vault

One-way mirror of Pi's memory and engineering state. **Do not edit files here** — changes are overwritten on the next session exit. To change memory, use Pi's `memory_write` / `memory_delete` tools.

## Layout

```
vault/
├── _hot.md          ← HOT tier: pre-compiled session context (~60 lines) — load this first
├── memory/
│   ├── L3/          ← WARM tier: active context (from SQLite l3_cache), one file per category
│   └── L2/          ← COLD tier: organised knowledge (from Supabase), one file per category
└── notes/
    ├── status.md    ← mirror of docs/STATUS.md
    ├── tickets/
    │   ├── open.md
    │   └── closed.md
    └── per-ticket/  ← WARM tier: one distilled brief per closed ticket (gitignored)
```

## HOT / WARM / COLD tiers (token-reduction strategy)

| Tier | Files | Rule |
|------|-------|------|
| **HOT**  | `vault/_hot.md` | Load once at session start — replaces reading PI.md + STATUS + CHECKPOINTS |
| **WARM** | `vault/memory/L3/*.md` · `vault/notes/per-ticket/T-NNN.md` | Query via `obsidian_search` or read individually on demand |
| **COLD** | `vault/memory/L2/*.md` · `docs/_archive/**` | Never auto-load; only if explicitly needed |

Typical savings: 40–50% fewer tokens per session vs. loading all context files at start.

## For VS Code Claude

Load `vault/_hot.md` first. Pull individual `vault/notes/per-ticket/T-NNN-*.md` files when working a specific ticket. Do not read entire directories at once. Query WARM files via `obsidian_search` rather than reading whole directories.

## Sync

Updated automatically at the end of every Pi session (`agent/session.py::on_exit`). Last sync timestamp is written to each file's header.
