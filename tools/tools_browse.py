"""
tools/tools_browse.py — Internet browsing for Pi.

Uses battle-tested libraries instead of hand-rolled parsers:
  trafilatura  — best-in-class web content extraction
  praw         — official Reddit API wrapper (read-only, no auth)
  scholarly    — Google Scholar scraper
  requests     — Discord REST API, Semantic Scholar fallback

Capabilities:
  fetch(url)              — trafilatura content extraction
  reddit_browse(sub)      — PRAW subreddit browsing
  reddit_search(query)    — PRAW Reddit search
  reddit_thread(url)      — PRAW thread + comments
  scholar_search(query)   — scholarly (Google Scholar) + Semantic Scholar fallback
  discord_read(channel)   — Discord REST API (needs DISCORD_BOT_TOKEN)
"""

import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import requests

_TIMEOUT = 15
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}

try:
    import trafilatura
    _TRAFILATURA_OK = True
except ImportError:
    _TRAFILATURA_OK = False

try:
    import praw as _praw
    _PRAW_OK = True
except ImportError:
    _PRAW_OK = False

try:
    from scholarly import scholarly as _scholarly
    _SCHOLARLY_OK = True
except ImportError:
    _SCHOLARLY_OK = False


def _praw_client():
    """
    PRAW client using credentials from .env if present.
    REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET: from reddit.com/prefs/apps (script app).
    Returns None if credentials are not configured.
    """
    if not _PRAW_OK:
        return None
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    try:
        return _praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="Pi-Agent/2.0 (personal assistant; read-only)",
        )
    except Exception:
        return None


