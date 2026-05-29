"""memory/audit.py — periodic memory hygiene orchestrator (T-082).

Runs the detection rules from memory.audit_rules, applies the action ladder
(flag → archive → hard-delete), and emits a markdown digest the user can skim.

Threats countered (see T-082 design):
  * False positives → high-importance never archives; archive is reversible;
    hard-delete requires either explicit user confirm OR 90-day archive grace.
  * Notification fatigue → digest is weekly, banner is one line, no mid-session.
  * System rots silently → every run logs to evolution.jsonl AND writes a
    last_audit_at timestamp file the banner reads to warn on staleness.
  * L2 archived but L3 still has derived facts → archive_l2 sweep invalidates
    L3 rows with > 0.6 word overlap in the same category.
  * User ignores digest → default-safe (inertia favors preservation, NEVER
    auto-deletes high-importance content even on neglect).

Entry points
------------
run_audit(memory_tools, dry_run=False) -> AuditRun
    The full audit pass. Called from session.on_exit (cheap rules) and from
    scripts/pi_audit.py (full pass on demand).

should_run_weekly(state_dir) -> bool
    Returns True if the last full audit was > 7 days ago. Used to gate the
    expensive rules + digest generation to once per week.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from memory.audit_rules import (
    AuditFinding,
    run_all_rules,
    rule_stale_low_importance,
    rule_invalidated_aged,
    rule_heuristic_unconfirmed,
    rule_importance_eroded,
    rule_lexical_near_dup,
    _content_text,
    _word_overlap,
    HIGH_IMPORTANCE_FLOOR,
)


# ── State file (last-audit-at marker) ────────────────────────────────────────

def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _audit_state_path() -> str:
    return os.path.join(_project_root(), "data", "audit_state.json")


def load_audit_state() -> Dict:
    """Load {last_run_at, last_full_run_at, ...} from disk, or empty dict."""
    p = _audit_state_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_audit_state(state: Dict) -> None:
    p = _audit_state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, p)


def should_run_weekly(state: Optional[Dict] = None, now: Optional[datetime] = None) -> bool:
    """Run the heavy weekly pass if last_full_run_at > 7 days ago (or never)."""
    state = state if state is not None else load_audit_state()
    last = state.get("last_full_run_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True
    now = now or datetime.now(timezone.utc)
    return (now - last_dt) >= timedelta(days=7)


# ── Audit run result ─────────────────────────────────────────────────────────

@dataclass
class AuditRun:
    """Summary of one audit pass — flagged, archived, deleted, errors."""
    run_at: str
    week_iso: str
    flagged: List[Dict] = field(default_factory=list)
    archived: List[Dict] = field(default_factory=list)
    deleted: List[Dict] = field(default_factory=list)
    merge_suggestions: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> Dict:
        return asdict(self)

    @property
    def total_findings(self) -> int:
        return len(self.flagged) + len(self.archived) + len(self.deleted) + len(self.merge_suggestions)


# ── Action helpers ───────────────────────────────────────────────────────────

def _flag_l2_row(memory_tools, row_id: str, reason: str, rule: str, now_iso: str) -> bool:
    """Set metadata.flagged_at + flag_reason inside content JSONB. Idempotent."""
    try:
        r = (
            memory_tools.supabase.table("organized_memory")
            .select("content")
            .eq("id", row_id)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if not rows:
            return False
        content = rows[0].get("content") or {}
        if not isinstance(content, dict):
            content = {"text": str(content)}
        meta = content.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        # Idempotent: don't overwrite an existing flag (preserves original reason).
        if not meta.get("flagged_at"):
            meta["flagged_at"] = now_iso
            meta["flag_reason"] = reason
            meta["flag_rule"] = rule
            content["metadata"] = meta
            memory_tools.supabase.table("organized_memory").update(
                {"content": content}
            ).eq("id", row_id).execute()
        return True
    except Exception as e:
        print(f"[Audit] flag_l2 error for {row_id[:8]}: {e}")
        return False


def _archive_l2_row(memory_tools, row_id: str) -> bool:
    """Set status='archived' on an L2 row. Reversible via pi_audit restore."""
    try:
        memory_tools.supabase.table("organized_memory").update(
            {"status": "archived"}
        ).eq("id", row_id).execute()
        return True
    except Exception as e:
        print(f"[Audit] archive_l2 error for {row_id[:8]}: {e}")
        return False


def _delete_l3_row(memory_tools, row_id: str) -> bool:
    """Hard-delete an L3 row from both Supabase and SQLite cache."""
    ok = True
    try:
        memory_tools.supabase.table("l3_active_memory").delete().eq("id", row_id).execute()
    except Exception as e:
        print(f"[Audit] delete_l3 supabase error for {row_id[:8]}: {e}")
        ok = False
    try:
        import sqlite3
        conn = sqlite3.connect(memory_tools.sqlite_path)
        conn.execute("DELETE FROM l3_cache WHERE id = ?", [row_id])
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Audit] delete_l3 sqlite error for {row_id[:8]}: {e}")
        ok = False
    return ok


def _invalidate_related_l3(memory_tools, archived_l2_row: Dict, by_l2_id: str) -> int:
    """When archiving an L2 row, invalidate L3 rows derived from it.

    Derivation is heuristic: same category + word overlap > 0.6 with the
    archived L2's text. Returns count of L3 rows invalidated.
    """
    category = archived_l2_row.get("category")
    text = _content_text(archived_l2_row)
    if not category or not text:
        return 0

    count = 0
    try:
        r = (
            memory_tools.supabase.table("l3_active_memory")
            .select("id,content,category")
            .eq("category", category)
            .limit(200)
            .execute()
        )
        l3_rows = r.data or []
    except Exception:
        return 0

    for l3 in l3_rows:
        l3_text = (l3.get("content") or "").strip()
        if not l3_text:
            continue
        if _word_overlap(text, l3_text) >= 0.6:
            try:
                memory_tools._invalidate_l3_entry(l3["id"], by_entry_id=by_l2_id)
                count += 1
            except Exception:
                continue
    return count


# ── Main entry point ─────────────────────────────────────────────────────────

def run_audit(memory_tools, dry_run: bool = False,
              now: Optional[datetime] = None) -> AuditRun:
    """Run the full audit pass: detect → act → record.

    Args:
        memory_tools: MemoryTools instance (used for Supabase reads/writes)
        dry_run:      if True, detect only; no archiving, flagging, or deletion
        now:          override "now" for deterministic tests

    Returns AuditRun summary. Errors are captured in run.errors; nothing raises.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    week_iso = now.strftime("%Y-W%W")

    run = AuditRun(run_at=now_iso, week_iso=week_iso, dry_run=dry_run)

    # ── Fetch ────────────────────────────────────────────────────────────────
    l2_rows: List[Dict] = []
    l3_rows: List[Dict] = []
    try:
        r2 = (
            memory_tools.supabase.table("organized_memory")
            .select("id,category,title,content,importance,status,created_at")
            .limit(2000)
            .execute()
        )
        l2_rows = r2.data or []
    except Exception as e:
        run.errors.append(f"l2 fetch: {e}")
    try:
        r3 = (
            memory_tools.supabase.table("l3_active_memory")
            .select("id,category,content,importance,active_until,created_at,metadata")
            .limit(2000)
            .execute()
        )
        l3_rows = r3.data or []
    except Exception as e:
        run.errors.append(f"l3 fetch: {e}")

    # ── Detect ───────────────────────────────────────────────────────────────
    findings = run_all_rules(l2_rows, l3_rows, now=now)

    # Build an id → row index for action helpers
    l2_by_id = {r["id"]: r for r in l2_rows if "id" in r}

    # ── Act ──────────────────────────────────────────────────────────────────
    for f in findings:
        rec = {
            "rule": f.rule,
            "recommendation": f.recommendation,
            "target_ids": f.target_ids,
            "target_tier": f.target_tier,
            "summary": f.summary,
            "detail": f.detail,
        }
        if dry_run:
            # In dry-run, classify but never mutate
            if f.recommendation == "archive":
                run.archived.append(rec)
            elif f.recommendation == "delete":
                run.deleted.append(rec)
            elif f.recommendation == "merge":
                run.merge_suggestions.append(rec)
            else:
                run.flagged.append(rec)
            continue

        if f.recommendation == "archive" and f.target_tier == "l2":
            target_id = f.target_ids[0]
            row = l2_by_id.get(target_id)
            if row is None:
                run.errors.append(f"archive: row {target_id[:8]} missing")
                continue
            # Sacred-importance guard: never auto-archive importance >= 7.
            if int(row.get("importance") or 0) >= HIGH_IMPORTANCE_FLOOR:
                rec["recommendation"] = "flag"
                rec["downgraded_reason"] = "high_importance"
                run.flagged.append(rec)
                continue
            if _archive_l2_row(memory_tools, target_id):
                related = _invalidate_related_l3(memory_tools, row, target_id)
                rec["related_l3_invalidated"] = related
                run.archived.append(rec)

        elif f.recommendation == "delete" and f.target_tier == "l3":
            if _delete_l3_row(memory_tools, f.target_ids[0]):
                run.deleted.append(rec)

        elif f.recommendation == "merge":
            # Surface as suggestion only; merges require explicit user action.
            _flag_l2_row(memory_tools, f.target_ids[1], f.summary, f.rule, now_iso)
            run.merge_suggestions.append(rec)

        else:  # "flag" or any unknown recommendation
            if f.target_tier == "l2":
                _flag_l2_row(memory_tools, f.target_ids[0], f.summary, f.rule, now_iso)
            run.flagged.append(rec)

    # ── Record run ───────────────────────────────────────────────────────────
    if not dry_run:
        state = load_audit_state()
        state["last_run_at"] = now_iso
        state["last_full_run_at"] = now_iso  # any run_audit() is the heavy pass
        state["last_run_flagged"] = len(run.flagged)
        state["last_run_archived"] = len(run.archived)
        state["last_run_deleted"] = len(run.deleted)
        state["last_run_merge_suggestions"] = len(run.merge_suggestions)
        state["last_run_errors"] = len(run.errors)
        try:
            save_audit_state(state)
        except Exception as e:
            run.errors.append(f"save_state: {e}")

    return run


# ── Banner helpers ───────────────────────────────────────────────────────────

def audit_banner_line(now: Optional[datetime] = None) -> str:
    """One-line banner for session start. Empty string if no audit ever ran."""
    state = load_audit_state()
    if not state.get("last_run_at"):
        return ""
    now = now or datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(state["last_run_at"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    age_days = (now - last).days
    flagged = state.get("last_run_flagged", 0)
    archived = state.get("last_run_archived", 0)
    merges = state.get("last_run_merge_suggestions", 0)

    if age_days > 10:
        return f"audit STALE ({age_days}d ago) — run `python scripts/pi_audit.py digest`"
    if flagged == 0 and archived == 0 and merges == 0:
        return ""  # quiet weeks stay quiet
    bits = []
    if flagged:
        bits.append(f"{flagged} flagged")
    if archived:
        bits.append(f"{archived} archived")
    if merges:
        bits.append(f"{merges} merge")
    return "audit: " + ", ".join(bits) + " (this week)"
