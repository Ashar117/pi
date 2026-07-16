"""tools/tools_research.py — Research quality tools (T-227, T-228).

Exposes:
  grounded_search(query)        — Gemini Google-Search grounding with live citations.
  deep_research(query)          — Multi-source synthesis: web + grounded + awareness,
                                  cross-validated, returned as structured dict.
"""
from __future__ import annotations

import os
import re
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Keywords that hint the query is about current events / news.
_NEWS_HINTS = frozenset({
    "today", "latest", "recent", "current", "news", "now", "2024", "2025", "2026",
    "update", "happening", "live", "breaking",
})


def _get_gemini_provider():
    """Return a GeminiProvider instance or None if GEMINI_API_KEY is missing."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return None
    try:
        from core.providers.gemini import GeminiProvider
        return GeminiProvider(api_key=api_key)
    except Exception as e:
        logger.debug("grounded_search: GeminiProvider init failed: %s", e)
        return None


def _fallback_web_search(query: str) -> Dict:
    """Call plain web_search and wrap its result in the grounded_search schema."""
    try:
        from tools.tools_web import WebTools
        raw = WebTools().web_search(query, max_results=5)
        results = raw.get("results") or []
        citations = [
            {"title": r.get("title", ""), "url": r.get("url", "")}
            for r in results
            if r.get("url")
        ]
        snippets = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')}"
            for r in results[:5]
        )
        return {
            "answer": snippets or "(no results)",
            "citations": citations,
            "provider": "web_fallback",
        }
    except Exception as e:
        return {"answer": f"Search unavailable: {e}", "citations": [], "provider": "error"}


def grounded_search(query: str) -> Dict:
    """T-227: Gemini-grounded search with live Google citations.

    Returns a synthesized answer grounded in live Google Search, plus source
    citations extracted from grounding metadata. Falls back to plain web_search
    if Gemini is unavailable or the API call fails.

    Args:
        query: The search query / question to answer.

    Returns:
        {
          "answer": str,            — synthesized answer (or raw snippets on fallback)
          "citations": [            — source list (may be empty on fallback)
            {"title": str, "url": str}, ...
          ],
          "provider": str           — "gemini-grounded" | "web_fallback" | "error"
        }
    """
    gemini = _get_gemini_provider()
    if gemini is None:
        logger.info("grounded_search: no GEMINI_API_KEY, using web_search fallback")
        result = _fallback_web_search(query)
        result.setdefault("provider", "web_fallback")
        return result

    try:
        raw = gemini.grounded_search(query)
        answer = raw.get("answer") or ""
        citations = raw.get("citations") or []
        tokens_in = raw.get("tokens_in", 0)
        tokens_out = raw.get("tokens_out", 0)

        # T-212: record token usage via the existing cost tracker path
        try:
            from core.cost_tracker import record_usage
            record_usage(
                provider="gemini",
                model=gemini.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        except Exception:
            pass

        if not answer and not citations:
            logger.info("grounded_search: empty Gemini response, falling back to web_search")
            result = _fallback_web_search(query)
            result.setdefault("provider", "web_fallback")
            return result

        return {
            "answer": answer,
            "citations": [c for c in citations if c.get("url")],
            "provider": "gemini-grounded",
        }
    except Exception as e:
        logger.warning("grounded_search: Gemini error (%s), falling back to web_search", e)
        result = _fallback_web_search(query)
        result.setdefault("provider", "web_fallback")
        return result


# ── deep_research helpers ─────────────────────────────────────────────────────

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return url


def _looks_like_news(query: str) -> bool:
    words = set(query.lower().split())
    return bool(words & _NEWS_HINTS)


def _gather_sources(query: str) -> List[Dict]:
    """Collect raw results from web_search and grounded_search. Returns list of
    {title, url, snippet, provider} dicts."""
    sources: List[Dict] = []

    # 1. Plain web_search
    try:
        from tools.tools_web import WebTools
        raw = WebTools().web_search(query, max_results=6)
        for r in (raw.get("results") or []):
            if r.get("url"):
                sources.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("snippet", ""),
                    "provider": "web",
                })
    except Exception as e:
        logger.debug("deep_research: web_search error: %s", e)

    # 2. Grounded search (Gemini). Skip if it fell back to web_search — step 1 already ran it.
    try:
        gs = grounded_search(query)
        if gs.get("provider") == "gemini-grounded":
            for c in (gs.get("citations") or []):
                if c.get("url"):
                    sources.append({
                        "title": c.get("title", ""),
                        "url": c["url"],
                        "snippet": gs.get("answer", "")[:300],
                        "provider": "grounded",
                    })
    except Exception as e:
        logger.debug("deep_research: grounded_search error: %s", e)

    # 3. Live awareness (news/current-events queries only)
    if _looks_like_news(query):
        try:
            from tools.tools_awareness import AwarenessTools
            news_raw = AwarenessTools().get_news(max_items=5)
            for item in (news_raw.get("items") or []):
                if item.get("url"):
                    sources.append({
                        "title": item.get("title", ""),
                        "url": item["url"],
                        "snippet": item.get("summary", ""),
                        "provider": "awareness",
                    })
        except Exception as e:
            logger.debug("deep_research: awareness error: %s", e)

    return sources


def _dedup_sources(sources: List[Dict]) -> List[Dict]:
    """Deduplicate by exact URL; keep first occurrence."""
    seen: set = set()
    out: List[Dict] = []
    for s in sources:
        key = s["url"].rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _cross_validate(sources: List[Dict]) -> Dict:
    """Count distinct domains and build an agreement note."""
    domains: set = set()
    for s in sources:
        d = _domain(s["url"])
        if d:
            domains.add(d)
    count = len(domains)
    notes = f"{count} independent source{'s' if count != 1 else ''} found." if count else ""
    return {
        "agreement_notes": notes,
        "source_count": count,
    }


def _synthesize(query: str, sources: List[Dict], validation: Dict) -> Dict:
    """Use Groq (cheap tier) to compose a structured summary from the gathered evidence."""
    if not sources:
        return {
            "summary": "No sources found.",
            "key_points": [],
            "confidence": "low",
        }

    evidence_block = "\n".join(
        f"[{i+1}] ({s['provider']}) {s['title']} — {s['url']}\n  {s['snippet'][:200]}"
        for i, s in enumerate(sources[:10])
    )
    agreement = validation.get("agreement_notes", "")
    source_count = validation.get("source_count", 1)
    confidence = "high" if source_count >= 3 else "medium" if source_count >= 2 else "low"

    prompt = (
        f"Research query: {query}\n\n"
        f"Evidence from {source_count} sources:\n{evidence_block}\n\n"
        f"Agreement notes: {agreement}\n\n"
        "Write a concise research summary in this exact JSON structure:\n"
        '{"summary": "<2-3 sentence direct answer>", "key_points": ["<point 1>", "<point 2>", ...]}\n'
        "Base it only on the evidence above. Mark uncertain claims with (unverified). "
        "Return only the JSON, no other text."
    )

    try:
        groq_key = os.getenv("GROQ_API_KEY", "")
        if groq_key:
            from groq import Groq
            import json as _json
            client = Groq(api_key=groq_key)
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.2,
            )
            raw_text = resp.choices[0].message.content.strip()
            # Extract JSON even if wrapped in markdown fences
            m = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if m:
                parsed = _json.loads(m.group())
                return {
                    "summary": parsed.get("summary", ""),
                    "key_points": parsed.get("key_points", []),
                    "confidence": confidence,
                }
    except Exception as e:
        logger.debug("deep_research: Groq synthesis error: %s", e)

    # Fallback: build summary from snippets directly
    snippets = [s["snippet"] for s in sources[:3] if s.get("snippet")]
    return {
        "summary": " ".join(snippets)[:600] or "See sources below.",
        "key_points": [s["title"] for s in sources[:5] if s.get("title")],
        "confidence": confidence,
    }


def deep_research(query: str, recency_hint: Optional[str] = None) -> Dict:
    """T-228: Multi-source research synthesis with cross-validation.

    Gathers from web_search + grounded_search (and live awareness for news queries),
    deduplicates by URL, cross-validates by domain, and synthesizes via Groq.

    Returns:
        {
          "summary": str,
          "key_points": [str, ...],
          "sources": [{"title": str, "url": str}, ...],
          "agreement_notes": str,
          "confidence": "high" | "medium" | "low",
          "source_count": int,
        }
    """
    if recency_hint:
        query = f"{query} ({recency_hint})"

    raw_sources = _gather_sources(query)
    sources = _dedup_sources(raw_sources)
    validation = _cross_validate(sources)
    synthesis = _synthesize(query, sources, validation)

    clean_sources = [
        {"title": s["title"], "url": s["url"]}
        for s in sources
        if s.get("url")
    ]

    return {
        "summary": synthesis.get("summary", ""),
        "key_points": synthesis.get("key_points", []),
        "sources": clean_sources,
        "agreement_notes": validation.get("agreement_notes", ""),
        "confidence": synthesis.get("confidence", "low"),
        "source_count": validation.get("source_count", 0),
    }


# ── Tool registry ─────────────────────────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_grounded_search(agent, tool_input, *, memory_override=None):
    return grounded_search(query=tool_input["query"])


def _handle_deep_research(agent, tool_input, *, memory_override=None):
    return deep_research(
        query=tool_input["query"],
        recency_hint=tool_input.get("recency_hint"),
    )


def _handle_deep_debate(agent, tool_input, *, memory_override=None):
    """T-262: root-mode access to the 3-agent (Claude/Gemini/Groq) debate.

    interactive=False — this runs mid-turn, not in the REPL "research mode"
    loop, so the blocking Enter prompt must be skipped.
    """
    from core.research_mode import run_research_mode
    synthesis = run_research_mode(
        question=tool_input["question"],
        rounds=tool_input.get("rounds", 2),
        interactive=False,
    )
    return {"success": True, "synthesis": synthesis}


TOOLS = [
    ToolSpec(
        name="grounded_search",
        description=(
            "Search the web with Google-AI grounding (Gemini). Returns a synthesised "
            "answer grounded in live Google Search results, plus source citations "
            "(title + URL). Falls back to plain web_search if Gemini is unavailable. "
            "Use this for research questions that need up-to-date facts with sources."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query or question to research",
                }
            },
            "required": ["query"],
        },
        handler=_handle_grounded_search,
        success_predicate=lambda r: bool(r.get("answer")),
    ),
    ToolSpec(
        name="deep_research",
        description=(
            "Multi-source research: gathers from web_search + Gemini grounded search "
            "+ live news (for current-events queries), cross-validates sources, and "
            "returns a structured synthesis (summary, key_points, sources, confidence). "
            "Use when the user needs a thorough, cited answer — not just raw search results."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The research question or topic",
                },
                "recency_hint": {
                    "type": "string",
                    "description": "Optional recency constraint, e.g. 'last 7 days' or 'June 2026'",
                },
            },
            "required": ["query"],
        },
        handler=_handle_deep_research,
        success_predicate=lambda r: bool(r.get("summary")),
    ),
    ToolSpec(
        name="deep_debate",
        description=(
            "Run a 3-model debate (Claude, Gemini, Groq/Llama) on a genuinely contested "
            "or high-stakes question, then return their synthesized final positions. "
            "Costs ~2 cents and ~30 seconds — use sparingly, only when a single model's "
            "answer is likely to be contested or the stakes clearly warrant multi-model "
            "critique. Not for routine questions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The contested or high-stakes question to debate",
                },
                "rounds": {
                    "type": "integer",
                    "default": 2,
                    "description": "1 = independent answers only; 2 = independent + rebuttal round",
                },
            },
            "required": ["question"],
        },
        handler=_handle_deep_debate,
        success_predicate=lambda r: r.get("success", False),
    ),
]
