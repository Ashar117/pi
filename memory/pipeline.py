"""
memory/pipeline.py — L1 -> L2 distillation.

Reads the raw L1 (raw_wiki) thread for the just-completed session,
asks an LLM to extract durable facts, and writes curated entries to L2
(organized_memory).

Runs at session EXIT and (T-064) mid-session every N turns.

Provider fallback chain (T-071):
  1. Groq llama-3.3-70b — free, primary
  2. Claude Haiku 4.5  — cheap fallback when Groq is rate-limited (429 TPD)
  3. Regex heuristic   — last-resort extractor when both LLMs are unavailable

The heuristic guarantees that explicit "remember/save/note" statements
and "my name is / I prefer / I live in" patterns never get lost just
because both LLM tiers are down.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent
_DROPPED_LOG = _ROOT / "logs" / "dropped_turns.jsonl"


def _load_dropped_turns(session_id: str) -> List[Dict]:
    """T-090: read log_turn entries from logs/dropped_turns.jsonl for this session.

    Returns a list of synthetic L1 rows in the same shape as get_l1_thread() output.
    Entries are matched by session_id and deduped against Supabase rows by turn_number.
    """
    if not _DROPPED_LOG.exists():
        return []
    rows = []
    try:
        for line in _DROPPED_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("session_id") != session_id:
                continue
            # Reconstruct as synthetic L1 row matching get_l1_thread() shape
            rows.append({
                "role": "user",
                "content": rec.get("user_content", ""),
                "turn_number": rec.get("turn_number", 0),
                "session_id": session_id,
                "metadata": {"dropped_fallback": True},
            })
            rows.append({
                "role": "assistant",
                "content": rec.get("assistant_content", ""),
                "turn_number": rec.get("turn_number", 0),
                "session_id": session_id,
                "metadata": {"dropped_fallback": True},
            })
    except Exception:
        pass
    return rows


def _drain_dropped_turns(session_id: str, memory_tools) -> int:
    """T-090: replay successfully-recovered dropped entries back to Supabase.

    Reads dropped_turns.jsonl, calls memory_tools.log_turn for entries matching
    session_id, and removes successfully-replayed lines from the file.
    Returns count of entries replayed.
    """
    if not _DROPPED_LOG.exists():
        return 0
    replayed = 0
    kept_lines = []
    try:
        for line in _DROPPED_LOG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                kept_lines.append(line)
                continue
            if rec.get("session_id") != session_id:
                kept_lines.append(line)
                continue
            try:
                memory_tools.log_turn(
                    thread_id=rec["thread_id"],
                    session_id=rec["session_id"],
                    turn_number=rec["turn_number"],
                    user_content=rec.get("user_content", ""),
                    assistant_content=rec.get("assistant_content", ""),
                    mode=rec.get("mode", "root"),
                )
                replayed += 1
            except Exception:
                kept_lines.append(line)
        # Rewrite with only un-replayed lines
        if not kept_lines:
            _DROPPED_LOG.unlink(missing_ok=True)
        else:
            _DROPPED_LOG.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    except Exception:
        pass
    return replayed


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a memory curator for an AI assistant called Pi. "
    "Your job is to read a conversation log and extract a concise list of "
    "facts that Pi should remember long-term. "
    "INCLUDE ONLY: stable identity facts (where someone lives/works/studies), "
    "explicit preferences ('I prefer X'), firm commitments and deadlines, "
    "things the user explicitly asked to remember, named project milestones. "
    "SKIP: greetings, small talk, hypotheticals ('might', 'maybe', 'thinking about'), "
    "tentative plans, facts already obvious from context, Pi's own responses, "
    "restatements of things already stored, session mechanics. "
    "Return ONLY a JSON array — no prose, no markdown fences. "
    'Each element: {"fact": "...", "category": "...", "importance": 1-10}. '
    "Categories: permanent_profile | active_project | current_priority | "
    "research_results | session_history | note. "
    "Minimum importance to include: 6. Maximum items: 8."
)


# ── Public entry point ────────────────────────────────────────────────────────

def distill_session(
    *,
    thread_id: str,
    session_id: str,
    memory_tools,                       # MemoryTools instance
    router=None,                        # T-084: preferred — LLMRouter, tier='cheap'
    groq_client=None,                   # legacy / fallback when router unavailable
    model: str = "llama-3.3-70b-versatile",
    dry_run: bool = False,
    anthropic_client=None,              # T-071 legacy Claude fallback (router supersedes)
    rows: Optional[List[Dict]] = None,  # T-072: pre-fetched subset (mid-session)
) -> Dict:
    """Read the L1 thread for ``thread_id``, distill facts, write them to L2.

    Args:
        thread_id:    UUID of the session's L1 thread in raw_wiki.
        session_id:   8-char hex — attached to every L2 write for provenance.
        memory_tools: Initialised MemoryTools for Supabase reads/writes.
        router:       T-084 preferred. LLMRouter; we call .chat(tier='cheap')
                      which routes Cerebras → Groq → Gemini → OpenRouter
                      with TPD-budget-aware brownout. When provided, the
                      legacy groq_client/anthropic_client args are ignored
                      and the heuristic stays as last-resort fallback.
        groq_client:  Legacy direct groq.Groq instance. Used only when
                      router is None. Deprecated; routes that still pass
                      this should migrate to passing router=agent.router.
        model:        Groq model name — passed through when using router so
                      cost tracker attributes correctly. Default
                      llama-3.3-70b-versatile.
        dry_run:      If True, return extracted facts without writing to L2.

    Returns:
        {"distilled": N, "skipped": M, "facts": [...]}
    """
    # T-072: caller may pre-filter rows (mid-session: only un-distilled rows).
    if rows is None:
        rows = memory_tools.get_l1_thread(thread_id)

    # T-090: merge any dropped log_turn entries recovered from local fallback.
    dropped = _load_dropped_turns(session_id)
    if dropped:
        existing_turns = {r.get("turn_number") for r in rows}
        for dr in dropped:
            if dr.get("turn_number") not in existing_turns:
                rows.append(dr)
        rows.sort(key=lambda r: (r.get("turn_number", 0), r.get("role", "")))
        print(f"[Distill] Merged {len(dropped)//2} dropped turn(s) from local fallback")

    if not rows:
        print(f"[Distill] No L1 rows for thread {thread_id[:8]}..., skipping")
        return {"distilled": 0, "skipped": 0, "facts": []}

    conversation = _format_conversation(rows)
    if not conversation.strip():
        return {"distilled": 0, "skipped": 0, "facts": []}

    # T-084: router path (preferred) — cheap tier picks Cerebras first,
    # falls back through Groq → Gemini → OpenRouter on failure or TPD
    # brownout. One failover code path. Heuristic stays as final resort.
    if router is not None:
        facts = _extract_facts_router(conversation, router)
        source = "router_cheap" if facts else "heuristic"
        if not facts:
            facts = _extract_facts_heuristic(conversation)
    else:
        # Legacy path: Groq → Haiku → heuristic. Kept for callers not yet
        # passing router=; deprecated.
        facts = _extract_facts(conversation, groq_client, model) if groq_client else []
        source = "groq" if facts else "heuristic"
        if not facts and anthropic_client is not None:
            facts = _extract_facts_claude(conversation, anthropic_client)
            source = "claude_haiku" if facts else source
        if not facts:
            facts = _extract_facts_heuristic(conversation)
            source = "heuristic" if facts else source

    if not facts:
        return {"distilled": 0, "skipped": 0, "facts": []}
    print(f"[Distill] facts source: {source} ({len(facts)} candidates)")

    distilled = 0
    skipped = 0
    skip_reasons: dict = {"low_importance": 0, "duplicate": 0, "empty": 0}

    for fact in facts:
        text = (fact.get("fact") or "").strip()
        category = (fact.get("category") or "note").strip()
        importance = int(fact.get("importance") or 0)

        if not text:
            skipped += 1
            skip_reasons["empty"] += 1
            continue

        if importance < 6:
            skipped += 1
            skip_reasons["low_importance"] += 1
            continue

        if not dry_run:
            dup_id = memory_tools._is_l2_duplicate(text, category)
            if dup_id:
                skipped += 1
                skip_reasons["duplicate"] += 1
                continue
            # T-082 audit-bug-3: pass the distillation source so the audit
            # system can later flag heuristic-extracted (low-confidence) facts.
            memory_tools.memory_write(
                content=text,
                tier="l2",
                importance=importance,
                category=category,
                session_id=session_id,
                source=f"distill_{source}",
            )
        distilled += 1

    reason_str = ", ".join(f"{v} {k}" for k, v in skip_reasons.items() if v)
    print(
        f"[Distill] {distilled} facts written to L2, {skipped} skipped"
        + (f" ({reason_str})" if reason_str else "")
    )
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
        facts = _parse_facts_json(raw)
        return facts
    except Exception as e:
        print(f"[Distill] Groq extraction failed: {e}")
        return []


def _extract_facts_claude(conversation: str, anthropic_client) -> List[Dict]:
    """T-071: Claude Haiku fallback when Groq is rate-limited.

    Cost: ~$0.001 per session distill at typical 4k-token transcript.
    Returns [] on any failure so the heuristic fallback can take over.
    """
    try:
        resp = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=_SYSTEM,
            messages=[{"role": "user", "content": conversation}],
        )
        # Extract text from the first text content block
        raw = ""
        for block in resp.content:
            if getattr(block, "type", "") == "text":
                raw = block.text.strip()
                break
        if not raw:
            return []
        facts = _parse_facts_json(raw)
        return facts
    except Exception as e:
        print(f"[Distill] Claude Haiku fallback failed: {e}")
        return []


def _extract_facts_router(conversation: str, router) -> List[Dict]:
    """T-084: distillation via LLMRouter with tier='cheap'.

    Router handles failover (Cerebras → Groq → Gemini → OpenRouter) and
    TPD-budget brownout internally; we only need to catch the all-providers-
    failed RuntimeError and return [] so the heuristic takes over.
    """
    try:
        resp = router.chat(
            messages=[{"role": "user", "content": conversation}],
            system=_SYSTEM,
            tools=[],
            max_tokens=800,
            tier="cheap",
        )
        raw = (resp.text or "").strip()
        if not raw:
            return []
        return _parse_facts_json(raw)
    except Exception as e:
        print(f"[Distill] router extraction failed: {e}")
        return []


# ── Heuristic extractor (last-resort) ─────────────────────────────────────────

# Patterns that strongly imply a durable fact about the user
_HEURISTIC_PATTERNS = [
    # Explicit memory commands
    (re.compile(r"(?:remember|save|note|store)(?:\s+this)?(?:\s*[:,-])?\s+(.{8,200}?)(?:\.|$|\n)", re.IGNORECASE),
     "note", 7),
    # Identity statements
    (re.compile(r"\b(?:my name is|i'?m|i am)\s+([A-Z][a-zA-Z]{2,30}(?:\s+[A-Z][a-zA-Z]+)?)\b"),
     "permanent_profile", 9),
    # Location
    (re.compile(r"\b(?:i live in|i'?m (?:from|in|at)|based in)\s+([A-Z][a-zA-Z\s,]{2,40})", re.IGNORECASE),
     "permanent_profile", 8),
    # Preferences
    (re.compile(r"\b(?:i prefer|i like|i love|i hate|i don'?t like)\s+([^.\n]{5,100})", re.IGNORECASE),
     "preferences", 7),
    # Deadlines / dates
    (re.compile(r"\b(?:deadline|due|by)\s+(?:on\s+)?([A-Z][a-z]+\s+\d{1,2}(?:,\s*\d{4})?|\d{4}-\d{2}-\d{2})", re.IGNORECASE),
     "current_priority", 8),
]


def _extract_facts_heuristic(conversation: str) -> List[Dict]:
    """T-071: Regex fallback when both Groq AND Claude are unavailable.

    Looks at USER turns only (Pi turns are answers, not facts to remember).
    Catches the most common "remember X" / identity / preference patterns.
    Quality is lower than LLM extraction but guarantees zero data loss for
    explicit instructions.
    """
    facts: List[Dict] = []
    seen: set = set()

    # Iterate user lines only — they start with "User: "
    for line in conversation.split("\n"):
        if not line.startswith("User: "):
            continue
        text = line[len("User: "):].strip()
        if len(text) < 8:
            continue

        for pattern, category, importance in _HEURISTIC_PATTERNS:
            for match in pattern.finditer(text):
                # Use the captured group as the fact body when present;
                # otherwise the whole match (trimmed).
                captured = match.group(1).strip() if match.groups() else match.group(0).strip()
                fact_text = captured.rstrip(".,;:")
                if len(fact_text) < 5 or len(fact_text) > 300:
                    continue
                key = fact_text.lower()[:60]
                if key in seen:
                    continue
                seen.add(key)
                facts.append({
                    "fact": fact_text,
                    "category": category,
                    "importance": importance,
                })
                if len(facts) >= 8:
                    return facts
    return facts


def _parse_facts_json(raw: str) -> List[Dict]:
    """Strip markdown fences and parse a JSON array. Returns [] on failure."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        facts = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    return facts if isinstance(facts, list) else []
