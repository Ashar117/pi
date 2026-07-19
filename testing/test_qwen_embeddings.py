"""T-290: Qwen/DashScope embeddings in the embedding engine (offline, mocked)."""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("openai")


def _stub_openai(monkeypatch, vector):
    import openai
    captured = {}

    class _FakeEmbeddings:
        def create(self, **kw):
            captured.update(kw)
            item = MagicMock()
            item.embedding = vector
            resp = MagicMock()
            resp.data = [item]
            return resp

    class _FakeClient:
        def __init__(self, *a, **k):
            captured["init_kwargs"] = k
            self.embeddings = _FakeEmbeddings()

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    return captured


def test_qwen_embed_hits_dashscope_base_url(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "fake-key")
    import memory.semantic_dedup as sd
    sd._QWEN_CLIENT = None
    captured = _stub_openai(monkeypatch, [0.1, 0.2, 0.3])

    vec = sd._qwen_embed("hello")

    assert vec == [0.1, 0.2, 0.3]
    assert captured["init_kwargs"]["base_url"] == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert captured["model"] == "text-embedding-v4"


def test_get_embedding_prefers_qwen_when_key_set(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "fake-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import memory.semantic_dedup as sd
    sd._QWEN_CLIENT = None
    sd._GEMINI_CLIENT = None
    _stub_openai(monkeypatch, [1.0, 2.0])

    with patch.object(sd, "_gemini_client", return_value=None) as gem:
        result = sd.get_embedding("some fact")

    assert result == [1.0, 2.0]
    gem.assert_not_called()


def test_get_embedding_falls_back_to_gemini_without_qwen_key(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    import memory.semantic_dedup as sd
    sd._QWEN_CLIENT = None

    fake_gemini = MagicMock()
    with patch.object(sd, "_gemini_client", return_value=fake_gemini):
        with patch.object(fake_gemini.models, "embed_content") as embed_call:
            emb_obj = MagicMock()
            emb_obj.values = [9.0, 9.0]
            resp = MagicMock()
            resp.embeddings = [emb_obj]
            embed_call.return_value = resp
            result = sd.get_embedding("some fact")

    assert result == [9.0, 9.0]
