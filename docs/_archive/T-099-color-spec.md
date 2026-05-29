# T-099 Color Spec — Foam Graph Visual Layer

**Audience:** Sonnet (executing the edits) — this is a complete spec, follow it exactly.
**Scope:** Add `foam.graph.views` styling + named sub-views to the three workspace files. Update `.vscode/settings.json` with the two workspace-level polish flags. Document the palette + named views in `docs/vscode-setup.md`. No code changes outside JSON / Markdown.
**Verified against:** Foam VS Code extension v0.40.3 schema (`foam.graph.views`, `foam.files.include/exclude`).

---

## 1. Files to edit

| File | What changes |
| --- | --- |
| `pi-backbone.code-workspace` | Add `foam.graph.views`, `foam.graph.navigateToPreview`, `foam.graph.titleMaxLength` under `settings` |
| `pi-personal.code-workspace` | Same — different views array |
| `pi-full-PRIVATE.code-workspace` | Same — union views array |
| `.vscode/settings.json` | Add `foam.graph.navigateToPreview: true` + `foam.graph.titleMaxLength: 32` at folder level (these are global UX flags, not graph-scope) |
| `.vscode/keybindings.json` (new if missing) | Bind sub-view toggles to `Ctrl+Alt+1..9` |
| `docs/vscode-setup.md` | Add `## Color palette` + `## Named graph views` sections |

**Do NOT touch:** any `.py`, any `vault/notes/**`, any `tickets/**`, any `prompts/**`, any `data/**`, any `logs/**`, any other file.

---

## 2. Canonical palette

Tailwind 400 desaturated — dark-bg friendly, 8 hues, no two close enough to confuse at small sizes.

| Token | Hex | Semantic role |
| --- | --- | --- |
| `slate` | `#94a3b8` | neutral / scaffolding / templates |
| `sky` | `#38bdf8` | architecture / docs / foundational |
| `amber` | `#fbbf24` | tickets / attention / action items |
| `emerald` | `#34d399` | active work / sprints / in-motion |
| `violet` | `#a78bfa` | preferences / style / meta |
| `rose` | `#fb7185` | personal / entities / relations |
| `teal` | `#2dd4bf` | derived / secondary / per-ticket briefs |
| `yellow` | `#fde047` | anchors — PI.md, north_star (brightest) |

**Edges (lineColor):** `#3f3f46` (zinc-700) — low contrast so colored nodes pop.

**Backgrounds (per workspace, subtle tint):**

- Backbone: `#191c20` (cool dark)
- Personal: `#1a1a22` (warm dark)
- Full: `#1c1a1e` (purple-leaning dark)

**Font:** `fontSize: 12`, `fontFamily: "Inter"` (falls back to system if missing).

---

## 3. Workspace-level global flags

Add these two keys to **each** of the three `*.code-workspace` files inside `settings` (alongside existing `foam.files.include`/`exclude`, before `foam.graph.views`):

```jsonc
"foam.graph.navigateToPreview": true,
"foam.graph.titleMaxLength": 32,
```

Also add the same two to `.vscode/settings.json` (so they apply even when opening the bare folder). Place them near the existing `foam.dailyNote.directory` line.

---

## 4. Backbone workspace — `foam.graph.views`

Insert this array under `settings` in `pi-backbone.code-workspace`:

