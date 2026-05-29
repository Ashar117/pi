"""testing/test_audit.py — golden tests for the memory audit system (T-082).

Covers detection rules (memory/audit_rules.py) and the orchestrator's action
ladder (memory/audit.py). No Supabase round-trips — Supabase is mocked.

If a rule changes, the corresponding test here is the canonical contract.
"""
from __future__ import annotations

import os
import json
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from memory.audit_rules import (
    rule_stale_low_importance,
    rule_heuristic_unconfirmed,
    rule_invalidated_aged,
    rule_lexical_near_dup,
    rule_importance_eroded,
    run_all_rules,
    STALE_AGE_DAYS,
    HEURISTIC_GRACE_DAYS,
    INVALIDATED_GRACE_DAYS,
    HIGH_IMPORTANCE_FLOOR,
)


NOW = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _l2_row(text: str, *, category: str = "preferences", importance: int = 5,
            age_days: int = 0, status: str = "active",
            source: str = "stated", access_count: int = 0,
            last_accessed_days_ago: int | None = None,
            row_id: str | None = None) -> dict:
    """Build a synthetic L2 row matching the production schema."""
    created = NOW - timedelta(days=age_days)
    meta = {"source": source, "access_count": access_count}
    if last_accessed_days_ago is not None:
        meta["last_accessed_at"] = _iso(NOW - timedelta(days=last_accessed_days_ago))
    return {
        "id": row_id or str(uuid.uuid4()),
        "category": category,
        "title": text[:100],
        "content": {"text": text, "metadata": meta},
        "importance": importance,
        "status": status,
        "created_at": _iso(created),
    }


def _l3_row(content: str, *, category: str = "preferences",
            invalid_at_days_ago: int | None = None,
            row_id: str | None = None) -> dict:
    meta = {}
    if invalid_at_days_ago is not None:
        meta["invalid_at"] = _iso(NOW - timedelta(days=invalid_at_days_ago))
    return {
        "id": row_id or str(uuid.uuid4()),
        "category": category,
        "content": content,
        "importance": 5,
        "active_until": None,
        "created_at": _iso(NOW - timedelta(days=30)),
        "metadata": meta,
    }


# ── rule_stale_low_importance ────────────────────────────────────────────────

def test_stale_low_importance_archives_old_low_imp_unaccessed():
    rows = [
        _l2_row("old low-imp fact", age_days=STALE_AGE_DAYS + 5, importance=4),
    ]
    f = rule_stale_low_importance(rows, now=NOW)
    assert len(f) == 1
    assert f[0].recommendation == "archive"
    assert f[0].rule == "stale"


def test_stale_skips_recent_rows():
    rows = [_l2_row("recent", age_days=10, importance=4)]
    assert rule_stale_low_importance(rows, now=NOW) == []


def test_stale_skips_high_importance():
    # Importance >= STALE_IMPORTANCE_MAX (6) should not be flagged stale.
    rows = [_l2_row("old but important", age_days=STALE_AGE_DAYS + 5, importance=8)]
    assert rule_stale_low_importance(rows, now=NOW) == []


def test_stale_skips_recently_accessed():
    rows = [_l2_row("accessed", age_days=STALE_AGE_DAYS + 5, importance=4,
                    last_accessed_days_ago=5)]
    assert rule_stale_low_importance(rows, now=NOW) == []


def test_stale_skips_archived():
    rows = [_l2_row("dead", age_days=STALE_AGE_DAYS + 5, importance=4, status="archived")]
    assert rule_stale_low_importance(rows, now=NOW) == []


# ── rule_heuristic_unconfirmed ───────────────────────────────────────────────

def test_heuristic_flags_unconfirmed_after_grace():
    rows = [_l2_row("heuristic fact", source="distill_heuristic",
                    age_days=HEURISTIC_GRACE_DAYS + 1, access_count=0)]
    f = rule_heuristic_unconfirmed(rows, now=NOW)
    assert len(f) == 1
    assert f[0].recommendation == "flag"
    assert f[0].rule == "heuristic_unconfirmed"


def test_heuristic_skips_within_grace():
    rows = [_l2_row("heuristic recent", source="distill_heuristic",
                    age_days=HEURISTIC_GRACE_DAYS - 1)]
    assert rule_heuristic_unconfirmed(rows, now=NOW) == []


def test_heuristic_skips_if_accessed():
    rows = [_l2_row("heuristic used", source="distill_heuristic",
                    age_days=HEURISTIC_GRACE_DAYS + 10, access_count=3)]
    assert rule_heuristic_unconfirmed(rows, now=NOW) == []


