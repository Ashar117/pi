"""
tools/tools_obsidian.py — Obsidian integration: live tool API + exit-time sync.

Two distinct responsibilities:

1. ObsidianTools class — wraps the Obsidian Local REST API (port 27123, enabled
   via the "Local REST API" community plugin). Provides obsidian_read/write/
   append/search so Claude can interact with the vault directly in root mode.
   Degrades gracefully when Obsidian is closed (returns error dict, never raises).

2. Sync functions — one-way mirror from Supabase + local files to vault/ at
   session exit. Called by agent/session.py::on_exit() via sync_vault().

Direction for sync:  Supabase + local files -> vault/   (never the other way)
Trigger:    agent/session.py on_exit(), after distillation and promotion
Atomic:     every file write uses .tmp -> os.replace() so a crash mid-sync
            cannot leave a partial file
Non-fatal:  each sync function individually try/excepted; one failure never
            blocks subsequent steps

Public API
----------
sync_vault(memory_tools, project_root=None) -> dict
    Master entry point. Returns summary dict with per-step counts/errors.
    This is the only function session.py needs to call.

Individual steps (also callable standalone for debugging):
    sync_l3_to_vault(memory_tools, vault_root)
    sync_l2_to_vault(memory_tools, vault_root)
    sync_l2_facts_to_vault(memory_tools, vault_root)  # T-081: per-fact files for Foam graph
    render_tickets_to_vault(project_root, vault_root)
    render_per_ticket_notes(project_root, vault_root)
    render_status_to_vault(project_root, vault_root)
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# ObsidianTools — live vault I/O via the Local REST API plugin
# ---------------------------------------------------------------------------

class ObsidianTools:
    """
    Thin wrapper around the Obsidian Local REST API (community plugin).
    Default host: http://127.0.0.1:27123

    All methods return a dict with at least {"success": bool}. They never
    raise — if Obsidian is closed or the plugin is not running, they return
    {"success": False, "error": "<reason>"} so Claude can relay the message.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self._host = host or os.environ.get("OBSIDIAN_HOST", "http://127.0.0.1:27123")
        self._api_key = api_key or os.environ.get("OBSIDIAN_API_KEY", "")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "text/markdown",
        }

    def _get(self, path: str) -> dict:
        try:
            import httpx
            with httpx.Client(
                base_url=self._host, headers=self._headers(),
                verify=False, timeout=10
            ) as c:
                r = c.get(path)
                r.raise_for_status()
                return {"success": True, "content": r.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _put(self, path: str, content: str) -> dict:
        try:
            import httpx
            with httpx.Client(
                base_url=self._host, headers=self._headers(),
                verify=False, timeout=10
            ) as c:
                r = c.put(path, content=content.encode())
                r.raise_for_status()
                return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _post(self, path: str, content: str = "", params: dict = None) -> dict:
        try:
            import httpx
            with httpx.Client(
                base_url=self._host, headers=self._headers(),
                verify=False, timeout=10
            ) as c:
                r = c.post(path, content=content.encode() if content else b"",
                           params=params or {})
                r.raise_for_status()
                try:
                    return {"success": True, "data": r.json()}
                except Exception:
                    return {"success": True, "content": r.text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def obsidian_read(self, path: str) -> dict:
        """Return the markdown content of a vault note.
        path is relative to vault root (e.g. 'notes/status.md')."""
        return self._get(f"/vault/{path.lstrip('/')}")

    def obsidian_write(self, path: str, content: str) -> dict:
        """Create or overwrite a vault note. path is relative to vault root."""
        result = self._put(f"/vault/{path.lstrip('/')}", content)
        if result["success"]:
            result["path"] = path
        return result

    def obsidian_append(self, path: str, content: str) -> dict:
        """Append text to a vault note (creates it if absent)."""
        result = self._post(f"/vault/{path.lstrip('/')}", content)
        if result["success"]:
            result["path"] = path
        return result

    def obsidian_search(self, query: str, max_results: int = 10) -> dict:
        """Full-text search across the vault. Returns matching paths + excerpts."""
        result = self._post(
            "/search/simple/",
            params={"query": query, "contextLength": 200},
        )
        if not result["success"]:
            return result
        hits = result.get("data", [])
        if not hits:
            return {"success": True, "results": [], "summary": "No results."}
        lines = []
        for h in hits[:max_results]:
            lines.append(f"### {h.get('filename', '?')}")
            for ctx in h.get("matches", [])[:2]:
                lines.append(ctx.get("context", ""))
        return {"success": True, "results": hits[:max_results],
                "summary": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Internal helpers (sync functions)
# ---------------------------------------------------------------------------

def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _default_vault_root() -> str:
    return os.path.join(_project_root(), "vault")


def _atomic_write(path: str, content: str) -> None:
    """Write content to path atomically: write .tmp then os.replace()."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _importance_bar(importance: int) -> str:
    """Map 1-10 importance to a 5-char visual bar: filled=X empty=o."""
    stars = min(5, round((importance or 0) / 2))
    return "X" * stars + "o" * (5 - stars)


# ---------------------------------------------------------------------------
# Step 1: L3 sync (SQLite -> vault/memory/L3/)
# ---------------------------------------------------------------------------

def sync_l3_to_vault(memory_tools, vault_root: str) -> dict:
    """
    Read all rows from the local SQLite l3_cache and write one markdown file
    per category under vault/memory/L3/.

    Uses SQLite (offline, fast) rather than Supabase so this step never
    makes a network call and works even if Supabase is unreachable.

    Returns {"written": N, "categories": [...], "error": None | str}
    """
    try:
        conn = sqlite3.connect(memory_tools.sqlite_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, content, importance, category, active_until, created_at "
            "FROM l3_cache ORDER BY category, importance DESC"
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception as e:
        return {"written": 0, "categories": [], "error": f"SQLite read failed: {e}"}

    # Group by category
    by_cat: dict = {}
    for row in rows:
        cat = row["category"] or "uncategorised"
        by_cat.setdefault(cat, []).append(row)

    written = 0
    for cat, entries in sorted(by_cat.items()):
        lines = [
            f"# L3 -- {cat}",
            f"*{len(entries)} entries - synced {_now_utc()}*",
            "",
        ]
        for e in entries:
            imp = e.get("importance") or 5
            bar = _importance_bar(imp)
            until = (
                f" - expires {e['active_until'][:10]}"
                if e.get("active_until") else ""
            )
            lines.append(f"- [{bar}] {e['content']}{until}")
        lines.append("")

        slug = cat.replace(" ", "_").replace("/", "-")
        path = os.path.join(vault_root, "memory", "L3", f"{slug}.md")
        _atomic_write(path, "\n".join(lines))
        written += 1

    return {"written": written, "categories": sorted(by_cat.keys()), "error": None}


# ---------------------------------------------------------------------------
# Step 2: L2 sync (Supabase organized_memory -> vault/memory/L2/)
# ---------------------------------------------------------------------------

def sync_l2_to_vault(memory_tools, vault_root: str) -> dict:
    """
    Pull all active L2 entries from Supabase organized_memory and write one
    markdown file per category under vault/memory/L2/.

    Returns {"written": N, "categories": [...], "error": None | str}
    """
    try:
        resp = (
            memory_tools.supabase
            .table("organized_memory")
            .select("id,category,title,content,importance,status,created_at")
            .eq("status", "active")
            .order("importance", desc=True)
            .execute()
        )
        rows = resp.data or []
    except Exception as e:
        return {"written": 0, "categories": [], "error": f"Supabase read failed: {e}"}

    by_cat: dict = {}
    for row in rows:
        cat = row.get("category") or "uncategorised"
        by_cat.setdefault(cat, []).append(row)

    written = 0
    for cat, entries in sorted(by_cat.items()):
        lines = [
            f"# L2 -- {cat}",
            f"*{len(entries)} entries - synced {_now_utc()}*",
            "",
        ]
        for e in entries:
            imp = e.get("importance") or 5
            bar = _importance_bar(imp)
            # content is JSONB {"text": "..."} or a plain string
            body = e.get("content") or {}
            text = body.get("text", "") if isinstance(body, dict) else str(body)
            lines.append(f"- [{bar}] {text}")
        lines.append("")

        slug = cat.replace(" ", "_").replace("/", "-")
        path = os.path.join(vault_root, "memory", "L2", f"{slug}.md")
        _atomic_write(path, "\n".join(lines))
        written += 1

    return {"written": written, "categories": sorted(by_cat.keys()), "error": None}


# ---------------------------------------------------------------------------
# Step 2b (T-081): Per-fact L2 vault sync — Foam-graph-friendly view
# ---------------------------------------------------------------------------

_TITLE_CASE_ENTITY = re.compile(
    r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+){0,3})\b"
)
# Words that look capitalized but should NOT become [[entity]] links — they're
# common sentence-starters or generic nouns that pollute the Foam graph.
_ENTITY_STOPLIST = frozenset({
    "I", "Pi", "Ash", "The", "This", "That", "These", "Those", "It", "He",
    "She", "We", "They", "You", "My", "Your", "His", "Her", "Their", "Our",
    "Mr", "Mrs", "Dr", "Ms", "And", "Or", "But", "So", "If", "When", "Then",
})


def _slugify(text: str, max_len: int = 60) -> str:
    """Filesystem-safe lowercase slug from arbitrary text.

    Underscores collapse to hyphens so 'permanent_profile' -> 'permanent-profile'
    rather than 'permanentprofile'.
    """
    text = text.strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text[:max_len].strip("-") or "untitled"


def _linkify_entities(text: str, self_entity: Optional[str] = None) -> str:
    """Wrap Title Case entity mentions in [[wiki links]] so Foam graphs them.

    Skips the stoplist (I, Ash, Pi, etc.) and the fact's own self-entity to
    avoid trivial self-loops in the graph.
    """
    seen = set()

    def repl(m: re.Match) -> str:
        ent = m.group(1).strip()
        if ent in _ENTITY_STOPLIST:
            return ent
        if self_entity and ent.lower() == self_entity.lower():
            return ent
        if ent in seen:  # only link the first mention per fact
            return ent
        seen.add(ent)
        return f"[[{ent}]]"

    return _TITLE_CASE_ENTITY.sub(repl, text)


def sync_l2_facts_to_vault(memory_tools, vault_root: str) -> dict:
    """T-081: write one markdown file PER L2 fact, plus per-category index files.

    Layout written under vault_root:
        notes/memory/index.md                       — top-level overview
        notes/memory/<category>.md                  — per-category index ([[slug]] links)
        notes/memory/<category>/<slug>.md           — one file per fact

    Each fact file gets YAML frontmatter (id, category, importance, created_at),
    the fact text with auto-linked entities, and a back-link to its category
    index. Foam (VS Code extension) then renders these as a navigable graph
    with category and entity nodes as hubs.

    Returns {"facts": N, "categories": [...], "error": None | str}.
    """
    try:
        resp = (
            memory_tools.supabase
            .table("organized_memory")
            .select("id,category,title,content,importance,status,created_at")
            .eq("status", "active")
            .order("importance", desc=True)
            .limit(2000)
            .execute()
        )
        rows = resp.data or []
    except Exception as e:
        return {"facts": 0, "categories": [], "error": f"Supabase read failed: {e}"}

    facts_dir = os.path.join(vault_root, "notes", "memory")
    os.makedirs(facts_dir, exist_ok=True)

    by_cat: dict = {}
    written_facts = 0

    for row in rows:
        cat_raw = row.get("category") or "uncategorised"
        cat_slug = _slugify(cat_raw, 40)
        body = row.get("content") or {}
        text = body.get("text", "") if isinstance(body, dict) else str(body)
        text = (text or "").strip()
        if not text:
            continue

        # Derive a stable per-fact slug — first 60 chars + last 6 of id for uniqueness
        fact_id = row.get("id") or ""
        suffix = (fact_id.replace("-", "")[-6:] or "noid")
        slug = f"{_slugify(text, 50)}-{suffix}"
        importance = int(row.get("importance") or 5)
        created = row.get("created_at") or ""

        linked_body = _linkify_entities(text)

        lines = [
            "---",
            f"id: {fact_id}",
            f"category: {cat_raw}",
            f"importance: {importance}",
            f"created_at: {created}",
            f"importance_bar: {_importance_bar(importance)}",
            "---",
            "",
            f"# {text[:80]}",
            "",
            linked_body,
            "",
            f"_category:_ [[{cat_slug}]]",
            "",
        ]
        path = os.path.join(facts_dir, cat_slug, f"{slug}.md")
        _atomic_write(path, "\n".join(lines))
        written_facts += 1
        by_cat.setdefault(cat_slug, []).append((slug, text, importance))

    # Per-category index files: list facts as wiki links, ordered by importance
    for cat_slug, entries in sorted(by_cat.items()):
        entries.sort(key=lambda x: -x[2])
        idx_lines = [
            f"# Memory category: {cat_slug}",
            f"*{len(entries)} facts - synced {_now_utc()}*",
            "",
        ]
        for slug, text, imp in entries:
            bar = _importance_bar(imp)
            idx_lines.append(f"- [{bar}] [[{slug}]] - {text[:120]}")
        idx_lines.append("")
        idx_lines.append("_up:_ [[index]]")
        path = os.path.join(facts_dir, f"{cat_slug}.md")
        _atomic_write(path, "\n".join(idx_lines))

    # Top-level index — entry point to the Foam graph
    idx_lines = [
        "# Pi Memory (L2)",
        f"*{written_facts} facts across {len(by_cat)} categories - synced {_now_utc()}*",
        "",
        "Open this file in VS Code with Foam installed, then run **Foam: Show Graph**.",
        "Each fact is a node; categories and Title-Case entities are hub nodes.",
        "",
        "## Categories",
        "",
    ]
    for cat_slug in sorted(by_cat.keys()):
        idx_lines.append(f"- [[{cat_slug}]] ({len(by_cat[cat_slug])})")
    idx_lines.append("")
    _atomic_write(os.path.join(facts_dir, "index.md"), "\n".join(idx_lines))

    return {"facts": written_facts, "categories": sorted(by_cat.keys()), "error": None}


# ---------------------------------------------------------------------------
# T-096: Entity hub pages — one .md per entity that's mentioned in ≥2 facts.
# Lifted as a pattern from breferrari/obsidian-mind (org/people/X.md hubs),
# adapted to Pi's database-backed memory: the hub is a *projection* of L2
# rows, not the source of truth. Refreshed on every sync_vault() call.
# ---------------------------------------------------------------------------

_ENTITY_HUB_MIN_MENTIONS = 2


def sync_entity_hubs_to_vault(memory_tools, vault_root: str) -> dict:
    """Generate one hub .md per Title-Case entity mentioned in ≥2 L2 facts.

    Layout written under vault_root:
        notes/memory/entities/index.md              — top-level entity index
        notes/memory/entities/<entity-slug>.md      — one hub per entity

    Each hub lists the facts that mention this entity (as wiki links to the
    per-fact .md files written by sync_l2_facts_to_vault), plus co-occurring
    entities (any Title-Case mention that appears in ≥1 of the same facts).
    Returns {"entities": N, "skipped_below_min": M, "error": None | str}.

    Privacy: entity hubs contain references to people / orgs Ash interacts
    with. Recommended to keep notes/memory/entities/ in .git/info/exclude
    rather than committing to the public repo.
    """
    try:
        resp = (
            memory_tools.supabase
            .table("organized_memory")
            .select("id,category,content,importance,created_at")
            .eq("status", "active")
            .order("importance", desc=True)
            .limit(2000)
            .execute()
        )
        rows = resp.data or []
    except Exception as e:
        return {"entities": 0, "skipped_below_min": 0,
                "error": f"Supabase read failed: {e}"}

    # Map entity → list of {slug, text_preview, importance, fact_id, category}.
    # Same slug derivation as sync_l2_facts_to_vault so wiki-links resolve.
    entity_to_facts: dict = {}
    fact_to_entities: dict = {}

    for row in rows:
        body = row.get("content") or {}
        text = body.get("text", "") if isinstance(body, dict) else str(body)
        text = (text or "").strip()
        if not text:
            continue
        fact_id = row.get("id") or ""
        suffix = (fact_id.replace("-", "")[-6:] or "noid")
        slug = f"{_slugify(text, 50)}-{suffix}"
        importance = int(row.get("importance") or 5)
        category = _slugify(row.get("category") or "uncategorised", 40)

        # Reuse the same regex sync_l2_facts_to_vault uses, minus the
        # stoplist + first-mention-only logic — we want every entity each
        # fact contains so we can build the inverted index.
        ents = set()
        for m in _TITLE_CASE_ENTITY.finditer(text):
            ent = m.group(1).strip()
            if ent in _ENTITY_STOPLIST:
                continue
            ents.add(ent)

        if not ents:
            continue
        fact_to_entities[slug] = ents
        for ent in ents:
            entity_to_facts.setdefault(ent, []).append({
                "slug": slug,
                "preview": text[:120],
                "importance": importance,
                "fact_id": fact_id,
                "category": category,
            })

    # Filter to entities with ≥ _ENTITY_HUB_MIN_MENTIONS mentions; rank facts
    # within each by importance desc.
    written_entities = 0
    skipped = 0
    entities_dir = os.path.join(vault_root, "notes", "memory", "entities")
    os.makedirs(entities_dir, exist_ok=True)
    hub_index_entries: list = []

    for entity, facts in sorted(entity_to_facts.items()):
        if len(facts) < _ENTITY_HUB_MIN_MENTIONS:
            skipped += 1
            continue
        facts.sort(key=lambda f: -f["importance"])

        # Co-occurring entities = any entity that appears in any of the same
        # facts as this one, excluding self.
        co_occurring: dict = {}
        for f in facts:
            for other in fact_to_entities.get(f["slug"], set()):
                if other == entity:
                    continue
                co_occurring[other] = co_occurring.get(other, 0) + 1
        related = sorted(co_occurring.items(), key=lambda kv: -kv[1])[:10]

        entity_slug = _slugify(entity, 50)
        lines = [
            "---",
            f"entity: {entity}",
            f"mention_count: {len(facts)}",
            f"synced: {_now_utc()}",
            "tags:",
            "  - entity",
            "  - hub",
            "---",
            "",
            f"# {entity}",
            "",
            f"_Auto-generated hub — {len(facts)} L2 facts mention {entity}._",
            "",
            "> Source of truth is `organized_memory` (Supabase L2).",
            "> This page is a read-only projection refreshed at session-exit.",
            "",
            "## Facts",
            "",
        ]
        for f in facts:
            bar = _importance_bar(f["importance"])
            lines.append(f"- [{bar}] [[{f['slug']}]] — {f['preview']}")
        lines.append("")
        if related:
            lines.append("## See Also")
            lines.append("")
            for other_entity, count in related:
                lines.append(f"- [[{_slugify(other_entity, 50)}]] ({count} shared facts)")
            lines.append("")

        path = os.path.join(entities_dir, f"{entity_slug}.md")
        _atomic_write(path, "\n".join(lines))
        written_entities += 1
        hub_index_entries.append((entity, entity_slug, len(facts)))

    # Entity index — alphabetical, with mention counts
    idx_lines = [
        "# Pi Memory — Entity hubs",
        f"*{written_entities} entities (≥{_ENTITY_HUB_MIN_MENTIONS} L2 facts each) "
        f"— synced {_now_utc()}*",
        "",
        "Each hub backlinks to every L2 fact mentioning that entity.",
        "Open in VS Code with Foam to navigate the graph; each entity is a hub node.",
        "",
    ]
    hub_index_entries.sort(key=lambda e: -e[2])  # by mention count desc
    for entity, slug, count in hub_index_entries:
        idx_lines.append(f"- [[{slug}]] ({count})")
    idx_lines.append("")
    _atomic_write(os.path.join(entities_dir, "index.md"), "\n".join(idx_lines))

    return {
        "entities": written_entities,
        "skipped_below_min": skipped,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Step 2c (T-082): Audit digest renderer
# ---------------------------------------------------------------------------

def render_audit_digest(audit_run, vault_root: str) -> dict:
    """T-082: write the weekly audit digest as actionable markdown.

    Layout under vault_root:
        notes/memory/audit/YYYY-Www.md     — the digest for that ISO week
        notes/memory/audit/latest.md       — pointer to the newest digest

    Each line in the digest carries copy/pasteable shell commands so Ash can
    act on findings from his terminal. The file is markdown so Foam graphs it
    alongside the rest of the memory vault.
    """
    week = getattr(audit_run, "week_iso", "")
    run_at = getattr(audit_run, "run_at", "")
    flagged = getattr(audit_run, "flagged", []) or []
    archived = getattr(audit_run, "archived", []) or []
    deleted = getattr(audit_run, "deleted", []) or []
    merges = getattr(audit_run, "merge_suggestions", []) or []
    errors = getattr(audit_run, "errors", []) or []

    lines = [
        f"# Memory audit — {week}",
        f"*Generated {run_at}. {len(flagged)} flagged, {len(archived)} archived, "
        f"{len(deleted)} hard-deleted, {len(merges)} merge suggestions.*",
        "",
        "All actions are reversible until the 90-day hard-delete grace window expires.",
        "Copy any command line into your shell. `--confirm` is required for hard deletes.",
        "",
    ]

    def _block(title: str, items: list, action_template) -> None:
        if not items:
            return
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        for item in items:
            lines.append(f"- {item.get('summary', '?')}")
            cmd = action_template(item)
            if cmd:
                lines.append(f"  `{cmd}`")
        lines.append("")

    _block("Flagged for review", flagged,
           lambda it: (f"python scripts/pi_audit.py keep {it['target_ids'][0]}"
                       f"  |  python scripts/pi_audit.py delete {it['target_ids'][0]} --confirm"))
    _block("Archived this week", archived,
           lambda it: f"python scripts/pi_audit.py restore {it['target_ids'][0]}")
    _block("Hard-deleted this week (irreversible)", deleted,
           lambda it: None)
    _block("Merge suggestions", merges,
           lambda it: (f"python scripts/pi_audit.py merge {it['target_ids'][0]} {it['target_ids'][1]}"
                       if len(it.get("target_ids", [])) >= 2 else None))

    if errors:
        lines.append("## Errors during audit")
        for e in errors:
            lines.append(f"- `{e}`")
        lines.append("")

    if not (flagged or archived or deleted or merges or errors):
        lines.append("_No findings this week. Memory is clean._")
        lines.append("")

    digest_dir = os.path.join(vault_root, "notes", "memory", "audit")
    os.makedirs(digest_dir, exist_ok=True)
    week_slug = week or "unknown-week"
    path = os.path.join(digest_dir, f"{week_slug}.md")
    _atomic_write(path, "\n".join(lines))
    # Pointer to the newest digest so the banner can link to it consistently
    _atomic_write(os.path.join(digest_dir, "latest.md"),
                  f"# Latest digest pointer\n\nSee [[{week_slug}]].\n")
    return {"path": path, "written": True, "error": None}


# ---------------------------------------------------------------------------
# Step 3: Ticket render (tickets/*.json -> vault/notes/tickets/*.md)
# ---------------------------------------------------------------------------

def render_tickets_to_vault(project_root: str, vault_root: str) -> dict:
    """
    Read all ticket JSON files from tickets/open/ and tickets/closed/ and
    render them as markdown tables + detail sections.

    Returns {"open": N, "closed": N, "error": None | str}
    """
    def _load(folder: str) -> list:
        out = []
        if not os.path.isdir(folder):
            return out
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, fname), encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                pass
        return out

    def _render(tickets: list, title: str) -> str:
        lines = [
            f"# {title}",
            f"*{len(tickets)} tickets - synced {_now_utc()}*",
            "",
            "| ID | Title | Sev | Solution |",
            "|---|---|---|---|",
        ]
        for t in tickets:
            tid = t.get("id", "?")
            ttitle = t.get("title", "").replace("|", "--")[:70]
            sev = t.get("severity", "")
            sol = t.get("linked_solution", "")
            lines.append(f"| {tid} | {ttitle} | {sev} | {sol} |")
        lines.append("")
        for t in tickets:
            fix = t.get("fix_summary", t.get("suggested_fix", ""))
            lines += [
                f"## {t.get('id', '?')} -- {t.get('title', '')}",
                "",
                f"**What failed:** {t.get('what_failed', '')}",
                "",
                f"**Fix:** {fix}",
                "",
            ]
        return "\n".join(lines)

    try:
        open_t = _load(os.path.join(project_root, "tickets", "open"))
        closed_t = _load(os.path.join(project_root, "tickets", "closed"))
        _atomic_write(
            os.path.join(vault_root, "notes", "tickets", "open.md"),
            _render(open_t, "Open Tickets"),
        )
        _atomic_write(
            os.path.join(vault_root, "notes", "tickets", "closed.md"),
            _render(closed_t, "Closed Tickets"),
        )
        return {"open": len(open_t), "closed": len(closed_t), "error": None}
    except Exception as e:
        return {"open": 0, "closed": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# Step 4: Status mirror (docs/STATUS.md -> vault/notes/status.md)
# ---------------------------------------------------------------------------

def render_status_to_vault(project_root: str, vault_root: str) -> dict:
    """
    Copy docs/STATUS.md into vault/notes/status.md with a sync-time header.

    Returns {"written": bool, "error": None | str}
    """
    src = os.path.join(project_root, "docs", "STATUS.md")
    dst = os.path.join(vault_root, "notes", "status.md")
    try:
        if not os.path.exists(src):
            return {"written": False, "error": "docs/STATUS.md not found"}
        content = open(src, encoding="utf-8").read()
        header = f"<!-- synced from docs/STATUS.md at {_now_utc()} -->\n\n"
        _atomic_write(dst, header + content)
        return {"written": True, "error": None}
    except Exception as e:
        return {"written": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Step 5: Per-ticket notes (tickets/closed/*.json -> vault/notes/per-ticket/)
# ---------------------------------------------------------------------------

def render_per_ticket_notes(project_root: str, vault_root: str) -> dict:
    """
    Generate one distilled brief per closed ticket under vault/notes/per-ticket/.
    These are the files VS Code Claude reads when working a specific ticket so
    it doesn't need to load the full ticket directory or derive context from code.

    Only closed tickets get notes — open tickets are already in tickets/open.md.

    Returns {"written": N, "error": None | str}
    """
    closed_dir = os.path.join(project_root, "tickets", "closed")
    out_dir = os.path.join(vault_root, "notes", "per-ticket")

    if not os.path.isdir(closed_dir):
        return {"written": 0, "error": None}

    written = 0
    try:
        for fname in sorted(os.listdir(closed_dir)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(closed_dir, fname), encoding="utf-8") as f:
                    t = json.load(f)
            except Exception:
                continue

            tid = t.get("id", "?")
            title = t.get("title", "")
            severity = t.get("severity", "")
            closed = t.get("closed", "")[:10] if t.get("closed") else ""
            sol = t.get("linked_solution", "")

            what = t.get("what_failed", "")
            root = t.get("where_failed", "")
            why = t.get("why_likely", "")
            fix = t.get("fix_summary", t.get("suggested_fix", ""))
            verif = t.get("verification", {})
            test_name = verif.get("test", "") if isinstance(verif, dict) else ""
            test_result = verif.get("result", "") if isinstance(verif, dict) else ""

            lines = [
                f"# {tid} -- {title}",
                f"*Severity: {severity}  |  Closed: {closed}  |  Solution: {sol}*",
                "",
                "## What Failed",
                what,
                "",
                "## Where / Why",
                root,
                "",
                why,
                "",
                "## Fix Applied",
                fix,
                "",
            ]
            if test_name:
                lines += [
                    "## Verification",
                    f"**Test:** `{test_name}`",
                    f"**Result:** {test_result}",
                    "",
                ]

            # File slug: T-NNN-slug from JSON filename
            slug = fname.replace(".json", "")
            path = os.path.join(out_dir, f"{slug}.md")
            _atomic_write(path, "\n".join(lines))
            written += 1

        return {"written": written, "error": None}
    except Exception as e:
        return {"written": written, "error": str(e)}


# ---------------------------------------------------------------------------
# Master entry point
# ---------------------------------------------------------------------------

def render_hot_file(project_root: str, vault_root: str) -> dict:
    """
    Generate vault/_hot.md — the single HOT-tier context file.

    HOT/WARM/COLD token-reduction strategy (40-94% fewer tokens per session):
    - HOT  (load once at start):   vault/_hot.md only
    - WARM (query on demand):      vault/memory/L3/*.md, vault/notes/per-ticket/
    - COLD (never auto-load):      vault/memory/L2/*.md, docs/_archive/**, tickets/closed/

    Returns {"written": bool, "error": None | str}
    """
    pi_path = os.path.join(project_root, "PI.md")
    ckpt_path = os.path.join(project_root, "CHECKPOINTS", "current.md")

    lines = [
        "# Pi — HOT Context",
        f"*Synced {_now_utc()} · Load only this file at session start; query vault/* on demand.*",
        "",
        "## Identity",
        "Ash · CS undergrad GSU · GNN researcher · Project Pi = continuous engineering loop",
        "",
    ]

    if os.path.isfile(pi_path):
        with open(pi_path, encoding="utf-8") as f:
            pi_text = f.read()
        m = re.search(r"(## §3 NOW.*?)(?=\n---)", pi_text, re.DOTALL)
        if m:
            lines += [m.group(1).strip(), ""]

    if os.path.isfile(ckpt_path):
        with open(ckpt_path, encoding="utf-8") as f:
            ckpt = f.read()
        m2 = re.search(r"(## At-a-glance state.*?)(?=\n##|\Z)", ckpt, re.DOTALL)
        if m2:
            lines += [m2.group(1).strip(), ""]

    lines += [
        "## Key paths",
        "- `pi_agent.py` — agent class, mode switch",
        "- `agent/` — tool dispatch, prompt, turn log, startup banner",
        "- `tools/` — 14 modules (memory, web, obsidian, gmail, browse, etc.)",
        "- `prompts/consciousness.txt` — system prompt (~700 lines)",
        "- `scripts/verify.py` — CI gate (must PASS before closing tickets)",
        "",
        "## Hard rules",
        "1. Never git push/commit without explicit 'go'",
        "2. Never delete files — archive to docs/_archive/",
        "3. 3-line startup; briefing only on demand",
        "4. Log every turn to logs/turns.jsonl",
        "5. Test before claiming success (verify.py PASS)",
        "",
        "## HOT / WARM / COLD tiers",
        "| Tier | Files | Rule |",
        "|------|-------|------|",
        "| HOT  | `vault/_hot.md` (this file) | Read once at session start |",
        "| WARM | `vault/memory/L3/*.md` · `vault/notes/per-ticket/T-NNN.md` | obsidian_search or read on demand |",
        "| COLD | `vault/memory/L2/*.md` · `docs/_archive/**` · `tickets/closed/*.json` | Never auto-load |",
    ]

    dst = os.path.join(vault_root, "_hot.md")
    try:
        _atomic_write(dst, "\n".join(lines) + "\n")
        return {"written": True, "error": None}
    except Exception as e:
        return {"written": False, "error": str(e)}


def sync_vault(memory_tools, project_root: Optional[str] = None) -> dict:
    """
    Run all vault sync steps in order. Non-fatal -- a failure in any step
    is captured in the returned summary and does not block subsequent steps.

    Called by agent/session.py::on_exit() after distillation and promotion.

    Returns:
        {
            "l3":         {"written": N, "categories": [...], "error": None|str},
            "l2":         {"written": N, "categories": [...], "error": None|str},
            "tickets":    {"open": N, "closed": N, "error": None|str},
            "per_ticket": {"written": N, "error": None|str},
            "status":     {"written": bool, "error": None|str},
            "elapsed_s":  float,
        }
    """
    t0 = time.time()
    root = project_root or _project_root()
    vault = _default_vault_root()
    summary: dict = {}

    for label, fn, args in [
        ("l3",         sync_l3_to_vault,         (memory_tools, vault)),
        ("l2",         sync_l2_to_vault,          (memory_tools, vault)),
        ("l2_facts",   sync_l2_facts_to_vault,    (memory_tools, vault)),  # T-081
        # Entity hubs MUST run after l2_facts because hub wiki-links resolve
        # to per-fact slugs that the prior step wrote. (T-096)
        ("entities",   sync_entity_hubs_to_vault, (memory_tools, vault)),
        ("tickets",    render_tickets_to_vault,    (root, vault)),
        ("per_ticket", render_per_ticket_notes,    (root, vault)),
        ("status",     render_status_to_vault,     (root, vault)),
        ("hot",        render_hot_file,            (root, vault)),
    ]:
        try:
            summary[label] = fn(*args)
        except Exception as e:
            summary[label] = {"error": str(e)}

    summary["elapsed_s"] = round(time.time() - t0, 2)

    l3_n = summary["l3"].get("written", 0)
    l2_n = summary["l2"].get("written", 0)
    tk_o = summary["tickets"].get("open", 0)
    tk_c = summary["tickets"].get("closed", 0)
    pt_n = summary["per_ticket"].get("written", 0)
    st = "ok" if summary["status"].get("written") else "skip"
    hot = "ok" if summary.get("hot", {}).get("written") else "skip"
    errors = [k for k, v in summary.items() if isinstance(v, dict) and v.get("error")]
    err_str = f"  WARN: {errors}" if errors else ""
    l2_facts_n = summary.get("l2_facts", {}).get("facts", 0)
    ent_n = summary.get("entities", {}).get("entities", 0)
    print(
        f"[Vault] synced -- L3:{l3_n} cats  L2:{l2_n} cats  L2-facts:{l2_facts_n}  "
        f"entities:{ent_n}  tickets:{tk_o}open/{tk_c}closed  per-ticket:{pt_n}  "
        f"status:{st}  hot:{hot}  ({summary['elapsed_s']}s){err_str}"
    )
    return summary


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_obsidian_read(agent, tool_input, *, memory_override=None):
    return ObsidianTools().obsidian_read(path=tool_input["path"])


def _handle_obsidian_write(agent, tool_input, *, memory_override=None):
    return ObsidianTools().obsidian_write(
        path=tool_input["path"],
        content=tool_input["content"],
    )


def _handle_obsidian_append(agent, tool_input, *, memory_override=None):
    return ObsidianTools().obsidian_append(
        path=tool_input["path"],
        content=tool_input["content"],
    )


def _handle_obsidian_search(agent, tool_input, *, memory_override=None):
    return ObsidianTools().obsidian_search(
        query=tool_input["query"],
        max_results=tool_input.get("max_results", 10),
    )


TOOLS = [
    ToolSpec(
        name="obsidian_read",
        description="Read a note from Ash's Obsidian vault by path (relative to vault root).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "e.g. 'Projects/Pi.md'"},
            },
            "required": ["path"],
        },
        handler=_handle_obsidian_read,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="obsidian_write",
        description="Create or overwrite a note in Ash's Obsidian vault.",
        input_schema={
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Note path relative to vault root"},
                "content": {"type": "string", "description": "Full markdown content"},
            },
            "required": ["path", "content"],
        },
        handler=_handle_obsidian_write,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="obsidian_append",
        description="Append markdown text to an existing Obsidian note (creates it if absent).",
        input_schema={
            "type": "object",
            "properties": {
                "path":    {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=_handle_obsidian_append,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="obsidian_search",
        description="Full-text search across Ash's Obsidian vault. Returns matching note paths and excerpts.",
        input_schema={
            "type": "object",
            "properties": {
                "query":       {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        handler=_handle_obsidian_search,
        success_predicate=lambda r: r.get("success", False),
    ),
]
