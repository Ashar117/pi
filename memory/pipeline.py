"""
memory/pipeline.py — L1 -> L2 distillation.

Reads the raw L1 (raw_wiki) thread for the just-completed session,
asks Groq to extract durable facts, and writes curated entries to L2
(organized_memory).

Runs once per session at EXIT time — never during live turns.
Uses Groq (free) so distillation costs nothing.
"""

import json
from typing import List, Dict, Optional


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a memory curator for an AI assistant called Pi. "
    "Your job is to read a conversation log and extract a concise list of "
    "facts that Pi should remember long-term. "
    "Only include facts that are genuinely worth keeping: preferences, "
    "decisions, important context, personal details, project milestones. "
    "Skip greetings, filler, and anything already obvious. "
    "Return ONLY a JSON array — no prose, no markdown fences. "
    'Each element: {"fact": "...", "category": "...", "importance": 1-10}. '
    "Categories: permanent_profile | active_project | current_priority | "
    "research_results | session_history | note. "
    "Minimum importance to include: 4. Maximum items: 12."
)


# ── Public entry point ────────────────────────────────────────────────────────

def distill_session(
    *,
    thread_id: str,
    session_id: str,
    memory_tools,          # MemoryTools instance
    groq_client,           # groq.Groq instance
    model: str = "llama-3.3-70b-versatile",
    dry_run: bool = False,
) -> Dict:
    """Read the L1 thread for ``thread_id``, distill facts, write them to L2.

    Args:
        thread_id:    UUID of the session's L1 thread in raw_wiki.
        session_id:   8-char hex — attached to every L2 write for provenance.
        memory_tools: Initialised MemoryTools for Supabase reads/writes.
        groq_client:  Initialised groq.Groq for the LLM call.
        model:        Groq model to use (default: llama-3.3-70b-versatile).
        dry_run:      If True, return extracted facts without writing to L2.

    Returns:
        {"distilled": N, "skipped": M, "facts": [...]}
    """
    rows = memory_tools.get_l1_thread(thread_id)
    if not rows:
        print(f"[Distill] No L1 rows for thread {thread_id[:8]}..., skipping")
        return {"distilled": 0, "skipped": 0, "facts": []}

    conversation = _format_conversation(rows)
    if not conversation.strip():
        return {"distilled": 0, "skipped": 0, "facts": []}

    facts = _extract_facts(conversation, groq_client, model)
    if not facts:
        return {"distilled": 0, "skipped": 0, "facts": []}

    distilled = 0
    skipped = 0

    for fact in facts:
        text = (fact.get("fact") or "").strip()
        category = (fact.get("category") or "note").strip()
        importance = int(fact.get("importance") or 4)

        if not text or importance < 4:
            skipped += 1
            continue

        if not dry_run:
            memory_tools.memory_write(
                content=text,
                tier="l2",
                importance=importance,
                category=category,
                session_id=session_id,
            )
        distilled += 1

    print(f"[Distill] {distilled} facts written to L2, {skipped} skipped")
    return {"distilled": distilled, "skipped": skipped, "facts": facts}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_conversation(rows: List[Dict]) -> str:
    """Build a readable transcript from L1 rows (user + assistant only)."""
    lines = []
    for row in rows:
        role = row.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = (row.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Pi"
        lines.append(f"{label}: {content[:600]}")
    return "\n".join(lines)


def _extract_facts(
    conversation: str,
    groq_client,
    model: str,
) -> List[Dict]:
    """Call Groq and parse the JSON fact array. Returns [] on any failure."""
    try:
        resp = groq_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": conversation},
            ],
            max_tokens=800,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences if the model added them anyway
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        facts = json.loads(raw)
        if not isinstance(facts, list):
            return []
        return facts
    except Exception as e:
        print(f"[Distill] Groq extraction failed: {e}")
        return []
