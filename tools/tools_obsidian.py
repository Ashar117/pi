"""
tools/tools_obsidian.py — Obsidian vault integration for Pi's tool loop.

Wraps the Obsidian Local REST API (obsidian-local-rest-api plugin).
Pi can read, write, append, and search Obsidian notes directly.

Requires:
  - Obsidian desktop running with the Local REST API plugin active
  - OBSIDIAN_HOST env var (default: http://127.0.0.1:27123)
  - OBSIDIAN_API_KEY env var (from the plugin settings)
"""

import os
from typing import Dict, List, Optional

import httpx

_DEFAULT_HOST = "http://127.0.0.1:27123"
_TIMEOUT = 10


class ObsidianTools:
    """Direct HTTP wrapper for Obsidian Local REST API."""

    def __init__(self, host: str = "", api_key: str = ""):
        self.host = (host or os.environ.get("OBSIDIAN_HOST", _DEFAULT_HOST)).rstrip("/")
        self.api_key = api_key or os.environ.get("OBSIDIAN_API_KEY", "")

    def _client(self) -> httpx.Client:
        headers = {"Content-Type": "text/markdown"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.Client(
            base_url=self.host,
            headers=headers,
            verify=False,   # local self-signed cert
            timeout=_TIMEOUT,
        )

    def _available(self) -> bool:
        """Return True if Obsidian is reachable."""
        try:
            with self._client() as c:
                c.get("/").raise_for_status()
            return True
        except Exception:
            return False

    # ── read ──────────────────────────────────────────────────────────────────

    def obsidian_read(self, path: str) -> Dict:
        """Read a vault note's markdown content.

        Args:
            path: Note path relative to vault root, e.g. 'Pi/Sessions/2026-05.md'

        Returns:
            {"content": str, "path": str, "success": bool}
        """
        try:
            with self._client() as c:
                r = c.get(f"/vault/{path.lstrip('/')}")
                r.raise_for_status()
            return {"content": r.text, "path": path, "success": True}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"content": "", "path": path, "success": False,
                        "error": f"Note not found: {path}"}
            return {"content": "", "path": path, "success": False,
                    "error": str(e)}
        except Exception as e:
            return {"content": "", "path": path, "success": False,
                    "error": f"Obsidian unavailable: {e}"}

    # ── write ─────────────────────────────────────────────────────────────────

    def obsidian_write(self, path: str, content: str) -> Dict:
        """Create or overwrite a vault note.

        Args:
            path:    Note path relative to vault root.
            content: Full markdown content to write.

        Returns:
            {"path": str, "success": bool}
        """
        try:
            with self._client() as c:
                r = c.put(f"/vault/{path.lstrip('/')}", content=content.encode())
                r.raise_for_status()
            return {"path": path, "success": True}
        except Exception as e:
            return {"path": path, "success": False, "error": f"Write failed: {e}"}

    # ── append ────────────────────────────────────────────────────────────────

    def obsidian_append(self, path: str, content: str) -> Dict:
        """Append text to an existing vault note (creates it if absent).

        Args:
            path:    Note path relative to vault root.
            content: Markdown text to append.

        Returns:
            {"path": str, "success": bool}
        """
        try:
            with self._client() as c:
                r = c.post(f"/vault/{path.lstrip('/')}", content=content.encode())
                r.raise_for_status()
            return {"path": path, "success": True}
        except Exception as e:
            return {"path": path, "success": False, "error": f"Append failed: {e}"}

    # ── search ────────────────────────────────────────────────────────────────

    def obsidian_search(self, query: str, max_results: int = 10) -> Dict:
        """Full-text search across the Obsidian vault.

        Args:
            query:       Search string (plain text, not regex).
            max_results: Maximum notes to return (default 10).

        Returns:
            {"results": [{"path": str, "excerpts": [str]}], "count": int, "success": bool}
        """
        try:
            with self._client() as c:
                r = c.post(
                    "/search/simple/",
                    params={"query": query, "contextLength": 200},
                )
                r.raise_for_status()
            hits = r.json()[:max_results]
            results = []
            for h in hits:
                excerpts = [
                    ctx.get("context", "")
                    for ctx in h.get("matches", [])[:3]
                ]
                results.append({"path": h.get("filename", ""), "excerpts": excerpts})
            return {"results": results, "count": len(results), "success": True}
        except Exception as e:
            return {"results": [], "count": 0, "success": False,
                    "error": f"Search failed: {e}"}
