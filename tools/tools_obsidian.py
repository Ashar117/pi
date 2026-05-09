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
    render_tickets_to_vault(project_root, vault_root)
    render_per_ticket_notes(project_root, vault_root)
    render_status_to_vault(project_root, vault_root)
"""

import json
import os
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
        ("tickets",    render_tickets_to_vault,    (root, vault)),
        ("per_ticket", render_per_ticket_notes,    (root, vault)),
        ("status",     render_status_to_vault,     (root, vault)),
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
    errors = [k for k, v in summary.items() if isinstance(v, dict) and v.get("error")]
    err_str = f"  WARN: {errors}" if errors else ""
    print(
        f"[Vault] synced -- L3:{l3_n} cats  L2:{l2_n} cats  "
        f"tickets:{tk_o}open/{tk_c}closed  per-ticket:{pt_n}  status:{st}"
        f"  ({summary['elapsed_s']}s){err_str}"
    )
    return summary
