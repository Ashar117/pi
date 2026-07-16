"""T-231: Cerebras model id is correct (default was llama-3.3-70b which 404s;
correct Cerebras API model name is llama3.3-70b — no dash between llama and version).

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
    class _FakeClient:
        def __init__(self, *a, **k):
            self.chat = type("C", (), {"completions": type("CC", (), {"create": lambda self, **k: None})()})()
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)


def test_default_model_has_no_dash_between_llama_and_version(monkeypatch):
    _stub_openai(monkeypatch)
    from core.providers.cerebras import CerebrasProvider
    p = CerebrasProvider("fake-key")
    assert p.model == "llama3.3-70b"
    assert p.model != "llama-3.3-70b"  # T-231: this was the broken name causing 404


def test_router_cerebras_model_default_is_correct():
    """The router's cerebras_model default must be the working API model id."""
    from core.llm_router import LLMRouter
    default = inspect.signature(LLMRouter.__init__).parameters["cerebras_model"].default
    # Updated 2026-06: Cerebras retired llama3.3-70b; Production model is gpt-oss-120b
    assert default == "gpt-oss-120b"
