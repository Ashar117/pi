"""T-299: write-time temporal inference — ephemeral phrasing auto-sets expiry.

Table-driven, no clock mocking (helpers take `now` as a parameter).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_memory import _infer_expiry, MemoryTools  # noqa: E402

_NOW = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)  # a Wednesday


def test_just_for_today_expires_end_of_today():
    got = _infer_expiry("just for today: the cafe wifi is FISH123", _NOW)
    assert got is not None
    assert got.date() == _NOW.date()
    assert got.hour == 23 and got.minute == 59


def test_for_today_only_variants():
    for phrase in ("for today only, the desk is B12", "today only special hours"):
        got = _infer_expiry(phrase, _NOW)
        assert got is not None and got.date() == _NOW.date(), phrase


def test_tonight_expires_end_of_today():
    got = _infer_expiry("the show is tonight at the park", _NOW)
    assert got is not None and got.date() == _NOW.date()


def test_until_tomorrow():
    got = _infer_expiry("this pass is valid until tomorrow", _NOW)
    assert got is not None
    assert got.date() == (_NOW + timedelta(days=1)).date()


def test_for_the_next_n_hours():
    got = _infer_expiry("the deal is live for the next 3 hours", _NOW)
    assert got == _NOW + timedelta(hours=3)


def test_for_the_next_n_days():
    got = _infer_expiry("im out of office for the next 5 days", _NOW)
    assert got == _NOW + timedelta(days=5)


def test_for_the_next_n_weeks():
    got = _infer_expiry("the road is closed for the next 2 weeks", _NOW)
    assert got == _NOW + timedelta(weeks=2)


def test_this_week():
    got = _infer_expiry("im parked in deck B this week", _NOW)
    assert got == _NOW + timedelta(days=7)


def test_this_month():
    got = _infer_expiry("the promo code works this month", _NOW)
    assert got == _NOW + timedelta(days=31)


def test_until_weekday_this_week_still_ahead():
    # _NOW is Wednesday 2026-07-15; "until friday" should land THIS friday (+2 days).
    got = _infer_expiry("the offer is valid until friday", _NOW)
    assert got is not None
    assert got.date() == (_NOW + timedelta(days=2)).date()
    assert got.hour == 23 and got.minute == 59


def test_until_weekday_same_day_rolls_to_next_week():
    # "until wednesday" said ON a Wednesday means NEXT Wednesday, not today.
    got = _infer_expiry("the offer is valid until wednesday", _NOW)
    assert got is not None
    assert got.date() == (_NOW + timedelta(days=7)).date()


def test_bare_weekday_mention_does_not_expire():
    """The false-positive guard: 'meeting on friday' is NOT a validity phrase."""
    assert _infer_expiry("we have a meeting on friday about the launch", _NOW) is None
    assert _infer_expiry("friday is usually a slow day", _NOW) is None


def test_non_ephemeral_content_stays_permanent():
    facts = [
        "my sister lives in Boston",
        "the project codename is BLUEHERON",
        "i prefer dark roast coffee",
    ]
    for f in facts:
        assert _infer_expiry(f, _NOW) is None, f


# ── Integration: memory_write wires the inference through ────────────────────

def _offline_mt(tmp_path):
    return MemoryTools(supabase_url="", supabase_key="",
                        sqlite_path=str(tmp_path / "pi.db"))


def test_memory_write_infers_expiry_and_reports_it(tmp_path):
    mt = _offline_mt(tmp_path)
    result = mt.memory_write(
        content="just for today: the cafe wifi password is FISH123",
        tier="l3", category="note", importance=6,
    )
    assert "auto_expiry" in result, f"expected auto_expiry in result, got {result}"

    import sqlite3
    conn = sqlite3.connect(mt.sqlite_path)
    row = conn.execute(
        "SELECT active_until FROM l3_cache WHERE content LIKE '%FISH123%'"
    ).fetchone()
    conn.close()
    assert row is not None and row[0] is not None


def test_memory_write_explicit_expiry_wins_over_inference(tmp_path):
    mt = _offline_mt(tmp_path)
    explicit = datetime.now(timezone.utc) + timedelta(days=30)
    result = mt.memory_write(
        content="just for today: but I'm overriding the expiry",
        tier="l3", category="note", importance=6, expiry=explicit,
    )
    assert "auto_expiry" not in result, "explicit expiry must not be reported as inferred"


def test_memory_write_permanent_fact_has_no_auto_expiry(tmp_path):
    mt = _offline_mt(tmp_path)
    result = mt.memory_write(
        content="my sister lives in Boston",
        tier="l3", category="note", importance=6,
    )
    assert "auto_expiry" not in result
