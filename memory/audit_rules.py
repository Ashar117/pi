"""memory/audit_rules.py — pure detection functions for the memory audit (T-082).

Each rule is a pure function: given a list of L2 / L3 rows, return a list of
AuditFinding dicts. Rules have no side effects — they only DETECT candidates.
The orchestrator in memory/audit.py decides what to do with the findings.

Tuning: thresholds are module-level constants so they can be adjusted in one
place. If a rule fires too often or too rarely, change the constant here.

Action ladder (decided by audit.py, not by rules):
    FLAG     — surfaced in digest; reversible; no state change to query-visibility
    ARCHIVE  — status='archived'; hidden from default queries; reversible
    DELETE   — hard DELETE from Supabase; irreversible

Rules below ONLY return findings; they never mutate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional


# ── Tuning constants ─────────────────────────────────────────────────────────

STALE_AGE_DAYS = 60                 # rule_stale: L2 considered stale after N days
STALE_LAST_ACCESS_DAYS = 30         # AND last access older than M days
STALE_IMPORTANCE_MAX = 6            # AND importance below this

HEURISTIC_GRACE_DAYS = 14           # rule_heuristic_unconfirmed: grace before flagging
INVALIDATED_GRACE_DAYS = 180        # rule_invalidated_aged: hard-delete after grace
IMPORTANCE_ERODED_DAYS = 90         # rule_importance_eroded: high-imp + no access window
HIGH_IMPORTANCE_FLOOR = 7           # facts at/above this never auto-archive

NEAR_DUP_OVERLAP = 0.60             # rule_lexical_near_dup: word overlap threshold
NEAR_DUP_MAX_PAIRS = 20             # cap surfaced pairs per audit run


# ── Finding type ─────────────────────────────────────────────────────────────

@dataclass
class AuditFinding:
    """A single candidate surfaced by a rule. Consumed by audit.py.

    rule:           identifier of the rule that fired (e.g. "stale")
    recommendation: "archive" | "delete" | "flag" | "merge"
    target_ids:     row id(s) the finding refers to (>=1)
    target_tier:    "l2" | "l3"
    summary:        one-line human-readable description (goes into digest)
    detail:         optional dict with extra context (importance, age, etc.)
    """
    rule: str
    recommendation: str
    target_ids: List[str]
    target_tier: str
    summary: str
    detail: Dict = field(default_factory=dict)


# ── Helpers ──────────────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might can shall must of in on at to for "
    "with from by about into through during before after between i you he "
    "she it we they this that these those and or but not".split()
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO-8601 parse. Returns None for null/empty/malformed."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _content_text(row: Dict) -> str:
    """Extract the fact body from an L2 row. content is JSONB {text, metadata}."""
    c = row.get("content") or {}
    if isinstance(c, dict):
        return (c.get("text") or "").strip()
    return str(c).strip()


def _content_metadata(row: Dict) -> Dict:
    """Extract the metadata dict from an L2 row's content JSONB."""
    c = row.get("content") or {}
    if isinstance(c, dict):
        md = c.get("metadata")
        if isinstance(md, dict):
            return md
    return {}


def _l3_metadata(row: Dict) -> Dict:
    """Extract the metadata dict from an L3 row (top-level metadata column)."""
    md = row.get("metadata")
    return md if isinstance(md, dict) else {}


def _word_overlap(a: str, b: str) -> float:
    """Jaccard-style overlap on stopword-stripped tokens. 0 on empty input."""
    wa = {w for w in _WORD_RE.findall(a.lower()) if w not in _STOPWORDS and len(w) > 1}
    wb = {w for w in _WORD_RE.findall(b.lower()) if w not in _STOPWORDS and len(w) > 1}
    denom = min(len(wa), len(wb))
    return len(wa & wb) / denom if denom else 0.0


def _age_days(row: Dict, now: datetime) -> Optional[float]:
    """Age in days from row.created_at. None if unparseable."""
    created = _parse_iso(row.get("created_at"))
    if created is None:
        return None
    return (now - created).total_seconds() / 86400


