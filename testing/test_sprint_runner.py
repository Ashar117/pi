"""
testing/test_sprint_runner.py — Phase D unit tests for scripts/sprint.py (T-043).

All offline. No real Claude calls — Anthropic client is stubbed end-to-end.
No git commands — subprocess is patched. No file moves outside tmp_path.
"""

import json
import os
import sys
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import sprint  # noqa: E402


# ── Risk classification ──────────────────────────────────────────────────────

class TestRiskClassification:
    def test_risk_flagged_by_component(self):
        assert sprint.is_risk_flagged({"component": "pi_agent.py"})
        assert sprint.is_risk_flagged({"component": "agent/tools.py"})
        assert sprint.is_risk_flagged({"component": "memory/pipeline.py"})
        assert sprint.is_risk_flagged({"component": "prompts/consciousness.txt"})

    def test_risk_flagged_by_where_failed(self):
        assert sprint.is_risk_flagged({
            "component": "tests/foo.py",
            "where_failed": "pi_agent.py:42",
        })

    def test_safe_components(self):
        assert not sprint.is_risk_flagged({"component": "scripts/refresh_pi.py"})
        assert not sprint.is_risk_flagged({"component": "testing/test_x.py"})
        assert not sprint.is_risk_flagged({"component": "docs/foo.md"})

    def test_is_safe_component(self):
        assert sprint.is_safe_component({"component": "scripts/foo.py"})
        assert sprint.is_safe_component({"component": "testing/test_x.py"})
        assert sprint.is_safe_component({"component": "docs/notes.md"})
        assert not sprint.is_safe_component({"component": "tools/tools_x.py"})


# ── Ticket selection ─────────────────────────────────────────────────────────

class TestPickTicket:
    def test_picks_highest_severity(self, tmp_path):
        d = tmp_path / "open"
        d.mkdir()
        (d / "T-100.json").write_text(json.dumps({
            "id": "T-100", "title": "low", "severity": "P3", "created": "2026-01-01",
        }))
        (d / "T-101.json").write_text(json.dumps({
            "id": "T-101", "title": "critical", "severity": "P0", "created": "2026-02-01",
        }))
        with patch.object(sprint, "TICKETS_OPEN", d):
            chosen = sprint.pick_ticket()
        assert chosen["id"] == "T-101"

    def test_skips_escalated(self, tmp_path):
        d = tmp_path / "open"
        d.mkdir()
        (d / "T-200.json").write_text(json.dumps({
            "id": "T-200", "title": "blocked", "severity": "P1", "status": "escalated",
        }))
        (d / "T-201.json").write_text(json.dumps({
            "id": "T-201", "title": "fresh", "severity": "P3",
        }))
        with patch.object(sprint, "TICKETS_OPEN", d):
            chosen = sprint.pick_ticket()
        assert chosen["id"] == "T-201"

    def test_returns_none_when_empty(self, tmp_path):
        with patch.object(sprint, "TICKETS_OPEN", tmp_path / "empty"):
            assert sprint.pick_ticket() is None

    def test_forced_id(self, tmp_path):
        d = tmp_path / "open"
        d.mkdir()
        (d / "T-300.json").write_text(json.dumps({"id": "T-300", "title": "x"}))
        (d / "T-301.json").write_text(json.dumps({"id": "T-301", "title": "y"}))
        with patch.object(sprint, "TICKETS_OPEN", d):
            chosen = sprint.pick_ticket(forced_id="T-301")
        assert chosen["id"] == "T-301"


# ── Safe path resolution ─────────────────────────────────────────────────────

class TestSafePath:
    def test_inside_root_ok(self):
        p = sprint._safe_path("scripts/sprint.py")
        assert str(p).startswith(str(sprint.ROOT))

    def test_escapes_blocked(self):
        with pytest.raises(sprint.SprintError):
            sprint._safe_path("../../../etc/passwd")


# ── Tool execution: read, write, edit, bash ──────────────────────────────────

