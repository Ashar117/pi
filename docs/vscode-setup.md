# VS Code Setup for Pi (T-046)

`.vscode/` is gitignored to keep personal IDE prefs private. This file documents the recommended setup so a fresh checkout knows what to install.

## One-time install

1. Install the **Foam** extension: `foam.foam-vscode` — gives Roam-style backlinks, daily notes, tag pane, and a graph view of all your markdown.
2. Install the **Python** extension: `ms-python.python` (+ `ms-python.vscode-pylance`).
3. Install **Markdown All in One**: `yzhang.markdown-all-in-one`.

VS Code will prompt you to install these the first time you open the workspace if `.vscode/extensions.json` is present (auto-created — see template below).

## What Foam gives you

- **Graph view** — `Foam: Show Graph` from the command palette. Visualises every markdown file as a node and every `[[wikilink]]` as an edge. Click a node to jump.
- **Backlinks panel** — sidebar pane that shows every file linking *to* the file you're viewing.
- **Daily notes** — `Foam: Open Daily Note` opens (or creates) `vault/Daily Notes/YYYY-MM-DD.md`. Same path the scheduler writes to.
- **Tag autocomplete** — type `#` and Foam suggests tags from across the vault.

## Templates

Drop these into `.vscode/` after a fresh clone (they're already present in Ash's working tree, just not committed).

### `.vscode/extensions.json`

```json
{
  "recommendations": [
    "foam.foam-vscode",
    "ms-python.python",
    "ms-python.vscode-pylance",
    "yzhang.markdown-all-in-one",
    "redhat.vscode-yaml"
  ]
}
```

### `.vscode/settings.json` (key bits)

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/pi_env/Scripts/python.exe",
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": ["testing"],
  "foam.workspace.includeGlobs": [
    "PI.md",
    "vault/**/*.md",
    "CHECKPOINTS/**/*.md",
    "docs/*.md",
    "tickets/closed/*.json",
    "solutions/SOLUTIONS.jsonl"
  ],
  "foam.workspace.ignoreGlobs": [
    "docs/_archive/**",
    "pi_env/**",
    "data/**",
    "logs/**",
    "vault/.god/**"
  ],
  "foam.dailyNote.directory": "vault/Daily Notes"
}
```

The full settings.json template (with file/search/watcher excludes) is checked in at `.vscode/settings.json` on Ash's machine — copy from there.

## Verifying it works

After installing Foam:

1. Open `vault/notes/status.md` — backlinks panel on the right should populate.
2. Run `Foam: Show Graph` from the palette — you should see PI.md at the centre with edges to every wikilinked note.
3. Run `Foam: Open Daily Note` — should open today's daily note in `vault/Daily Notes/`.

If the graph is empty, check that `foam.workspace.includeGlobs` covers your vault path and that you've reloaded the window.