def _last_accessed(row: Dict, tier: str) -> Optional[datetime]:
    """Last access timestamp from metadata (set by tools_memory access tracking)."""
    meta = _content_metadata(row) if tier == "l2" else _l3_metadata(row)
    return _parse_iso(meta.get("last_accessed_at"))


def _access_count(row: Dict, tier: str) -> int:
    meta = _content_metadata(row) if tier == "l2" else _l3_metadata(row)
    try:
        return int(meta.get("access_count") or 0)
    except (TypeError, ValueError):
        return 0


# ── Detection rules ──────────────────────────────────────────────────────────

def rule_stale_low_importance(l2_rows: Iterable[Dict], now: Optional[datetime] = None) -> List[AuditFinding]:
    """L2 rows older than 60d, importance < 6, never accessed in 30d → archive.

    Default-safe: high-importance (>=7) never reaches this rule. The 60d / 30d
    / importance-6 thresholds match prune_l2_stale (T-073) so this rule is the
    detection front-half of the same policy.
    """
    now = now or _now_utc()
    findings: List[AuditFinding] = []
    last_access_cutoff = now - timedelta(days=STALE_LAST_ACCESS_DAYS)

    for row in l2_rows:
        if row.get("status") != "active":
            continue
        importance = int(row.get("importance") or 0)
        if importance >= STALE_IMPORTANCE_MAX:
            continue
        age = _age_days(row, now)
        if age is None or age < STALE_AGE_DAYS:
            continue
        la = _last_accessed(row, "l2")
        if la is not None and la > last_access_cutoff:
            continue  # accessed recently — keep
        findings.append(AuditFinding(
            rule="stale",
            recommendation="archive",
            target_ids=[row["id"]],
            target_tier="l2",
            summary=f"[stale] {row.get('category','?')}/{_content_text(row)[:80]}",
            detail={"age_days": round(age, 1), "importance": importance,
                    "category": row.get("category")},
        ))
    return findings


def rule_heuristic_unconfirmed(l2_rows: Iterable[Dict], now: Optional[datetime] = None) -> List[AuditFinding]:
    """L2 rows extracted by the T-071 regex heuristic, > 14d old, never accessed.

    Heuristic-derived facts are lower confidence than LLM-extracted ones. If the
    user never accessed them and they're past a grace window, surface for review.
    """
    now = now or _now_utc()
    findings: List[AuditFinding] = []

    for row in l2_rows:
        if row.get("status") != "active":
            continue
        meta = _content_metadata(row)
        source = (meta.get("source") or "").lower()
        if "heuristic" not in source:
            continue
        age = _age_days(row, now)
        if age is None or age < HEURISTIC_GRACE_DAYS:
            continue
        if _access_count(row, "l2") > 0:
            continue
        findings.append(AuditFinding(
            rule="heuristic_unconfirmed",
            recommendation="flag",
            target_ids=[row["id"]],
            target_tier="l2",
            summary=f"[heuristic] {row.get('category','?')}: {_content_text(row)[:80]}",
            detail={"age_days": round(age, 1), "source": source,
                    "importance": int(row.get("importance") or 0)},
        ))
    return findings


def rule_invalidated_aged(l3_rows: Iterable[Dict], now: Optional[datetime] = None) -> List[AuditFinding]:
    """L3 rows with invalid_at set, invalidated > 180d ago → hard-delete.

    These were superseded and have been hidden from default queries for the
    grace period. Safe to actually remove. If the user wanted historical
    access, they've had 180 days to use the include_invalidated flag.
    """
    now = now or _now_utc()
    findings: List[AuditFinding] = []
    cutoff = now - timedelta(days=INVALIDATED_GRACE_DAYS)

    for row in l3_rows:
        meta = _l3_metadata(row)
        invalid_at = _parse_iso(meta.get("invalid_at"))
        if invalid_at is None or invalid_at > cutoff:
            continue
        findings.append(AuditFinding(
            rule="invalidated_aged",
            recommendation="delete",
            target_ids=[row["id"]],
            target_tier="l3",
            summary=f"[invalidated>180d] {row.get('category','?')}: {(row.get('content') or '')[:80]}",
            detail={"invalidated_days_ago": (now - invalid_at).days},
        ))
    return findings


