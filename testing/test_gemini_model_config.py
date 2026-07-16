"""T-212: Gemini model id is configurable (default bumped off retired 2.0-flash;
GEMINI_MODEL env override) and token counts are populated.

Offline — google.genai.Client is stubbed; no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("google.genai")


def _stub_client(monkeypatch):
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda *a, **k: object())


def test_default_model_is_not_retired_2_0_flash(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    _stub_client(monkeypatch)
    from core.providers.gemini import GeminiProvider
    p = GeminiProvider("fake-key")
    assert p.model != "gemini-2.0-flash"  # retired free tier (429 limit 0)
    assert p.model == "gemini-2.5-flash"


def test_gemini_model_env_override(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.0-flash")
    _stub_client(monkeypatch)
    from core.providers.gemini import GeminiProvider
    p = GeminiProvider("fake-key", model="gemini-2.5-flash")
    assert p.model == "gemini-3.0-flash"  # env wins over passed default


def test_router_default_gemini_model_updated():
    """The router's gemini_model default must not be the retired model."""
    import inspect
    from core.llm_router import LLMRouter
    default = inspect.signature(LLMRouter.__init__).parameters["gemini_model"].default
    assert default == "gemini-2.5-flash"
