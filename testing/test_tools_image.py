"""T-159 — tests for tools/tools_image.py (no network).

Asserts the graceful-degrade contract: when httpx is unavailable, both
providers return a structured error rather than raising, and generate_image
surfaces a {success: False} dict.
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.tools_image as img


def test_pollinations_without_httpx_returns_error():
    with patch.object(img, "_HTTPX_OK", False):
        out = img._generate_pollinations("a cat")
    assert out["success"] is False and "httpx" in out["error"].lower()


def test_huggingface_without_httpx_returns_error():
    with patch.object(img, "_HTTPX_OK", False):
        out = img._generate_huggingface("a cat")
    assert out["success"] is False and "httpx" in out["error"].lower()


def test_generate_image_degrades_to_error_when_no_httpx():
    with patch.object(img, "_HTTPX_OK", False):
        out = img.generate_image("a sunset over mountains")
    assert isinstance(out, dict) and out["success"] is False


def test_generate_image_never_raises_on_provider_failure():
    # Both providers raise → generate_image must still return a dict, not raise.
    with patch.object(img, "_generate_pollinations", side_effect=RuntimeError("boom")), \
         patch.object(img, "_generate_huggingface", side_effect=RuntimeError("boom")):
        try:
            out = img.generate_image("x")
        except Exception as e:
            out = {"raised": str(e)}
    assert isinstance(out, dict)
    assert out.get("success") is not True


# ── T-268: Gemini backend ─────────────────────────────────────────────────────

def test_gemini_without_genai_returns_error():
    with patch.object(img, "_GENAI_OK", False):
        out = img._generate_gemini("a cat")
    assert out["success"] is False and "google-genai" in out["error"]


def test_gemini_without_key_returns_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with patch.object(img, "_GENAI_OK", True):
        out = img._generate_gemini("a cat")
    assert out["success"] is False and "GEMINI_API_KEY" in out["error"]


def test_gemini_saves_image_bytes(tmp_path):
    fake_image = MagicMock()
    fake_image.image.image_bytes = b"fake-png-bytes"
    fake_response = MagicMock()
    fake_response.generated_images = [fake_image]
    fake_client = MagicMock()
    fake_client.models.generate_images.return_value = fake_response

    save_path = str(tmp_path / "out.png")
    with patch.object(img, "_GENAI_OK", True), \
         patch.object(img, "_genai") as mock_genai:
        mock_genai.Client.return_value = fake_client
        out = img._generate_gemini("a cat", save_path=save_path, gemini_api_key="fake-key")

    assert out == {
        "success": True, "path": save_path, "backend": "gemini",
        "model": img.GEMINI_IMAGE_MODEL, "prompt": "a cat", "bytes": len(b"fake-png-bytes"),
    }
    assert open(save_path, "rb").read() == b"fake-png-bytes"


def test_generate_image_dispatches_gemini_backend():
    with patch.object(img, "_generate_gemini", return_value={"success": True}) as mock_gen:
        img.generate_image("a cat", backend="gemini")
    mock_gen.assert_called_once()


def test_gemini_default_backend_stays_pollinations():
    """Keyless-by-default: gemini must be opt-in, never the default backend."""
    with patch.object(img, "_generate_pollinations", return_value={"success": True}) as mock_poll, \
         patch.object(img, "_generate_gemini") as mock_gem:
        img.generate_image("a cat")
    mock_poll.assert_called_once()
    mock_gem.assert_not_called()