def rule_lexical_near_dup(l2_rows: Iterable[Dict], now: Optional[datetime] = None) -> List[AuditFinding]:
    """Pairs of L2 rows in same category with > 0.6 word overlap, both active.

    Recommends merge: keep the higher-importance row, archive the lower one.
    Capped at NEAR_DUP_MAX_PAIRS per audit run to avoid digest bloat.
    """
    findings: List[AuditFinding] = []
    by_cat: Dict[str, List[Dict]] = {}
    for row in l2_rows:
        if row.get("status") != "active":
            continue
        by_cat.setdefault(row.get("category") or "uncategorised", []).append(row)

    pairs = 0
    for cat, rows in by_cat.items():
        # O(n²) within category — fine for <1000 facts/category in practice.
        for i in range(len(rows)):
            if pairs >= NEAR_DUP_MAX_PAIRS:
                return findings
            text_i = _content_text(rows[i])
            for j in range(i + 1, len(rows)):
                if pairs >= NEAR_DUP_MAX_PAIRS:
                    return findings
                text_j = _content_text(rows[j])
                overlap = _word_overlap(text_i, text_j)
                if overlap < NEAR_DUP_OVERLAP:
                    continue
                # Recommend keeping the higher-importance row.
                imp_i = int(rows[i].get("importance") or 0)
                imp_j = int(rows[j].get("importance") or 0)
                keep, drop = (rows[i], rows[j]) if imp_i >= imp_j else (rows[j], rows[i])
                findings.append(AuditFinding(
                    rule="near_dup",
                    recommendation="merge",
                    target_ids=[keep["id"], drop["id"]],
                    target_tier="l2",
                    summary=(
                        f"[near-dup {overlap:.0%}] {cat}: keep '{_content_text(keep)[:50]}' "
                        f"merge '{_content_text(drop)[:50]}'"
                    ),
                    detail={"overlap": round(overlap, 3), "category": cat,
                            "keep_importance": max(imp_i, imp_j),
                            "drop_importance": min(imp_i, imp_j)},
                ))
                pairs += 1
    return findings


def rule_importance_eroded(l2_rows: Iterable[Dict], now: Optional[datetime] = None) -> List[AuditFinding]:
    """High-importance L2 (>=7) never accessed in 90 days → FLAG only.

    These never auto-archive (importance is sacred). The rule exists so the
    user is reminded that something they once marked important is sitting
    unused. They may want to demote it OR keep it.
    """
    now = now or _now_utc()
    findings: List[AuditFinding] = []
    cutoff = now - timedelta(days=IMPORTANCE_ERODED_DAYS)

    for row in l2_rows:
        if row.get("status") != "active":
            continue
        importance = int(row.get("importance") or 0)
        if importance < HIGH_IMPORTANCE_FLOOR:
            continue
        age = _age_days(row, now)
        if age is None or age < IMPORTANCE_ERODED_DAYS:
            continue
        la = _last_accessed(row, "l2")
        if la is not None and la > cutoff:
            continue
        findings.append(AuditFinding(
            rule="importance_eroded",
            recommendation="flag",
            target_ids=[row["id"]],
            target_tier="l2",
            summary=f"[eroded] {row.get('category','?')}: {_content_text(row)[:80]} (imp={importance})",
            detail={"importance": importance, "age_days": round(age, 1)},
        ))
    return findings


# ── Aggregator ───────────────────────────────────────────────────────────────

def run_all_rules(l2_rows: List[Dict], l3_rows: List[Dict],
                  now: Optional[datetime] = None) -> List[AuditFinding]:
    """Run every rule once and return the combined finding list.

    Order matches the audit digest ordering: archives first (most severe),
    then deletes, then flags, then merges.
    """
    findings: List[AuditFinding] = []
    findings.extend(rule_stale_low_importance(l2_rows, now))
    findings.extend(rule_invalidated_aged(l3_rows, now))
    findings.extend(rule_heuristic_unconfirmed(l2_rows, now))
    findings.extend(rule_importance_eroded(l2_rows, now))
    findings.extend(rule_lexical_near_dup(l2_rows, now))
    return findings