def test_heuristic_skips_non_heuristic_source():
    rows = [_l2_row("groq-derived", source="distill_groq",
                    age_days=HEURISTIC_GRACE_DAYS + 10)]
    assert rule_heuristic_unconfirmed(rows, now=NOW) == []


# ── rule_invalidated_aged ────────────────────────────────────────────────────

def test_invalidated_aged_deletes_old():
    rows = [_l3_row("ancient invalid", invalid_at_days_ago=INVALIDATED_GRACE_DAYS + 1)]
    f = rule_invalidated_aged(rows, now=NOW)
    assert len(f) == 1
    assert f[0].recommendation == "delete"
    assert f[0].target_tier == "l3"


def test_invalidated_aged_skips_recent_invalidation():
    rows = [_l3_row("recent invalid", invalid_at_days_ago=30)]
    assert rule_invalidated_aged(rows, now=NOW) == []


def test_invalidated_aged_skips_never_invalidated():
    rows = [_l3_row("never invalidated", invalid_at_days_ago=None)]
    assert rule_invalidated_aged(rows, now=NOW) == []


# ── rule_lexical_near_dup ────────────────────────────────────────────────────

def test_near_dup_finds_overlap_pairs():
    rows = [
        _l2_row("Ash prefers oregano bread with extra cheese", row_id="aaa"),
        _l2_row("Ash likes oregano bread and extra cheese please", row_id="bbb"),
        _l2_row("Different topic completely", row_id="ccc"),
    ]
    f = rule_lexical_near_dup(rows, now=NOW)
    assert len(f) == 1
    assert set(f[0].target_ids) == {"aaa", "bbb"}
    assert f[0].recommendation == "merge"


def test_near_dup_keeps_higher_importance():
    rows = [
        _l2_row("Ash likes pizza and pasta", importance=5, row_id="low"),
        _l2_row("Ash likes pizza and pasta a lot", importance=9, row_id="high"),
    ]
    f = rule_lexical_near_dup(rows, now=NOW)
    assert len(f) == 1
    # target_ids[0] should be the keeper
    assert f[0].target_ids[0] == "high"
    assert f[0].target_ids[1] == "low"


def test_near_dup_only_within_category():
    rows = [
        _l2_row("identical words here please", category="A", row_id="1"),
        _l2_row("identical words here please", category="B", row_id="2"),
    ]
    assert rule_lexical_near_dup(rows, now=NOW) == []


# ── rule_importance_eroded ───────────────────────────────────────────────────

def test_importance_eroded_flags_unused_high_imp():
    rows = [_l2_row("important but unused", importance=HIGH_IMPORTANCE_FLOOR + 1,
                    age_days=120, access_count=0)]
    f = rule_importance_eroded(rows, now=NOW)
    assert len(f) == 1
    assert f[0].recommendation == "flag"


def test_importance_eroded_skips_low_importance():
    rows = [_l2_row("not important enough", importance=5, age_days=200, access_count=0)]
    assert rule_importance_eroded(rows, now=NOW) == []


def test_importance_eroded_skips_recently_accessed():
    rows = [_l2_row("important and used", importance=9, age_days=200,
                    last_accessed_days_ago=5)]
    assert rule_importance_eroded(rows, now=NOW) == []


# ── Integration: high-importance never auto-archives ─────────────────────────

def test_high_importance_protected_from_auto_archive(tmp_path, monkeypatch):
    """The single most important safety property: importance >= 7 NEVER auto-archives.

    Defense in depth: BOTH the stale rule AND the orchestrator guard refuse to
    archive high-importance rows. This test asserts the END-TO-END property:
    no matter how stale, no matter how unused, a high-importance row is safe.
    """
    from memory.audit import run_audit
    import memory.audit as audit_mod

    high_imp_row = _l2_row("sacred fact", importance=9, age_days=STALE_AGE_DAYS + 100)

    mt = MagicMock()
    table_select = MagicMock()
    table_select.execute.return_value.data = [high_imp_row]
    mt.supabase.table.return_value.select.return_value.limit.return_value = table_select

    monkeypatch.setattr(audit_mod, "_audit_state_path",
                        lambda: str(tmp_path / "audit_state.json"))
    run = run_audit(mt, dry_run=False, now=NOW)
    assert len(run.archived) == 0, "high-importance row was auto-archived (forbidden)"


