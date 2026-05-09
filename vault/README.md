# Pi Vault

One-way mirror of Pi's memory and engineering state. **Do not edit files here** — changes are overwritten on the next session exit. To change memory, use Pi's `memory_write` / `memory_delete` tools.

## Layout

```
vault/
├── memory/
│   ├── L3/          ← active context (from SQLite l3_cache), one file per category
│   └── L2/          ← organised knowledge (from Supabase), one file per category
└── notes/
    ├── status.md    ← mirror of docs/STATUS.md
    ├── tickets/
    │   ├── open.md
    │   └── closed.md
    └── per-ticket/  ← one distilled brief per closed ticket (gitignored)
```

## For VS Code Claude

Read `notes/status.md` at session start instead of `docs/STATUS.md`. Pull individual `notes/per-ticket/T-NNN-*.md` files when working a specific ticket. Do not read entire directories at once.

## Sync

Updated automatically at the end of every Pi session (`agent/session.py::on_exit`). Last sync timestamp is written to each file's header.
