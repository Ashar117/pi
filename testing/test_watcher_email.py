"""T-257: email watcher fires once per new unread message, not on repeats."""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.watchers import _check_email


def _fake_gmail(configured=True, messages=None, search_ok=True, error=""):
    inst = MagicMock()
    inst.is_configured.return_value = configured
    if search_ok:
        inst.gmail_search.return_value = {
            "success": True, "messages": messages or [], "count": len(messages or []),
        }
    else:
        inst.gmail_search.return_value = {"success": False, "error": error, "messages": []}
    return inst


def test_not_configured_does_not_trigger():
    with patch("tools.tools_gmail.GmailTools", return_value=_fake_gmail(configured=False)):
        triggered, detail, state = _check_email({}, {})
    assert triggered is False
    assert "not configured" in detail.lower()


def test_search_failure_does_not_trigger():
    fake = _fake_gmail(search_ok=False, error="token expired")
    with patch("tools.tools_gmail.GmailTools", return_value=fake):
        triggered, detail, state = _check_email({}, {})
    assert triggered is False
    assert "token expired" in detail


def test_first_sweep_with_unread_triggers_once():
    msgs = [
        {"id": "m1", "from_short": "Boss", "subject": "Q3 numbers"},
        {"id": "m2", "from_short": "Newsletter", "subject": "Weekly digest"},
    ]
    fake = _fake_gmail(messages=msgs)
    with patch("tools.tools_gmail.GmailTools", return_value=fake):
        triggered, detail, state = _check_email({}, {})
    assert triggered is True
    assert "Boss" in detail and "Q3 numbers" in detail
    assert "+1 more" in detail
    assert set(state["seen_ids"]) == {"m1", "m2"}


def test_second_sweep_same_messages_does_not_retrigger():
    msgs = [{"id": "m1", "from_short": "Boss", "subject": "Q3 numbers"}]
    fake = _fake_gmail(messages=msgs)
    with patch("tools.tools_gmail.GmailTools", return_value=fake):
        triggered1, _, state1 = _check_email({}, {})
        triggered2, detail2, state2 = _check_email({}, state1)
    assert triggered1 is True
    assert triggered2 is False
    assert detail2 == ""


def test_new_message_after_seen_ones_triggers_again():
    fake = _fake_gmail(messages=[{"id": "m1", "from_short": "Boss", "subject": "First"}])
    with patch("tools.tools_gmail.GmailTools", return_value=fake):
        _, _, state1 = _check_email({}, {})

    fake2 = _fake_gmail(messages=[
        {"id": "m1", "from_short": "Boss", "subject": "First"},
        {"id": "m2", "from_short": "Client", "subject": "Second"},
    ])
    with patch("tools.tools_gmail.GmailTools", return_value=fake2):
        triggered, detail, state2 = _check_email({}, state1)
    assert triggered is True
    assert "Client" in detail and "Second" in detail
    assert "m1" not in state2.get("last_fired_ids", [])
    assert "m2" in state2["last_fired_ids"]


if __name__ == "__main__":
    test_not_configured_does_not_trigger()
    test_search_failure_does_not_trigger()
    test_first_sweep_with_unread_triggers_once()
    test_second_sweep_same_messages_does_not_retrigger()
    test_new_message_after_seen_ones_triggers_again()
    print("OK")