def test_orchestrator_downgrades_archive_to_flag_for_high_importance():
    """If a rule incorrectly returns recommendation=archive for a high-importance
    row, the orchestrator's safety guard MUST downgrade it to flag.

    This protects against future rule bugs — defense in depth.
    """
    from memory.audit import run_audit
    import memory.audit as audit_mod
    import memory.audit_rules as rules_mod
    from memory.audit_rules import AuditFinding

    # Synthetic high-imp row
    row = _l2_row("important and old", importance=9, age_days=200, row_id="protected")

    # Inject a hostile rule that incorrectly tries to archive a high-imp row
    def hostile_rule(rows, now=None):
        return [AuditFinding(
            rule="hostile",
            recommendation="archive",
            target_ids=["protected"],
            target_tier="l2",
            summary="should NOT actually archive",
        )]

    mt = MagicMock()
    table_select = MagicMock()
    table_select.execute.return_value.data = [row]
    mt.supabase.table.return_value.select.return_value.limit.return_value = table_select

    # Monkey-patch run_all_rules to return our hostile finding
    orig_run_all = audit_mod.run_all_rules
    audit_mod.run_all_rules = lambda l2, l3, now=None: hostile_rule(l2, now)
    try:
        import tempfile
        tmpdir = tempfile.mkdtemp()
        orig_state = audit_mod._audit_state_path
        audit_mod._audit_state_path = lambda: os.path.join(tmpdir, "audit_state.json")
        try:
            run = run_audit(mt, dry_run=False, now=NOW)
            assert len(run.archived) == 0, "orchestrator failed to block hostile archive"
            downgraded = [x for x in run.flagged
                          if x.get("downgraded_reason") == "high_importance"]
            assert len(downgraded) == 1, "expected downgrade to flag"
        finally:
            audit_mod._audit_state_path = orig_state
    finally:
        audit_mod.run_all_rules = orig_run_all


# ── Banner ───────────────────────────────────────────────────────────────────

def test_banner_quiet_when_no_findings(tmp_path, monkeypatch):
    from memory.audit import audit_banner_line, save_audit_state
    import memory.audit as audit_mod

    state_file = tmp_path / "audit_state.json"
    monkeypatch.setattr(audit_mod, "_audit_state_path", lambda: str(state_file))
    save_audit_state({
        "last_run_at": _iso(NOW),
        "last_run_flagged": 0,
        "last_run_archived": 0,
        "last_run_merge_suggestions": 0,
    })
    assert audit_banner_line(now=NOW) == ""


def test_banner_shows_findings(tmp_path, monkeypatch):
    from memory.audit import audit_banner_line, save_audit_state
    import memory.audit as audit_mod

    state_file = tmp_path / "audit_state.json"
    monkeypatch.setattr(audit_mod, "_audit_state_path", lambda: str(state_file))
    save_audit_state({
        "last_run_at": _iso(NOW),
        "last_run_flagged": 3,
        "last_run_archived": 1,
        "last_run_merge_suggestions": 2,
    })
    line = audit_banner_line(now=NOW)
    assert "3 flagged" in line
    assert "1 archived" in line
    assert "2 merge" in line


def test_banner_warns_when_stale(tmp_path, monkeypatch):
    from memory.audit import audit_banner_line, save_audit_state
    import memory.audit as audit_mod

    state_file = tmp_path / "audit_state.json"
    monkeypatch.setattr(audit_mod, "_audit_state_path", lambda: str(state_file))
    long_ago = _iso(NOW - timedelta(days=15))
    save_audit_state({"last_run_at": long_ago, "last_run_flagged": 0,
                      "last_run_archived": 0, "last_run_merge_suggestions": 0})
    line = audit_banner_line(now=NOW)
    assert "STALE" in line


# ── Idempotency ──────────────────────────────────────────────────────────────

def test_run_all_rules_is_idempotent():
    """Running the rules twice on the same data returns the same findings."""
    rows_l2 = [
        _l2_row("old low-imp", age_days=STALE_AGE_DAYS + 5, importance=4, row_id="a"),
        _l2_row("heuristic", source="distill_heuristic",
                age_days=HEURISTIC_GRACE_DAYS + 5, row_id="b"),
    ]
    rows_l3 = [_l3_row("invalid old", invalid_at_days_ago=INVALIDATED_GRACE_DAYS + 5, row_id="c")]
    f1 = run_all_rules(rows_l2, rows_l3, now=NOW)
    f2 = run_all_rules(rows_l2, rows_l3, now=NOW)
    assert [(x.rule, tuple(x.target_ids)) for x in f1] == \
           [(x.rule, tuple(x.target_ids)) for x in f2]
