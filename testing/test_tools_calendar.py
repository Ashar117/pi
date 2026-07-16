"""T-159 — tests for tools/tools_calendar.py (no network).

Covers the no-network surface: the _format_event formatter, is_configured(),
and the graceful {success: False, error} contracts every operation must honor
when the Google service is unavailable. The external service is never built.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_calendar import CalendarTools
import tools.tools_calendar as cal


# ── _format_event (pure) ─────────────────────────────────────────────────────

def test_format_event_timed():
    c = CalendarTools()
    ev = c._format_event({
        "id": "abc", "summary": "Standup", "location": "Zoom",
        "start": {"dateTime": "2026-06-01T09:30:00Z"},
        "end": {"dateTime": "2026-06-01T10:00:00Z"},
    })
    assert ev["title"] == "Standup"
    assert ev["time_str"] == "09:30"
    assert ev["location"] == "Zoom"
    assert ev["id"] == "abc"


def test_format_event_all_day():
    c = CalendarTools()
    ev = c._format_event({"summary": "Holiday", "start": {"date": "2026-06-01"},
                          "end": {"date": "2026-06-02"}})
    assert ev["time_str"] == "All day"
    assert ev["start"] == "2026-06-01"


def test_format_event_missing_fields_defaults():
    c = CalendarTools()
    ev = c._format_event({})
    assert ev["title"] == "(no title)"
    assert ev["time_str"] == "All day"
    assert ev["location"] == ""


def test_format_event_malformed_datetime_falls_back():
    c = CalendarTools()
    ev = c._format_event({"summary": "X", "start": {"dateTime": "not-a-date-xyz"}})
    # falls back to first 16 chars rather than crashing
    assert ev["time_str"] == "not-a-date-xyz"[:16]


def test_format_event_truncates_long_description():
    c = CalendarTools()
    ev = c._format_event({"summary": "X", "description": "y" * 500,
                          "start": {"date": "2026-06-01"}})
    assert len(ev["description"]) == 200


# ── is_configured ────────────────────────────────────────────────────────────

def test_is_configured_reflects_creds_file(tmp_path):
    fake = tmp_path / "gmail_credentials.json"
    with patch.object(cal, "_CREDS_FILE", fake):
        assert CalendarTools().is_configured() is False
        fake.write_text("{}", encoding="utf-8")
        assert CalendarTools().is_configured() is True


# ── graceful degradation: every op returns {success: False} on service error ──

def _failing_service(*a, **k):
    raise RuntimeError("no creds")


def test_calendar_today_graceful_when_unconfigured():
    c = CalendarTools()
    with patch.object(c, "_get_service", _failing_service):
        out = c.calendar_today()
    assert out["success"] is False and "error" in out
    assert "summary" in out  # still provides a user-facing summary


def test_calendar_upcoming_graceful():
    c = CalendarTools()
    with patch.object(c, "_get_service", _failing_service):
        out = c.calendar_upcoming(days=3)
    assert out["success"] is False


def test_calendar_search_graceful():
    c = CalendarTools()
    with patch.object(c, "_get_service", _failing_service):
        out = c.calendar_search("dentist")
    assert out["success"] is False and "error" in out


def test_calendar_create_graceful():
    c = CalendarTools()
    with patch.object(c, "_get_service", _failing_service):
        out = c.calendar_create(title="X", start="2026-06-01T10:00:00", end="2026-06-01T11:00:00")
    assert out["success"] is False and "error" in out


def test_calendar_delete_graceful():
    c = CalendarTools()
    with patch.object(c, "_get_service", _failing_service):
        out = c.calendar_delete("evt-1")
    assert out["success"] is False


# ── _get_events formats a summary from a mocked service ──────────────────────

class _FakeEvents:
    def __init__(self, items):
        self._items = items
    def list(self, **kwargs):
        self._kwargs = kwargs
        return self
    def execute(self):
        return {"items": self._items}


class _FakeService:
    def __init__(self, items):
        self._events = _FakeEvents(items)
    def events(self):
        return self._events


def test_get_events_summary_with_events():
    c = CalendarTools()
    items = [{"summary": "Lunch", "location": "Cafe",
              "start": {"dateTime": "2026-06-01T12:00:00Z"},
              "end": {"dateTime": "2026-06-01T13:00:00Z"}}]
    with patch.object(c, "_get_service", lambda: _FakeService(items)):
        out = c.calendar_today()
    assert out["success"] is True
    assert out["count"] == 1
    assert "Lunch" in out["summary"] and "12:00" in out["summary"]


def test_get_events_summary_empty():
    c = CalendarTools()
    with patch.object(c, "_get_service", lambda: _FakeService([])):
        out = c.calendar_upcoming(days=5)
    assert out["success"] is True and out["count"] == 0
    assert "no events" in out["summary"].lower()
