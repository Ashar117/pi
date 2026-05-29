"""memory/recall.py — T-123: recall referenced (tagged/replied-to) messages.

When the user replies to (quotes) a previous Pi message in Telegram, the
quoted text arrives as bubble.reply_targets. Without recall, Pi treats the
user's new message as standalone — losing the reference and (for media)
wasting vision tokens re-analysing.

recall_referenced(text) embeds the referenced text and searches L3 first,
then L2, for cosine matches >= 0.85. Hits are returned so the caller can
inject "You previously wrote: <recalled>" into the model context.

Designed to fail silently — recall is best-effort. Returns [] on any error.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


_DEFAULT_THRESHOLD = 0.85


def _track(event: str, exc: Optional[Exception] = None, **context) -> None:
    try:
        from agent.observability import track_silent
        track_silent(f"recall.{event}", exc, context=context)
    except Exception:
        pass


def _get_embedding(text: str) -> Optional[List[float]]:
    """Compute embedding via memory.semantic_dedup. Returns None on failure."""
    try:
        from memory.semantic_dedup import get_embedding
        return get_embedding(text)
    except Exception as e:
        _track("embed_failed", e, text_prefix=text[:50])
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    try:
        from memory.semantic_dedup import cosine_similarity
        return cosine_similarity(a, b)
    except Exception:
        return 0.0


def _l3_candidates(db_path: Path, limit: int = 500) -> List[Dict[str, Any]]:
    """Return active L3 rows (skipping derived placeholders and invalidated)."""
    if not Path(db_path).exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute(
                """
                SELECT id, content, importance, category, created_at
                FROM l3_cache
                WHERE invalid_at IS NULL
                  AND (superseded_by IS NULL OR superseded_by = '')
                  AND (kind IS NULL OR kind != 'derived' OR content NOT LIKE '(pending%')
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                {"id": r[0], "content": r[1], "importance": r[2], "category": r[3], "created_at": r[4], "tier": "l3"}
                for r in cur.fetchall()
            ]
        finally:
            conn.close()
    except Exception as e:
        _track("l3_query_failed", e)
        return []


def recall_referenced(
    text: str,
    db_path: Optional[Path] = None,
    threshold: float = _DEFAULT_THRESHOLD,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Find L3/L2 entries semantically matching the referenced text.

    Args:
        text: the quoted/referenced text (typically from bubble.reply_targets)
        db_path: SQLite path. Defaults to data/pi.db.
        threshold: minimum cosine similarity (default 0.85).
        limit: max hits to return.

    Returns: list of {id, content, importance, category, created_at, tier, score}.
    Empty list on no match OR on any failure (best-effort).
    """
    if not text or not text.strip():
        return []

    if db_path is None:
        db_path = Path(__file__).parent.parent / "data" / "pi.db"

    query_emb = _get_embedding(text)
    if query_emb is None:
        return []

    candidates = _l3_candidates(db_path)
    if not candidates:
        _track("miss", None, text_prefix=text[:50], reason="no_candidates")
        return []

    scored: List[Dict[str, Any]] = []
    for cand in candidates:
        cand_emb = _get_embedding(cand["content"])
        if cand_emb is None:
            continue
        score = _cosine(query_emb, cand_emb)
        if score >= threshold:
            cand["score"] = round(score, 3)
            scored.append(cand)

    if not scored:
        _track("miss", None, text_prefix=text[:50], reason="below_threshold", candidates=len(candidates))
        return []

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def format_recall_context(hits: List[Dict[str, Any]]) -> str:
    """Render recall hits as a context block suitable for prepending to user input."""
    if not hits:
        return ""
    lines = ["[RECALLED CONTEXT — facts previously stored that match what you referenced:]"]
    for h in hits:
        when = (h.get("created_at") or "")[:10]
        lines.append(f"- ({when}, importance {h.get('importance','?')}) {h['content']}")
    return "\n".join(lines)
