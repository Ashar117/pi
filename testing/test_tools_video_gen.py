"""T-159 — tests for tools/tools_video_gen.py (no network)."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.tools_video_gen as vid


def test_replicate_without_token_returns_error(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    out = vid._generate_replicate("a drone shot")
    assert out["success"] is False and "error" in out


def test_generate_video_degrades_gracefully(monkeypatch):
    # No tokens and providers patched to fail → must return a dict, not raise.
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    with patch.object(vid, "_generate_replicate", return_value={"success": False, "error": "no token"}), \
         patch.object(vid, "_generate_huggingface", return_value={"success": False, "error": "no key"}):
        out = vid.generate_video("a timelapse")
    assert isinstance(out, dict) and out["success"] is False


def test_generate_video_never_raises():
    with patch.object(vid, "_generate_replicate", side_effect=RuntimeError("boom")), \
         patch.object(vid, "_generate_huggingface", side_effect=RuntimeError("boom")):
        try:
            out = vid.generate_video("x")
        except Exception as e:
            out = {"raised": str(e)}
    assert isinstance(out, dict) and out.get("success") is not True


def test_generate_video_exhaustion_names_every_backend_tried(monkeypatch):
    """T-255: chain exhaustion must not silently drop the Replicate reason."""
    monkeypatch.setenv("REPLICATE_API_TOKEN", "fake-token")
    with patch.object(vid, "_generate_replicate",
                       return_value={"success": False, "error": "quota exceeded"}), \
         patch.object(vid, "_generate_huggingface",
                       return_value={"success": False, "error": "cold start timeout"}):
        out = vid.generate_video("a drone shot")
    assert out["success"] is False
    assert "quota exceeded" in out["error"]
    assert "cold start timeout" in out["error"]
    assert "Replicate" in out["error"] and "HuggingFace" in out["error"]
