#!/usr/bin/env python3
"""
tools/mcp_obsidian_server.py — MCP server wrapping Obsidian Local REST API.

Run by Claude Code as an MCP subprocess. Exposes four tools:
  obsidian_read   — fetch a note's content by path
  obsidian_write  — create/overwrite a note
  obsidian_append — append text to a note
  obsidian_search — full-text search across the vault
"""
import os
import httpx
from mcp.server.fastmcp import FastMCP

OBSIDIAN_HOST = os.environ.get("OBSIDIAN_HOST", "http://127.0.0.1:27123")
OBSIDIAN_KEY  = os.environ.get("OBSIDIAN_API_KEY", "")

mcp = FastMCP("obsidian")

def _headers() -> dict:
    return {"Authorization": f"Bearer {OBSIDIAN_KEY}", "Content-Type": "text/markdown"}

def _client() -> httpx.Client:
    return httpx.Client(base_url=OBSIDIAN_HOST, headers=_headers(), verify=False, timeout=10)


@mcp.tool()
def obsidian_read(path: str) -> str:
    """Return the markdown content of a vault note. path is relative to vault root (e.g. 'docs/STATUS.md')."""
    with _client() as c:
        r = c.get(f"/vault/{path.lstrip('/')}")
        r.raise_for_status()
        return r.text


@mcp.tool()
def obsidian_write(path: str, content: str) -> str:
    """Create or overwrite a vault note. path is relative to vault root."""
    with _client() as c:
        r = c.put(f"/vault/{path.lstrip('/')}", content=content.encode())
        r.raise_for_status()
        return f"Written: {path}"


@mcp.tool()
def obsidian_append(path: str, content: str) -> str:
    """Append text to an existing vault note (creates it if absent)."""
    with _client() as c:
        r = c.post(f"/vault/{path.lstrip('/')}", content=content.encode())
        r.raise_for_status()
        return f"Appended to: {path}"


@mcp.tool()
def obsidian_search(query: str, context_length: int = 200) -> str:
    """Search the vault for query. Returns matching note paths and excerpts."""
    with _client() as c:
        r = c.post(
            "/search/simple/",
            params={"query": query, "contextLength": context_length},
        )
        r.raise_for_status()
        hits = r.json()
    if not hits:
        return "No results."
    lines = []
    for h in hits[:10]:
        lines.append(f"### {h.get('filename', '?')}")
        for ctx in h.get("matches", [])[:2]:
            lines.append(ctx.get("context", ""))
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