```jsonc
"foam.graph.views": [
  {
    "name": "Default",
    "colorBy": "none",
    "background": "#191c20",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "fontFamily": "Inter",
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "/PI\\.md$" } },
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "/README\\.md$" } },
      { "enabled": true, "color": "#38bdf8", "match": { "property": "path", "value": "docs/adr/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "docs/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "tickets/open/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "tickets/closed/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "vault/notes/tickets/" } },
      { "enabled": true, "color": "#2dd4bf", "match": { "property": "path", "value": "vault/notes/per-ticket/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/sprints/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/retros/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/status\\.md$" } },
      { "enabled": true, "color": "#a78bfa", "match": { "property": "path", "value": "CHECKPOINTS/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "vault/notes/templates/" } }
    ]
  },
  {
    "name": "TicketsFocus",
    "colorBy": "none",
    "background": "#191c20",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": false, "color": "#3f3f46", "match": { "property": "path", "value": ".*" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "tickets/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "vault/notes/tickets/" } },
      { "enabled": true, "color": "#2dd4bf", "match": { "property": "path", "value": "vault/notes/per-ticket/" } },
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "/PI\\.md$" } }
    ]
  },
  {
    "name": "ADRs",
    "colorBy": "none",
    "background": "#191c20",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": false, "color": "#3f3f46", "match": { "property": "path", "value": ".*" } },
      { "enabled": true, "color": "#38bdf8", "match": { "property": "path", "value": "docs/adr/" } },
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "/PI\\.md$" } }
    ]
  },
  {
    "name": "EngineeringLoop",
    "colorBy": "none",
    "background": "#191c20",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": false, "color": "#3f3f46", "match": { "property": "path", "value": ".*" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "tickets/" } },
      { "enabled": true, "color": "#2dd4bf", "match": { "property": "path", "value": "vault/notes/per-ticket/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/sprints/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/retros/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/status\\.md$" } },
      { "enabled": true, "color": "#a78bfa", "match": { "property": "path", "value": "CHECKPOINTS/" } }
    ]
  }
]
```

**Group-rule semantics:**

- `match.value` is a substring of the node id (= the file path). Foam wraps it in a regex matcher, so `/PI\\.md$` is an anchored regex but `tickets/` is a plain substring.
- Disabled-catch-all-`.*` + enabled-specifics = hide everything except the specifics. This is how sub-views work.
- Later rules override color of earlier matching rules. Order matters — keep specific rules **after** generic ones.

---

## 5. Personal workspace — `foam.graph.views`

Insert under `settings` in `pi-personal.code-workspace`:

```jsonc
"foam.graph.views": [
  {
    "name": "Default",
    "colorBy": "none",
    "background": "#1a1a22",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "fontFamily": "Inter",
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "vault/notes/north_star\\.md$" } },
      { "enabled": true, "color": "#fb7185", "match": { "property": "path", "value": "vault/notes/memory/entities/" } },
      { "enabled": true, "color": "#38bdf8", "match": { "property": "path", "value": "vault/notes/memory/permanent-profile/" } },
      { "enabled": true, "color": "#a78bfa", "match": { "property": "path", "value": "vault/notes/memory/preferences/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/memory/active-project/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "vault/notes/memory/current-priority/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "vault/notes/memory/session-history/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "vault/notes/memory/session-summary/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "vault/notes/memory/note/" } },
      { "enabled": true, "color": "#2dd4bf", "match": { "property": "path", "value": "vault/notes/sessions/" } }
    ]
  },
  {
    "name": "EntitiesOnly",
    "colorBy": "none",
    "background": "#1a1a22",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": false, "color": "#3f3f46", "match": { "property": "path", "value": ".*" } },
      { "enabled": true, "color": "#fb7185", "match": { "property": "path", "value": "vault/notes/memory/entities/" } },
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "vault/notes/north_star\\.md$" } }
    ]
  },
  {
    "name": "ProfileAndPreferences",
    "colorBy": "none",
    "background": "#1a1a22",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": false, "color": "#3f3f46", "match": { "property": "path", "value": ".*" } },
      { "enabled": true, "color": "#38bdf8", "match": { "property": "path", "value": "vault/notes/memory/permanent-profile/" } },
      { "enabled": true, "color": "#a78bfa", "match": { "property": "path", "value": "vault/notes/memory/preferences/" } },
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "vault/notes/north_star\\.md$" } }
    ]
  },
  {
    "name": "ActiveAndPriority",
    "colorBy": "none",
    "background": "#1a1a22",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": false, "color": "#3f3f46", "match": { "property": "path", "value": ".*" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/memory/active-project/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "vault/notes/memory/current-priority/" } }
    ]
  }
]
```

---

## 6. Full workspace — `foam.graph.views`

Union of both palettes. Default view colors everything; sub-views slice into the backbone half, the personal half, or the cross-linked subset.

Insert under `settings` in `pi-full-PRIVATE.code-workspace`:

