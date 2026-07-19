"""memory/semantic_dedup.py — embedding-based L2 duplicate detection (T-080).

Catches semantic paraphrases that the lexical dedup in tools_memory misses.
Example failure modes the lexical check misses:
    "I like dogs"          vs "I'm a dog person"      (0 shared stopword-stripped tokens)
    "I study at GSU"       vs "I'm a Georgia State student"

Pipeline on each L2 write:
    1. Compute Gemini embedding for the new fact (cheap, ~$0.00002).
    2. Pull stored embeddings for existing rows in the same category.
    3. Cosine-similarity against each.
    4. >= 0.90 -> drop as duplicate (return the existing row id).
    5. 0.75-0.90 -> borderline; ask Claude Haiku for tiebreak.
    6. < 0.75 -> write (returns None — caller proceeds to lexical dedup + insert).

Cost model at Ash's scale (~5 L2 writes/day):
    embed: 5 calls/day * $0.00002 = $0.0001/day  (within Gemini free tier)
    haiku tiebreak: rare borderline case, ~1/week, ~$0.001 each

Storage: each embedding (~3072 floats) is persisted inside the L2 row's
content.metadata.embedding. No Supabase schema migration required.

Safety:
    * Module degrades gracefully — any failure returns None (no dedup; caller
      still does lexical dedup and writes).
    * Never raises — observability/optimization, not correctness.
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple


# ── Tuning constants ─────────────────────────────────────────────────────────

EMBEDDING_MODEL = "gemini-embedding-001"
QWEN_EMBEDDING_MODEL = os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4")
_QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
COSINE_DUPLICATE_THRESHOLD = 0.90      # >= this: drop as duplicate
COSINE_BORDERLINE_THRESHOLD = 0.75     # >= this but < duplicate: ask Haiku
HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_MAX_TOKENS = 32                  # tiebreaker only needs yes/no


# ── Lazy clients (one per process) ───────────────────────────────────────────

_GEMINI_CLIENT = None
_ANTHROPIC_CLIENT = None


def _gemini_client():
    """Lazy Gemini client. Returns None if no key OR SDK missing."""
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is not None:
        return _GEMINI_CLIENT
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None
    try:
        from google import genai
        _GEMINI_CLIENT = genai.Client(api_key=key)
        return _GEMINI_CLIENT
    except Exception:
        return None


_QWEN_CLIENT = None


def _qwen_client():
    """Lazy DashScope (OpenAI-compatible) client. Returns None if no key."""
    global _QWEN_CLIENT
    if _QWEN_CLIENT is not None:
        return _QWEN_CLIENT
    key = os.getenv("QWEN_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        _QWEN_CLIENT = OpenAI(api_key=key, base_url=_QWEN_BASE_URL, timeout=30.0)
        return _QWEN_CLIENT
    except Exception:
        return None


def _anthropic_client():
    """Lazy Anthropic client used for the Haiku tiebreaker."""
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is not None:
        return _ANTHROPIC_CLIENT
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        _ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=key)
        return _ANTHROPIC_CLIENT
    except Exception:
        return None


# ── Embedding ────────────────────────────────────────────────────────────────

def _qwen_embed(text: str) -> Optional[List[float]]:
    """DashScope text-embedding-v4 via the OpenAI-compatible endpoint.

    Never raises — returns None on any failure (matches get_embedding contract).
    """
    client = _qwen_client()
    if client is None:
        return None
    try:
        resp = client.embeddings.create(model=QWEN_EMBEDDING_MODEL, input=text)
        return list(resp.data[0].embedding)
    except Exception as e:
        print(f"[Dedup] qwen embed failed (non-fatal): {e}")
        return None


def get_embedding(text: str) -> Optional[List[float]]:
    """Compute an embedding — Qwen (DashScope) when QWEN_API_KEY is set,
    else Gemini. Returns None on any failure.

    Never raises — failure here just means semantic dedup is skipped for this
    write, lexical dedup still runs, fact is still preserved.
    """
    text = (text or "").strip()
    if not text:
        return None
    if os.getenv("QWEN_API_KEY"):
        emb = _qwen_embed(text)
        if emb is not None:
            return emb
    client = _gemini_client()
    if client is None:
        return None
    try:
        r = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
        # SDK shape: r.embeddings is a list (typically len 1 for single content).
        if hasattr(r, "embeddings") and r.embeddings:
            emb = r.embeddings[0]
            vals = emb.values if hasattr(emb, "values") else None
            if vals:
                return list(vals)
        # Fallback shape: r.embedding directly
        if hasattr(r, "embedding"):
            vals = r.embedding.values if hasattr(r.embedding, "values") else r.embedding
            return list(vals) if vals else None
    except Exception as e:
        # Common failure: rate limit, network, malformed input. Never raise.
        print(f"[Dedup] gemini embed failed (non-fatal): {e}")
    return None


# ── Cosine similarity ────────────────────────────────────────────────────────

def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors. Returns 0.0 on dimension mismatch."""
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ── Haiku tiebreaker (only fires on borderline cosine) ──────────────────────

