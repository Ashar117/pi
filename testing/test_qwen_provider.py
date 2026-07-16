"""Qwen Cloud hackathon: QwenProvider wiring (DashScope, OpenAI-compatible).

Offline — openai.OpenAI client is stubbed; no network.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("openai")


def _stub_openai(monkeypatch):
    import openai
    captured = {}
    class _FakeClient:
        def __init__(self, *a, **k):
            captured.update(k)
            self.chat = type("C", (), {"completions": type("CC", (), {"create": lambda self, **k: None})()})()
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    return captured


def test_provider_name_model_and_base_url(monkeypatch):
    captured = _stub_openai(monkeypatch)
    from core.providers.qwen import QwenProvider
    p = QwenProvider("fake-key")
    assert p.name == "qwen"
    assert p.model == "qwen-max"
    # DashScope OpenAI-compatible endpoint — the Alibaba Cloud proof-of-use
    assert captured["base_url"] == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def test_router_registers_qwen_when_key_present(monkeypatch):
    _stub_openai(monkeypatch)
    from core.llm_router import LLMRouter
    r = LLMRouter(qwen_key="fake-key", enable_ollama=False)
    assert [p.name for p in r._providers] == ["qwen"]


def test_qwen_first_in_premium_balanced_cheap_tiers():
    from core.llm_router import LLMRouter
    for tier in ("premium", "balanced", "cheap"):
        assert LLMRouter._TIER_ORDERS[tier][0] == "qwen", tier


def test_qwen_has_daily_token_budget():
    from core.llm_router import PROVIDER_DAILY_TOKEN_BUDGET
    assert PROVIDER_DAILY_TOKEN_BUDGET["qwen"], "hackathon credits are finite — cap required"


def test_router_qwen_model_default():
    from core.llm_router import LLMRouter
    default = inspect.signature(LLMRouter.__init__).parameters["qwen_model"].default
    assert default == "qwen-max"