```jsonc
"foam.graph.views": [
  {
    "name": "Default",
    "colorBy": "none",
    "background": "#1c1a1e",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "fontFamily": "Inter",
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "/PI\\.md$" } },
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "vault/notes/north_star\\.md$" } },
      { "enabled": true, "color": "#38bdf8", "match": { "property": "path", "value": "docs/adr/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "docs/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "tickets/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "vault/notes/tickets/" } },
      { "enabled": true, "color": "#2dd4bf", "match": { "property": "path", "value": "vault/notes/per-ticket/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/sprints/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/retros/" } },
      { "enabled": true, "color": "#a78bfa", "match": { "property": "path", "value": "CHECKPOINTS/" } },
      { "enabled": true, "color": "#fb7185", "match": { "property": "path", "value": "vault/notes/memory/entities/" } },
      { "enabled": true, "color": "#38bdf8", "match": { "property": "path", "value": "vault/notes/memory/permanent-profile/" } },
      { "enabled": true, "color": "#a78bfa", "match": { "property": "path", "value": "vault/notes/memory/preferences/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/memory/active-project/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "vault/notes/memory/current-priority/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "vault/notes/memory/session-history/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "vault/notes/memory/session-summary/" } },
      { "enabled": true, "color": "#94a3b8", "match": { "property": "path", "value": "vault/notes/memory/note/" } },
      { "enabled": true, "color": "#2dd4bf", "match": { "property": "path", "value": "vault/notes/sessions/" } }
    ]
  },
  {
    "name": "BackboneOnly",
    "colorBy": "none",
    "background": "#191c20",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": false, "color": "#3f3f46", "match": { "property": "path", "value": ".*" } },
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "/PI\\.md$" } },
      { "enabled": true, "color": "#38bdf8", "match": { "property": "path", "value": "docs/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "tickets/" } },
      { "enabled": true, "color": "#2dd4bf", "match": { "property": "path", "value": "vault/notes/per-ticket/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/sprints/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/retros/" } },
      { "enabled": true, "color": "#a78bfa", "match": { "property": "path", "value": "CHECKPOINTS/" } }
    ]
  },
  {
    "name": "PersonalOnly",
    "colorBy": "none",
    "background": "#1a1a22",
    "lineColor": "#3f3f46",
    "fontSize": 12,
    "show": {
      "placeholder": { "enabled": false },
      "tag": { "enabled": false },
      "attachment": { "enabled": false },
      "image": { "enabled": false }
    },
    "groups": [
      { "enabled": false, "color": "#3f3f46", "match": { "property": "path", "value": ".*" } },
      { "enabled": true, "color": "#fde047", "match": { "property": "path", "value": "vault/notes/north_star\\.md$" } },
      { "enabled": true, "color": "#fb7185", "match": { "property": "path", "value": "vault/notes/memory/entities/" } },
      { "enabled": true, "color": "#38bdf8", "match": { "property": "path", "value": "vault/notes/memory/permanent-profile/" } },
      { "enabled": true, "color": "#a78bfa", "match": { "property": "path", "value": "vault/notes/memory/preferences/" } },
      { "enabled": true, "color": "#34d399", "match": { "property": "path", "value": "vault/notes/memory/active-project/" } },
      { "enabled": true, "color": "#fbbf24", "match": { "property": "path", "value": "vault/notes/memory/current-priority/" } },
      { "enabled": true, "color": "#2dd4bf", "match": { "property": "path", "value": "vault/notes/sessions/" } }
    ]
  }
]
```

---

## 7. Keybindings — `.vscode/keybindings.json`

If the file doesn't exist, create it. If it does, MERGE these entries (don't overwrite). Foam exposes the graph-open command with a `view` arg.

```json
[
  {
    "key": "ctrl+alt+0",
    "command": "foam-vscode.show-graph",
    "args": { "view": "Default" }
  },
  {
    "key": "ctrl+alt+1",
    "command": "foam-vscode.show-graph",
    "args": { "view": "TicketsFocus" }
  },
  {
    "key": "ctrl+alt+2",
    "command": "foam-vscode.show-graph",
    "args": { "view": "ADRs" }
  },
  {
    "key": "ctrl+alt+3",
    "command": "foam-vscode.show-graph",
    "args": { "view": "EngineeringLoop" }
  },
  {
    "key": "ctrl+alt+4",
    "command": "foam-vscode.show-graph",
    "args": { "view": "EntitiesOnly" }
  },
  {
    "key": "ctrl+alt+5",
    "command": "foam-vscode.show-graph",
    "args": { "view": "ProfileAndPreferences" }
  },
  {
    "key": "ctrl+alt+6",
    "command": "foam-vscode.show-graph",
    "args": { "view": "ActiveAndPriority" }
  },
  {
    "key": "ctrl+alt+7",
    "command": "foam-vscode.show-graph",
    "args": { "view": "BackboneOnly" }
  },
  {
    "key": "ctrl+alt+8",
    "command": "foam-vscode.show-graph",
    "args": { "view": "PersonalOnly" }
  }
]
```