def haiku_tiebreak(new_text: str, existing_text: str) -> Optional[bool]:
    """Ask Claude Haiku whether two facts are duplicates. Returns True/False/None.

    Returns:
        True  — model says duplicate (caller should drop the new fact)
        False — model says distinct  (caller should write the new fact)
        None  — Haiku unavailable or response unparseable (caller should write,
                preferring false-positive insert over false-positive drop)
    """
    client = _anthropic_client()
    if client is None:
        return None
    prompt = (
        "You are a memory deduplication oracle. Decide whether these two "
        "facts express the SAME underlying information about the same person.\n\n"
        f"Fact A: {existing_text[:300]}\n"
        f"Fact B: {new_text[:300]}\n\n"
        "Reply with exactly one word: DUPLICATE or DISTINCT. Be conservative: "
        "if either fact adds non-trivial new information that the other lacks, "
        "they are DISTINCT, not DUPLICATE."
    )
    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=HAIKU_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", "") == "text":
                text = block.text.strip().upper()
                break
        if "DUPLICATE" in text:
            return True
        if "DISTINCT" in text:
            return False
        return None
    except Exception as e:
        print(f"[Dedup] haiku tiebreaker failed (non-fatal): {e}")
        return None


# ── Main entry point ─────────────────────────────────────────────────────────

def find_semantic_duplicate(
    new_text: str,
    candidates: List[Dict],
) -> Optional[Tuple[str, float, str]]:
    """T-080: scan candidates for a semantic duplicate of new_text.

    Args:
        new_text:   the fact being written
        candidates: list of {"id": str, "text": str, "embedding": [floats]}
                    one per existing same-category L2 row that has an embedding

    Returns:
        (existing_id, cosine_score, reason)  on duplicate detection
        None                                 if no duplicate (caller writes)

    Reasons:
        "cosine_high"      — direct cosine >= 0.90, no LLM needed
        "haiku_confirmed"  — borderline cosine, Haiku said DUPLICATE

    Behaviour on tooling failure (no Gemini key, network error, etc.):
        Returns None — semantic dedup is skipped; lexical dedup still runs.
        This is correct: we prefer false-positive insert (extra L2 row) over
        false-positive drop (lose a real fact).
    """
    new_emb = get_embedding(new_text)
    if new_emb is None:
        return None  # graceful degrade

    best_id: Optional[str] = None
    best_text: str = ""
    best_score: float = 0.0

    for cand in candidates:
        emb = cand.get("embedding")
        if not emb:
            continue
        score = cosine_similarity(new_emb, emb)
        if score > best_score:
            best_score = score
            best_id = cand.get("id")
            best_text = cand.get("text") or ""

    if best_id is None:
        return None

    if best_score >= COSINE_DUPLICATE_THRESHOLD:
        return (best_id, best_score, "cosine_high")

    if best_score >= COSINE_BORDERLINE_THRESHOLD:
        # Borderline: defer to Haiku. Only fires when cosine alone is uncertain.
        verdict = haiku_tiebreak(new_text, best_text)
        if verdict is True:
            return (best_id, best_score, "haiku_confirmed")
        # verdict False or None — caller writes.

    return None


# ── Public helper for memory_write to embed the new row before insert ────────

def compute_embedding_for_write(text: str) -> Optional[List[float]]:
    """Public wrapper so tools_memory.memory_write can embed without importing
    private helpers. Just an alias for get_embedding to keep the import surface
    of this module explicit.
    """
    return get_embedding(text)
