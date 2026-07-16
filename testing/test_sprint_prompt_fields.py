"""testing/test_sprint_prompt_fields.py — T-279: planner/coder prompts carry the real ticket schema.

The first sprint dry-run rehearsal (T-276) produced a plan claiming the ticket
didn't name its files — because generate_plan read the dead pre-2026-05 schema
(what_failed/where_failed/why_likely/suggested_fix) and rendered its evidence
lines empty, and auto_implement's first message carried only id+title+plan.
These tests capture the real messages.create payloads with a fake client.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeResp:
    def __init__(self, text):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
        self.stop_reason = "end_turn"


class _CaptureClient:
    """client.messages.create(**kw) recorder returning a canned response."""

    def __init__(self, reply="plan text"):
        self.captured = []
        self._reply = reply
        self.messages = self

    def create(self, **kwargs):
        self.captured.append(kwargs)
        return _FakeResp(self._reply)


_TICKET = {
    "id": "T-999",
    "title": "test ticket",
    "severity": "P3",
    "component": "testing/",
    "current_state": "EVIDENCE_MARKER_12345 lives at file.py:42",
    "target_state": "TARGET_MARKER_67890",
    "migration_plan": ["STEP_MARKER_ALPHA", "STEP_MARKER_BETA"],
    "risk_notes": "RISK_MARKER_XYZ",
}


def test_planner_prompt_carries_current_schema_fields():
    from scripts.sprint import generate_plan

    client = _CaptureClient()
    generate_plan(client, dict(_TICKET))

    user_msg = client.captured[0]["messages"][0]["content"]
    assert "EVIDENCE_MARKER_12345" in user_msg, "planner never sees current_state"
    assert "TARGET_MARKER_67890" in user_msg, "planner never sees target_state"
    assert "STEP_MARKER_ALPHA" in user_msg, "planner never sees migration_plan"
    assert "RISK_MARKER_XYZ" in user_msg, "planner never sees risk_notes"


def test_coder_first_message_carries_evidence():
    from scripts.sprint import auto_implement

    client = _CaptureClient(reply="DONE")
    out = auto_implement(client, dict(_TICKET), plan="the plan",
                         deadline_ts=time.time() + 60)

    assert out["status"] == "done"
    first_user = client.captured[0]["messages"][0]["content"]
    assert "EVIDENCE_MARKER_12345" in first_user, "coder never sees current_state"
    assert "TARGET_MARKER_67890" in first_user, "coder never sees target_state"
    assert "RISK_MARKER_XYZ" in first_user, "coder never sees risk_notes"
