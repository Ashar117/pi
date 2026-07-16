"""T-294: session summary is routed through LLMRouter (tier='cheap'), not a
bare Groq client. Offline — router is faked, no network.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.session import generate_session_summary  # noqa: E402


class _FakeRouter:
    def __init__(self, text="summary text"):
        self.calls = []
        self._text = text

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        resp = MagicMock()
        resp.text = self._text
        return resp


def test_summary_calls_router_with_cheap_tier():
    router = _FakeRouter()
    messages = [
        {"role": "user", "content": "what is 2 plus 2?"},
        {"role": "assistant", "content": "4"},
    ]
    summary = generate_session_summary(router, messages, n=12)

    assert summary == "summary text"
    assert router.calls, "router.chat was not called"
    assert router.calls[0]["tier"] == "cheap"


def test_summary_returns_empty_on_empty_context():
    router = _FakeRouter()
    summary = generate_session_summary(router, [], n=12)
    assert summary == ""
    assert router.calls == []


def test_summary_never_raises_on_router_failure():
    class _BoomRouter:
        def chat(self, **kwargs):
            raise RuntimeError("all providers failed")

    messages = [{"role": "user", "content": "hello"}]
    summary = generate_session_summary(_BoomRouter(), messages, n=12)
    assert summary == ""