class TestExecTool:
    def test_read_file_existing(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hello", encoding="utf-8")
        with patch.object(sprint, "ROOT", tmp_path):
            out = sprint._exec_tool("read_file", {"path": "x.txt"}, set())
        assert out == "hello"

    def test_read_file_missing(self, tmp_path):
        with patch.object(sprint, "ROOT", tmp_path):
            out = sprint._exec_tool("read_file", {"path": "no.txt"}, set())
        assert "ERROR" in out

    def test_write_file_records_changes(self, tmp_path):
        changes: set = set()
        with patch.object(sprint, "ROOT", tmp_path):
            out = sprint._exec_tool("write_file",
                                    {"path": "scripts/new.py", "content": "x = 1"},
                                    changes)
        assert "OK" in out
        assert "scripts/new.py" in changes
        assert (tmp_path / "scripts" / "new.py").read_text() == "x = 1"

    def test_write_file_blocks_risk_flagged(self, tmp_path):
        changes: set = set()
        with patch.object(sprint, "ROOT", tmp_path):
            out = sprint._exec_tool("write_file",
                                    {"path": "pi_agent.py", "content": "x"}, changes)
        assert "risk-flagged" in out
        assert not (tmp_path / "pi_agent.py").exists()

    def test_edit_file_unique_match(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("hello world", encoding="utf-8")
        changes: set = set()
        with patch.object(sprint, "ROOT", tmp_path):
            out = sprint._exec_tool("edit_file",
                                    {"path": "doc.md", "old_str": "world", "new_str": "Pi"},
                                    changes)
        assert "OK" in out
        assert f.read_text() == "hello Pi"

    def test_edit_file_non_unique_rejected(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("dup dup", encoding="utf-8")
        with patch.object(sprint, "ROOT", tmp_path):
            out = sprint._exec_tool("edit_file",
                                    {"path": "doc.md", "old_str": "dup", "new_str": "x"},
                                    set())
        assert "multiple times" in out

    def test_run_bash_success(self, tmp_path):
        with patch.object(sprint, "ROOT", tmp_path):
            out = sprint._exec_tool("run_bash", {"command": "echo hi"}, set())
        assert "exit=0" in out
        assert "hi" in out

    def test_run_bash_blocks_destructive(self, tmp_path):
        with patch.object(sprint, "ROOT", tmp_path):
            out = sprint._exec_tool("run_bash", {"command": "git push origin main"}, set())
        assert "blocked" in out


# ── close_ticket ─────────────────────────────────────────────────────────────

class TestCloseTicket:
    def test_appends_solution_and_moves_ticket(self, tmp_path):
        opens = tmp_path / "open"
        closed = tmp_path / "closed"
        opens.mkdir()
        closed.mkdir()
        sols = tmp_path / "SOLUTIONS.jsonl"

        ticket_path = opens / "T-999-test.json"
        ticket = {"id": "T-999", "title": "test ticket", "what_failed": "x"}
        ticket_path.write_text(json.dumps(ticket), encoding="utf-8")
        ticket["_path"] = str(ticket_path)

        with patch.object(sprint, "SOLUTIONS", sols), \
             patch.object(sprint, "TICKETS_CLOSED", closed):
            sol_id = sprint.close_ticket(ticket, "Fixed it.", ["scripts/foo.py"])

        assert sol_id.startswith("S-")
        assert sols.exists()
        sol_line = json.loads(sols.read_text(encoding="utf-8").strip())
        assert sol_line["ticket"] == "T-999"
        assert "Fixed" in sol_line["fix"]
        # ticket moved
        assert not ticket_path.exists()
        assert (closed / "T-999-test.json").exists()
        moved = json.loads((closed / "T-999-test.json").read_text(encoding="utf-8"))
        assert moved["status"] == "closed"
        assert moved["linked_solution"] == sol_id

    def test_increments_solution_id(self, tmp_path):
        sols = tmp_path / "SOLUTIONS.jsonl"
        sols.write_text(
            json.dumps({"id": "S-042"}) + "\n"
            + json.dumps({"id": "S-100"}) + "\n",
            encoding="utf-8",
        )
        opens = tmp_path / "open"
        opens.mkdir()
        closed = tmp_path / "closed"
        closed.mkdir()
        tp = opens / "T-1.json"
        tp.write_text(json.dumps({"id": "T-1"}))
        ticket = {"id": "T-1", "_path": str(tp), "title": "x"}

        with patch.object(sprint, "SOLUTIONS", sols), \
             patch.object(sprint, "TICKETS_CLOSED", closed):
            sol_id = sprint.close_ticket(ticket, "ok", [])
        assert sol_id == "S-101"


# ── Plan generation (mocked Anthropic) ───────────────────────────────────────

class TestGeneratePlan:
    def test_calls_claude_and_returns_text(self):
        # Stub the Anthropic client
        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock(text="## Plan\nDo the thing.")]
        msg.usage.input_tokens = 100
        msg.usage.output_tokens = 50
        client.messages.create.return_value = msg

        ticket = {"id": "T-1", "title": "fix x", "severity": "P3",
                  "component": "scripts/foo.py", "what_failed": "broken",
                  "where_failed": "", "why_likely": "", "suggested_fix": ""}
        # remove .text attribute from non-text blocks to test the filter
        for b in msg.content:
            b.text = "## Plan\nDo the thing."

        plan, cost = sprint.generate_plan(client, ticket)
        assert "Plan" in plan
        assert cost > 0


# ── Risk-flagged ticket auto-escalates ───────────────────────────────────────

class TestRunOneTicketEscalation:
    def test_risk_flagged_escalates_immediately(self, tmp_path, monkeypatch):
        opens = tmp_path / "open"
        opens.mkdir()
        ticket_path = opens / "T-RF-risky.json"
        ticket_path.write_text(json.dumps({
            "id": "T-RF", "title": "risky", "severity": "P2",
            "component": "pi_agent.py", "what_failed": "x",
        }))
        sprint_log = tmp_path / "sprint"

        monkeypatch.setattr(sprint, "TICKETS_OPEN", opens)
        monkeypatch.setattr(sprint, "SPRINT_LOG_DIR", sprint_log)

        # Stub plan generation so we don't hit the real API
        client = MagicMock()
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="## Plan\n...")],
            usage=MagicMock(input_tokens=10, output_tokens=10),
        )
        monkeypatch.setattr(sprint, "_claude_client", lambda: client)

        ticket = sprint.pick_ticket()
        assert ticket["id"] == "T-RF"

        args = MagicMock()
        args.auto_implement = True
        args.dry_run = False
        args.max_cost = 1.0

        status, cost = sprint.run_one_ticket(args, ticket, 0.0)
        assert status == "escalated"

    def test_unsafe_component_with_auto_implement_escalates(self, tmp_path, monkeypatch):
        opens = tmp_path / "open"
        opens.mkdir()
        ticket_path = opens / "T-UN-unsafe.json"
        ticket_path.write_text(json.dumps({
            "id": "T-UN", "title": "unsafe", "severity": "P3",
            "component": "tools/tools_foo.py", "what_failed": "x",
        }))
        sprint_log = tmp_path / "sprint"

        monkeypatch.setattr(sprint, "TICKETS_OPEN", opens)
        monkeypatch.setattr(sprint, "SPRINT_LOG_DIR", sprint_log)

        client = MagicMock()
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="## Plan\n...")],
            usage=MagicMock(input_tokens=10, output_tokens=10),
        )
        monkeypatch.setattr(sprint, "_claude_client", lambda: client)

        ticket = sprint.pick_ticket()
        args = MagicMock()
        args.auto_implement = True
        args.dry_run = False
        args.max_cost = 1.0

        status, _ = sprint.run_one_ticket(args, ticket, 0.0)
        assert status == "escalated"


# ── Plan-only mode ───────────────────────────────────────────────────────────

class TestPlanOnly:
    def test_plan_only_when_auto_implement_off(self, tmp_path, monkeypatch):
        opens = tmp_path / "open"
        opens.mkdir()
        (opens / "T-OK-safe.json").write_text(json.dumps({
            "id": "T-OK", "title": "safe", "severity": "P3",
            "component": "scripts/foo.py", "what_failed": "x",
        }))
        sprint_log = tmp_path / "sprint"

        monkeypatch.setattr(sprint, "TICKETS_OPEN", opens)
        monkeypatch.setattr(sprint, "SPRINT_LOG_DIR", sprint_log)

        client = MagicMock()
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="## Plan")],
            usage=MagicMock(input_tokens=10, output_tokens=10),
        )
        monkeypatch.setattr(sprint, "_claude_client", lambda: client)

        ticket = sprint.pick_ticket()
        args = MagicMock()
        args.auto_implement = False
        args.dry_run = False
        args.max_cost = 1.0

        status, _ = sprint.run_one_ticket(args, ticket, 0.0)
        assert status == "plan-only"