Command name `foam-vscode.show-graph` is verified against Foam 0.40.3 `package.json` (line 362). Bindings only work when a workspace has the named view defined. `Ctrl+Alt+1` (TicketsFocus) in `pi-personal` is a no-op — that's intentional, not a bug.

---

## 8. Documentation — extend `docs/vscode-setup.md`

Add these two sections **after** the existing `## Three Foam graphs (T-099)` section (before `## One-time install`):

```markdown
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
```

---

## 9. Verification checklist (Sonnet runs all)

After all edits, **before claiming done**:

1. ✅ JSON validates — open each `.code-workspace` file, no red squiggles. Same for `.vscode/settings.json` and `.vscode/keybindings.json`.
2. ✅ `code pi-backbone.code-workspace` from PowerShell, then `Ctrl+Shift+P` → `Developer: Reload Window`
3. ✅ `Foam: Show Graph` — confirm:
   - Background is `#191c20`
   - Nodes have palette colors (yellow PI.md, amber tickets, etc.)
   - No placeholder or tag nodes visible
   - Title bar still reads `Pi — Backbone (shareable) - pi-backbone (Workspace)`
4. ✅ `Ctrl+Alt+1` opens TicketsFocus view — only ticket-related nodes visible
5. ✅ `Ctrl+Alt+2` opens ADRs view — only `docs/adr/*` nodes visible
6. ✅ `Ctrl+Alt+3` opens EngineeringLoop view
7. ✅ Repeat steps 2–3 for `pi-personal.code-workspace` — confirm warm-dark background, rose entity nodes
8. ✅ `Ctrl+Alt+4` opens EntitiesOnly view in personal workspace
9. ✅ Repeat for `pi-full-PRIVATE.code-workspace` — confirm union view, then `Ctrl+Alt+7` / `Ctrl+Alt+8` toggle between backbone/personal slices
10. ✅ `python scripts/verify.py` — must PASS (no Python code touched, but verify the JSON/Markdown didn't break anything)
11. ✅ `git status` — only these files modified/added:
    - `pi-backbone.code-workspace` (modified, tracked)
    - `pi-personal.code-workspace` (modified, untracked-by-local-exclude)
    - `pi-full-PRIVATE.code-workspace` (modified, untracked-by-local-exclude)
    - `.vscode/settings.json` (modified, gitignored)
    - `.vscode/keybindings.json` (new or modified, gitignored)
    - `docs/vscode-setup.md` (modified, tracked)
    - `docs/T-099-color-spec.md` (this file — optional: leave or delete)

**If any step fails:** STOP, report which check failed and what was observed. Do not commit. Do not "fix forward" by guessing — diagnose first.

---

## 10. Out of scope (do NOT do)

- Do not write a script that auto-tags markdown files with frontmatter — coloring is path-based, no tags needed
- Do not modify `.gitignore` — workspace privacy is already handled via `.git/info/exclude`
- Do not touch `pi-backbone.code-workspace`'s `foam.files.include` / `foam.files.exclude` — already correct from prior commit
- Do not close T-099 ticket — Ash will close after testing approval
- Do not commit — Ash will commit after testing
- Do not push — Ash explicitly said hold for testing
- Do not run `verify.py` repeatedly in a loop, run once for the final check

---

## 11. Ship message (for Ash to copy when ready)

```text
T-099 visual layer: palette + named sub-views

- 8-hue Tailwind 400 palette, path-based group rules per workspace
- 4 named views in backbone (Default + TicketsFocus + ADRs + EngineeringLoop)
- 4 in personal (Default + EntitiesOnly + ProfileAndPreferences + ActiveAndPriority)
- 3 in full (Default + BackboneOnly + PersonalOnly)
- Sub-views bound to Ctrl+Alt+0..8
- Placeholders + tag nodes hidden globally
- Per-workspace background tint + navigateToPreview + titleMaxLength=32
- docs/vscode-setup.md extended with palette + view tables
```
