"""T-154 — sprint runner refuses to auto-implement unverified (self-report) tickets.

A wrong self-diagnosis + green verify would falsely "close" a bug (see T-143).
The auto path only runs "verified" tickets; a forced pick (explicit human
choice) bypasses the gate.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import sprint


# ── ticket_confidence classification ─────────────────────────────────────────

def test_explicit_verified():
    assert sprint.ticket_confidence({"root_cause_confidence": "verified"}) == "verified"


def test_explicit_hypothesis_first_token():
    t = {"root_cause_confidence": "hypothesis (original) — corrected later"}
    assert sprint.ticket_confidence(t) == "hypothesis"


def test_self_report_defaults_to_hypothesis():
    assert sprint.ticket_confidence(
        {"source": "Pi self-report (session tool call)"}
    ) == "hypothesis"


def test_other_source_defaults_to_verified():
    assert sprint.ticket_confidence({"source": "Claude session audit"}) == "verified"


# ── pick_ticket auto-gate ─────────────────────────────────────────────────────

def test_auto_skips_hypothesis_picks_verified(monkeypatch):
    tix = [
        {"id": "T-900", "source": "Pi self-report", "severity": "P1"},
        {"id": "T-901", "source": "human", "root_cause_confidence": "verified", "severity": "P2"},
    ]
    monkeypatch.setattr(sprint, "list_open_tickets", lambda: tix)
    assert sprint.pick_ticket().get("id") == "T-901"


def test_auto_returns_none_when_all_hypothesis(monkeypatch):
    tix = [{"id": "T-900", "source": "Pi self-report", "severity": "P0"}]
    monkeypatch.setattr(sprint, "list_open_tickets", lambda: tix)
    assert sprint.pick_ticket() is None


def test_forced_pick_bypasses_gate(monkeypatch):
    tix = [{"id": "T-900", "source": "Pi self-report", "severity": "P0"}]
    monkeypatch.setattr(sprint, "list_open_tickets", lambda: tix)
    assert sprint.pick_ticket(forced_id="T-900").get("id") == "T-900"
