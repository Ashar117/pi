# VS Code Setup for Pi (T-046)

`.vscode/` is gitignored to keep personal IDE prefs private. This file documents the recommended setup so a fresh checkout knows what to install.

## Three Foam graphs (T-099)

The vault contains two kinds of content: **architectural** (PI.md, ADRs, tickets, sprints, retros, per-ticket briefs — stable, shareable) and **personal/relations** (entity hubs, memory facts, normie session archives, north-star — grows fast, private). Mixing them in one Foam graph buries the engineering spine in personal nodes and makes screen-sharing risky.

Three `.code-workspace` files at repo root carve the graph into three lenses:

| Workspace | Audience | Indexes | Open via |
| --- | --- | --- | --- |
| `pi-backbone.code-workspace` | Daily driver, shareable | PI.md, docs/, CHECKPOINTS/, vault/notes/{status,tickets,per-ticket,sprints,retros,templates}/** | `File → Open Workspace from File` |
| `pi-personal.code-workspace` | Entity / memory recall, never shared | vault/notes/{memory,sessions}/**, north_star.md | Local only — in `.git/info/exclude` |
| `pi-full-PRIVATE.code-workspace` | Opt-in union for cross-graph queries | Everything except `_archive`/`pi_env`/`data`/`logs`/`.god` | Local only — never screen-share |

Each workspace sets `foam.workspace.includeGlobs` + `ignoreGlobs` so `Foam: Show Graph` renders only that slice. The `window.title` is set per-workspace so the title bar tells you which lens is active.

**Privacy posture:**

- `pi-backbone.code-workspace` is committed (its globs describe shareable structure)
- `pi-personal.code-workspace` and `pi-full-PRIVATE.code-workspace` are in `.git/info/exclude` (their existence + globs would hint at personal subtree layout)
- The graph split mirrors the existing `.git/info/exclude` boundary — personal vault subtrees are already local-only, this just makes the graph view match

**Default open:** don't habitually open `pi-full-PRIVATE` — backbone is the safe default. The full view is for "did any ticket touch this person/entity?" moments only.

### Color palette

Each node is colored by its path. Palette is Tailwind 400 desaturated for dark-bg legibility, 8 hues, all readable at small node size.

| Hue | Hex | Used for |
| --- | --- | --- |
| yellow | `#fde047` | Anchors — `PI.md`, `vault/notes/north_star.md` |
| sky | `#38bdf8` | ADRs (`docs/adr/`), permanent-profile memory facts |
| amber | `#fbbf24` | Tickets, current-priority memory |
| emerald | `#34d399` | Sprints, retros, status, active-project memory |
| violet | `#a78bfa` | CHECKPOINTS, preferences memory |
| rose | `#fb7185` | Entity hubs (`vault/notes/memory/entities/`) |
| teal | `#2dd4bf` | Per-ticket briefs, session archives |
| slate | `#94a3b8` | Docs (non-ADR), templates, session-history/summary |

Edges render in `#3f3f46` (zinc-700) so colored nodes pop. Background tint per workspace: backbone `#191c20`, personal `#1a1a22`, full `#1c1a1e` — quick visual cue which graph is active.

### Named graph views

Each workspace ships multiple views accessible from the command palette (`Foam: Show Graph` opens the Default) or via keybindings.

**Backbone workspace:**

| View | Keybinding | Shows |
| --- | --- | --- |
| `Default` | `Ctrl+Alt+0` | Everything backbone, colored per palette |
| `TicketsFocus` | `Ctrl+Alt+1` | Only tickets + per-ticket briefs + PI.md |
| `ADRs` | `Ctrl+Alt+2` | Only `docs/adr/` + PI.md |
| `EngineeringLoop` | `Ctrl+Alt+3` | Tickets, per-ticket, sprints, retros, status, CHECKPOINTS |

**Personal workspace:**

| View | Keybinding | Shows |
| --- | --- | --- |
| `Default` | `Ctrl+Alt+0` | All memory + sessions + north_star, colored per palette |
| `EntitiesOnly` | `Ctrl+Alt+4` | Only entity hubs + north_star |
| `ProfileAndPreferences` | `Ctrl+Alt+5` | Permanent-profile + preferences + north_star |
| `ActiveAndPriority` | `Ctrl+Alt+6` | Active-project + current-priority memory |

**Full workspace:**

| View | Keybinding | Shows |
| --- | --- | --- |
| `Default` | `Ctrl+Alt+0` | Everything from both halves |
| `BackboneOnly` | `Ctrl+Alt+7` | Backbone slice within full graph |
| `PersonalOnly` | `Ctrl+Alt+8` | Personal slice within full graph |

Keybindings are scoped — `Ctrl+Alt+1` (TicketsFocus) is a no-op in `pi-personal` because that view only exists in `pi-backbone`. By design.

#### Adding a new view

1. Append a new object to the workspace's `foam.graph.views` array
2. Set `name`, `background`, `lineColor`, `fontSize`, `show`
3. Build `groups` — start with a catch-all `{ "enabled": false, "match": { "property": "path", "value": ".*" } }` then add `enabled: true` rules for the slice you want to keep
4. (Optional) Bind a key in `.vscode/keybindings.json`
5. Reload window

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
  "foam.dailyNote.directory": "vault/Daily Notes"
}
```

**Do NOT put `foam.files.include` / `foam.files.exclude` here** — folder-level Foam globs override workspace-level globs (VS Code settings precedence: WorkspaceFolder beats Workspace), which silently breaks the three-graph split. Foam globs live in the `.code-workspace` files only.

The full settings.json template (with file/search/watcher excludes) is checked in at `.vscode/settings.json` on Ash's machine — copy from there.

> **Note on setting keys:** Foam ≥0.40 uses `foam.files.include` and `foam.files.exclude`. The older `foam.workspace.includeGlobs` / `foam.workspace.ignoreGlobs` keys silently do nothing in current Foam — if a workspace's graph filtering isn't applying, check the key names first.

## Verifying it works

After installing Foam:

1. Open `vault/notes/status.md` — backlinks panel on the right should populate.
2. Run `Foam: Show Graph` from the palette — you should see PI.md at the centre with edges to every wikilinked note.
3. Run `Foam: Open Daily Note` — should open today's daily note in `vault/Daily Notes/`.

If the graph is empty, check that `foam.workspace.includeGlobs` covers your vault path and that you've reloaded the window.
