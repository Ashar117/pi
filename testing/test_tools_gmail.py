"""T-159 — tests for tools/tools_gmail.py (no network).

Covers is_configured() and the graceful {success: False, error} contract every
operation honors when the Gmail service is unavailable. Service never built.
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.tools_gmail import GmailTools
import tools.tools_gmail as gm


def test_is_configured_reflects_creds_file(tmp_path):
    fake = tmp_path / "gmail_credentials.json"
    with patch.object(gm, "_CREDS_FILE", fake):
        assert GmailTools().is_configured() is False
        fake.write_text("{}", encoding="utf-8")
        assert GmailTools().is_configured() is True


def _failing(*a, **k):
    raise RuntimeError("no creds")


def test_gmail_search_graceful():
    g = GmailTools()
    with patch.object(g, "_get_service", _failing):
        out = g.gmail_search("is:unread")
    assert out["success"] is False and out["messages"] == []


def test_gmail_read_graceful():
    g = GmailTools()
    with patch.object(g, "_get_service", _failing):
        out = g.gmail_read("msg-1")
    assert out["success"] is False and "error" in out


def test_inbox_summary_graceful():
    g = GmailTools()
    with patch.object(g, "_get_service", _failing):
        out = g.inbox_summary(max_results=5)
    assert out["success"] is False


def test_gmail_send_graceful():
    g = GmailTools()
    with patch.object(g, "_get_service", _failing):
        out = g.gmail_send(to="a@b.com", subject="hi", body="test")
    assert out["success"] is False and "error" in out


def test_gmail_send_creates_draft_never_sends():
    """T-271: gmail_send must call drafts().create, never messages().send."""
    g = GmailTools()
    mock_svc = MagicMock()
    mock_svc.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
        "id": "draft-123", "message": {"id": "msg-456"},
    }
    with patch.object(g, "_get_service", return_value=mock_svc):
        out = g.gmail_send(to="a@b.com", subject="hi", body="test")

    assert out == {
        "success": True, "draft_id": "draft-123", "message_id": "msg-456",
        "to": "a@b.com", "subject": "hi",
    }
    mock_svc.users.return_value.drafts.return_value.create.assert_called_once()
    mock_svc.users.return_value.messages.return_value.send.assert_not_called()