def _reddit_json(url: str, params: dict = None) -> Optional[dict]:
    """Public Reddit JSON API — no auth, always works for public subs."""
    headers = {**_HEADERS, "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


class BrowseTools:

    # ── General web fetch ──────────────────────────────────────────────────────

    @staticmethod
    def fetch(url: str, max_chars: int = 8000) -> Dict:
        """
        Fetch a URL and return its main text content.
        Uses trafilatura for best-in-class content extraction.
        """
        try:
            if _TRAFILATURA_OK:
                downloaded = trafilatura.fetch_url(url)
                if downloaded:
                    text = trafilatura.extract(
                        downloaded,
                        include_comments=False,
                        include_tables=True,
                        no_fallback=False,
                    ) or ""
                    if text:
                        if len(text) > max_chars:
                            text = text[:max_chars] + f"\n... [{len(text)-max_chars} chars truncated]"
                        return {"success": True, "url": url, "content": text, "extractor": "trafilatura"}

            # Fallback: raw requests + basic tag strip
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            r.raise_for_status()
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s{2,}", " ", text).strip()
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n... [{len(text)-max_chars} chars truncated]"
            return {"success": True, "url": url, "content": text, "extractor": "fallback"}

        except Exception as e:
            return {"success": False, "url": url, "error": str(e)}

    # ── Reddit ─────────────────────────────────────────────────────────────────

    @staticmethod
    def reddit_browse(subreddit: str, sort: str = "hot", count: int = 10) -> Dict:
        """Browse a subreddit. Uses PRAW if credentials set in .env, else public JSON API."""
        rc = _praw_client()
        if rc:
            try:
                sub     = rc.subreddit(subreddit)
                listing = {"hot": sub.hot, "new": sub.new, "top": sub.top, "rising": sub.rising}.get(sort, sub.hot)
                items   = []
                for post in listing(limit=min(count, 25)):
                    items.append({
                        "title":     post.title,
                        "score":     post.score,
                        "comments":  post.num_comments,
                        "url":       post.url,
                        "permalink": f"https://reddit.com{post.permalink}",
                        "flair":     post.link_flair_text or "",
                        "author":    str(post.author) if post.author else "[deleted]",
                        "selftext":  post.selftext[:300] if post.selftext else "",
                    })
                return {"success": True, "subreddit": subreddit, "sort": sort, "count": len(items), "posts": items}
            except Exception:
                pass  # fall through to public API

        # Public JSON API (no auth required for public subreddits)
        data = _reddit_json(
            f"https://www.reddit.com/r/{subreddit}/{sort}.json",
            params={"limit": min(count, 25)},
        )
        if not data:
            return {"success": False, "subreddit": subreddit, "error": "Failed to fetch subreddit"}
        posts = [c["data"] for c in data.get("data", {}).get("children", [])]
        items = [{
            "title":     p.get("title", ""),
            "score":     p.get("score", 0),
            "comments":  p.get("num_comments", 0),
            "url":       p.get("url", ""),
            "permalink": f"https://reddit.com{p.get('permalink','')}",
            "flair":     p.get("link_flair_text") or "",
            "author":    p.get("author", ""),
            "selftext":  (p.get("selftext") or "")[:300],
        } for p in posts]
        return {"success": True, "subreddit": subreddit, "sort": sort, "count": len(items), "posts": items}

    @staticmethod
    def reddit_search(query: str, subreddit: str = "", count: int = 10) -> Dict:
        """Search Reddit. Uses PRAW if credentials set, else public search JSON."""
        rc = _praw_client()
        if rc:
            try:
                target = rc.subreddit(subreddit) if subreddit else rc.subreddit("all")
                items  = []
                for post in target.search(query, limit=min(count, 25), sort="relevance"):
                    items.append({
                        "title":     post.title,
                        "score":     post.score,
                        "subreddit": str(post.subreddit),
                        "comments":  post.num_comments,
                        "permalink": f"https://reddit.com{post.permalink}",
                        "selftext":  post.selftext[:200] if post.selftext else "",
                    })
                return {"success": True, "query": query, "count": len(items), "posts": items}
            except Exception:
                pass

        # Public search JSON
        base = f"https://www.reddit.com/r/{subreddit}/search.json" if subreddit else "https://www.reddit.com/search.json"
        params = {"q": query, "limit": min(count, 25), "sort": "relevance"}
        if subreddit:
            params["restrict_sr"] = "on"
        data = _reddit_json(base, params)
        if not data:
            return {"success": False, "query": query, "error": "Reddit search failed"}
        posts = [c["data"] for c in data.get("data", {}).get("children", [])]
        items = [{
            "title":     p.get("title", ""),
            "score":     p.get("score", 0),
            "subreddit": p.get("subreddit", ""),
            "comments":  p.get("num_comments", 0),
            "permalink": f"https://reddit.com{p.get('permalink','')}",
            "selftext":  (p.get("selftext") or "")[:200],
        } for p in posts]
        return {"success": True, "query": query, "count": len(items), "posts": items}

    @staticmethod
    def reddit_thread(permalink: str, max_comments: int = 20) -> Dict:
        """Read a Reddit thread + top comments. PRAW if creds, else public JSON."""
        rc = _praw_client()
        if rc:
            try:
                path = permalink.replace("https://reddit.com", "").replace("https://www.reddit.com", "")
                sub  = rc.submission(url=f"https://reddit.com{path}" if path.startswith("/") else permalink)
                sub.comments.replace_more(limit=0)
                post = {"title": sub.title, "author": str(sub.author) if sub.author else "[deleted]",
                        "score": sub.score, "selftext": (sub.selftext or "")[:1000], "url": sub.url}
                comments = [{"author": str(c.author) if c.author else "[deleted]",
                             "score": c.score, "body": c.body[:400]}
                            for c in sub.comments[:max_comments] if hasattr(c, "body")]
                return {"success": True, "post": post, "comments": comments, "count": len(comments)}
            except Exception:
                pass

        # Public .json endpoint
        url = (permalink.rstrip("/") + ".json") if not permalink.endswith(".json") else permalink
        if not url.startswith("http"):
            url = "https://www.reddit.com" + url
        data = _reddit_json(url)
        if not data or not isinstance(data, list) or len(data) < 2:
            return {"success": False, "error": "Failed to fetch thread"}
        pd   = data[0]["data"]["children"][0]["data"]
        post = {"title": pd.get("title",""), "author": pd.get("author",""),
                "score": pd.get("score",0),  "selftext": (pd.get("selftext") or "")[:1000]}
        comments = []
        for c in data[1]["data"]["children"][:max_comments]:
            if c.get("kind") == "t1":
                d = c["data"]
                comments.append({"author": d.get("author",""), "score": d.get("score",0), "body": (d.get("body",""))[:400]})
        return {"success": True, "post": post, "comments": comments, "count": len(comments)}

    # ── Google Scholar ─────────────────────────────────────────────────────────

    @staticmethod
    def scholar_search(query: str, count: int = 5) -> Dict:
        """
        Search academic papers via scholarly (Google Scholar).
        Falls back to Semantic Scholar API if scholarly is blocked.
        """
        if _SCHOLARLY_OK:
            try:
                results = []
                search  = _scholarly.search_pubs(query)
                for _ in range(min(count, 8)):
                    pub  = next(search)
                    bib  = pub.get("bib", {})
                    authors = bib.get("author", "")
                    if isinstance(authors, list):
                        authors = ", ".join(authors[:3])
                        if len(bib.get("author", [])) > 3:
                            authors += " et al."
                    results.append({
                        "title":     bib.get("title", ""),
                        "authors":   authors,
                        "year":      bib.get("pub_year", ""),
                        "abstract":  (bib.get("abstract") or "")[:300],
                        "citations": pub.get("num_citations", 0),
                        "pdf_url":   pub.get("eprint_url", ""),
                        "source":    "google_scholar",
                    })
                if results:
                    return {"success": True, "query": query, "count": len(results), "papers": results}
            except Exception:
                pass  # Scholar blocked or rate-limited → fall through to Semantic Scholar

        # Semantic Scholar fallback
        return BrowseTools._semantic_scholar(query, count)

    @staticmethod
    def _semantic_scholar(query: str, count: int) -> Dict:
        """Semantic Scholar API (free, no key, rate-limited)."""
        url     = "https://api.semanticscholar.org/graph/v1/paper/search"
        params  = {"query": query, "limit": min(count, 10),
                   "fields": "title,authors,year,abstract,citationCount,externalIds,openAccessPdf"}
        headers = {"User-Agent": "Pi-PersonalAgent/2.0", "Accept": "application/json"}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
            if r.status_code == 429:
                raise Exception("rate limited")
            r.raise_for_status()
            papers = r.json().get("data", [])
            items  = []
            for p in papers:
                authors = ", ".join(a.get("name", "") for a in p.get("authors", [])[:3])
                if len(p.get("authors", [])) > 3:
                    authors += " et al."
                pdf_url  = (p.get("openAccessPdf") or {}).get("url", "")
                arxiv_id = (p.get("externalIds") or {}).get("ArXiv", "")
                items.append({
                    "title":     p.get("title", ""),
                    "authors":   authors,
                    "year":      p.get("year"),
                    "abstract":  (p.get("abstract") or "")[:300],
                    "citations": p.get("citationCount", 0),
                    "pdf_url":   pdf_url or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
                    "source":    "semantic_scholar",
                })
            if items:
                return {"success": True, "query": query, "count": len(items), "papers": items}
        except Exception:
            pass

        # ArXiv last resort
        return BrowseTools._arxiv_search(query, count)

    @staticmethod
    def _arxiv_search(query: str, count: int) -> Dict:
        """ArXiv API — always free, always available."""
        url    = "http://export.arxiv.org/api/query"
        params = {"search_query": f"all:{query}", "start": 0, "max_results": min(count, 10)}
        try:
            r    = requests.get(url, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            ns   = {"atom": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(r.text)

            def txt(el, tag):
                t = el.find(tag, ns)
                return (t.text or "").strip() if t is not None else ""

            items = []
            for entry in root.findall("atom:entry", ns)[:count]:
                title   = txt(entry, "atom:title").replace("\n", " ")
                summary = txt(entry, "atom:summary")
                link    = txt(entry, "atom:id").replace("http://", "https://")
                for lnk in entry.findall("atom:link", ns):
                    if lnk.get("title") == "pdf":
                        link = lnk.get("href", link)
                        break
                authors = [txt(a, "atom:name") for a in entry.findall("atom:author", ns)[:3]]
                items.append({
                    "title":     title,
                    "authors":   ", ".join(a for a in authors if a),
                    "year":      None,
                    "abstract":  summary[:300],
                    "citations": 0,
                    "pdf_url":   link,
                    "source":    "arxiv",
                })
            return {"success": True, "query": query, "count": len(items), "papers": items, "source": "arxiv_fallback"}
        except Exception as e:
            return {"success": False, "query": query, "error": str(e)}

    # ── Discord ────────────────────────────────────────────────────────────────

    @staticmethod
    def discord_read(channel_id: str, count: int = 20) -> Dict:
        """Read recent messages from a Discord channel via Bot API."""
        token = os.environ.get("DISCORD_BOT_TOKEN")
        if not token:
            return {
                "success": False,
                "error": (
                    "DISCORD_BOT_TOKEN not set in .env.\n"
                    "Setup:\n"
                    "  1. discord.com/developers -> New Application -> Bot -> Reset Token\n"
                    "  2. Add DISCORD_BOT_TOKEN=<token> to .env\n"
                    "  3. Invite bot: OAuth2 -> bot scope -> Read Message History permission\n"
                    "  4. Channel ID: Discord -> right-click channel -> Copy Channel ID"
                ),
            }
        url     = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        try:
            r = requests.get(url, headers=headers, params={"limit": min(count, 100)}, timeout=_TIMEOUT)
            if r.status_code == 401:
                return {"success": False, "error": "Invalid DISCORD_BOT_TOKEN"}
            if r.status_code == 403:
                return {"success": False, "error": "Bot lacks permission to read this channel"}
            if r.status_code == 404:
                return {"success": False, "error": f"Channel {channel_id} not found"}
            r.raise_for_status()
            msgs  = r.json()
            items = [
                {
                    "id":          m.get("id", ""),
                    "author":      m.get("author", {}).get("username", "?"),
                    "content":     m.get("content", "")[:500],
                    "timestamp":   m.get("timestamp", ""),
                    "attachments": len(m.get("attachments", [])),
                }
                for m in msgs
            ]
            return {"success": True, "channel_id": channel_id, "count": len(items), "messages": items}
        except Exception as e:
            return {"success": False, "channel_id": channel_id, "error": str(e)}


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_web_browse(agent, tool_input, *, memory_override=None):
    return BrowseTools.fetch(
        url=tool_input["url"],
        max_chars=tool_input.get("max_chars", 8000),
    )


def _handle_reddit_browse(agent, tool_input, *, memory_override=None):
    return BrowseTools.reddit_browse(
        subreddit=tool_input["subreddit"],
        sort=tool_input.get("sort", "hot"),
        count=tool_input.get("count", 10),
    )


def _handle_reddit_search(agent, tool_input, *, memory_override=None):
    return BrowseTools.reddit_search(
        query=tool_input["query"],
        subreddit=tool_input.get("subreddit", ""),
        count=tool_input.get("count", 10),
    )


def _handle_reddit_thread(agent, tool_input, *, memory_override=None):
    return BrowseTools.reddit_thread(
        permalink=tool_input["permalink"],
        max_comments=tool_input.get("max_comments", 20),
    )


def _handle_scholar_search(agent, tool_input, *, memory_override=None):
    return BrowseTools.scholar_search(
        query=tool_input["query"],
        count=tool_input.get("count", 5),
    )


def _handle_discord_read(agent, tool_input, *, memory_override=None):
    return BrowseTools.discord_read(
        channel_id=tool_input["channel_id"],
        count=tool_input.get("count", 20),
    )


def _handle_fetch(agent, tool_input, *, memory_override=None):
    """Merged handler for web_browse / reddit_* / discord_read.

    Routes by explicit 'source' field or auto-detects from field presence
    (for backward compat when called via legacy alias names).
    """
    source = tool_input.get("source", "").lower()

    # Auto-detect from field presence when called via legacy alias
    if not source:
        if "channel_id" in tool_input:
            source = "discord"
        elif "permalink" in tool_input:
            source = "reddit_thread"
        elif "query" in tool_input:
            source = "reddit_search"
        elif "subreddit" in tool_input:
            source = "reddit_browse"
        elif "url" in tool_input:
            source = "web"
        else:
            return {"success": False, "error": "Cannot determine source. Provide 'source' field (web/reddit/discord)."}

    if source == "web":
        return BrowseTools.fetch(
            url=tool_input["url"],
            max_chars=tool_input.get("max_chars", 8000),
        )
    if source in ("reddit_thread", "reddit_thread"):
        return BrowseTools.reddit_thread(
            permalink=tool_input["permalink"],
            max_comments=tool_input.get("max_comments", 20),
        )
    if source in ("reddit_search", "reddit") and "query" in tool_input:
        return BrowseTools.reddit_search(
            query=tool_input["query"],
            subreddit=tool_input.get("subreddit", ""),
            count=tool_input.get("count", 10),
        )
    if source in ("reddit_browse", "reddit"):
        return BrowseTools.reddit_browse(
            subreddit=tool_input["subreddit"],
            sort=tool_input.get("sort", "hot"),
            count=tool_input.get("count", 10),
        )
    if source == "discord":
        return BrowseTools.discord_read(
            channel_id=tool_input["channel_id"],
            count=tool_input.get("count", 20),
        )
    return {"success": False, "error": f"Unknown source '{source}'. Use web, reddit, or discord."}


TOOLS = [
    ToolSpec(
        name="fetch",
        description=(
            "Fetch content from the web, Reddit, or Discord. "
            "source='web': fetch any URL (articles, docs, GitHub). "
            "source='reddit': browse subreddit (subreddit=), search posts (query=), or read thread (permalink=). "
            "source='discord': read channel messages (channel_id=)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source":      {"type": "string",
                                "enum": ["web", "reddit", "discord"],
                                "description": "Where to fetch from"},
                "url":         {"type": "string",
                                "description": "URL to fetch (web source)"},
                "subreddit":   {"type": "string",
                                "description": "Subreddit name without r/ (reddit browse)"},
                "query":       {"type": "string",
                                "description": "Search query (reddit search)"},
                "permalink":   {"type": "string",
                                "description": "Reddit permalink URL (reddit thread)"},
                "channel_id":  {"type": "string",
                                "description": "Discord channel ID (discord source)"},
                "sort":        {"type": "string",
                                "enum": ["hot", "new", "top", "rising"], "default": "hot"},
                "count":       {"type": "integer", "default": 10},
                "max_chars":   {"type": "integer", "default": 8000,
                                "description": "Max characters returned (web source)"},
                "max_comments":{"type": "integer", "default": 20,
                                "description": "Max comments returned (reddit thread)"},
            },
            "required": ["source"],
        },
        handler=_handle_fetch,
        success_predicate=lambda r: r.get("success", False),
        aliases=("web_browse", "reddit_browse", "reddit_search", "reddit_thread", "discord_read"),
    ),
    ToolSpec(
        name="scholar_search",
        description=(
            "Search academic papers via Semantic Scholar (free). Returns titles, authors, "
            "year, abstract, citation count."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "count": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        handler=_handle_scholar_search,
        success_predicate=lambda r: r.get("success", False),
    ),
]
