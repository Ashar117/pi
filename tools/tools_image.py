"""
tools/tools_image.py — Image generation for Pi.

Two backends:
  1. Pollinations.ai — zero cost, no API key, HTTP GET, returns JPEG bytes.
     Default. Works immediately with no setup.
  2. HuggingFace Inference API — free account + HF_TOKEN env var, better
     quality (SDXL, FLUX-schnell), rate-limited ~60 req/hr free tier.

Images are saved to the system temp dir by default. Returns the file path.
"""

import os
import time
import tempfile
from typing import Optional
from urllib.parse import quote

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False


# ---------------------------------------------------------------------------
# Pollinations.ai backend (no key, always free)
# ---------------------------------------------------------------------------

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"
POLLINATIONS_PARAMS = "?width={width}&height={height}&seed={seed}&nologo=true"


def _generate_pollinations(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    seed: Optional[int] = None,
    save_path: Optional[str] = None,
) -> dict:
    if not _HTTPX_OK:
        return {"success": False, "error": "httpx not installed — pip install httpx"}

    seed = seed or int(time.time()) % 99999
    encoded = quote(prompt)
    url = (
        POLLINATIONS_URL.format(prompt=encoded)
        + POLLINATIONS_PARAMS.format(width=width, height=height, seed=seed)
    )

    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.content
    except Exception as e:
        return {"success": False, "error": f"Pollinations request failed: {e}"}

    if save_path is None:
        suffix = f"_pi_{seed}.jpg"
        save_path = os.path.join(tempfile.gettempdir(), f"pi_image_{seed}.jpg")

    try:
        with open(save_path, "wb") as f:
            f.write(data)
    except Exception as e:
        return {"success": False, "error": f"Failed to save image: {e}"}

    return {
        "success": True,
        "path": save_path,
        "backend": "pollinations",
        "prompt": prompt,
        "seed": seed,
        "size": f"{width}x{height}",
        "bytes": len(data),
    }


# ---------------------------------------------------------------------------
# HuggingFace Inference API backend (free tier, better quality)
# ---------------------------------------------------------------------------

HF_API_URL = "https://api-inference.huggingface.co/models/{model}"
HF_DEFAULT_MODEL = "black-forest-labs/FLUX.1-schnell"


def _generate_huggingface(
    prompt: str,
    model: str = HF_DEFAULT_MODEL,
    save_path: Optional[str] = None,
    hf_token: Optional[str] = None,
) -> dict:
    if not _HTTPX_OK:
        return {"success": False, "error": "httpx not installed — pip install httpx"}

    token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        return {
            "success": False,
            "error": "No HF_TOKEN found. Set HF_TOKEN env var (free at huggingface.co).",
        }

    url = HF_API_URL.format(model=model)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with httpx.Client(timeout=90) as client:
            r = client.post(url, headers=headers, json={"inputs": prompt})
            if r.status_code == 503:
                return {
                    "success": False,
                    "error": f"Model '{model}' is loading (cold start). Try again in ~20s.",
                }
            r.raise_for_status()
            data = r.content
    except Exception as e:
        return {"success": False, "error": f"HuggingFace request failed: {e}"}

    if save_path is None:
        ts = int(time.time())
        save_path = os.path.join(tempfile.gettempdir(), f"pi_image_{ts}.png")

    try:
        with open(save_path, "wb") as f:
            f.write(data)
    except Exception as e:
        return {"success": False, "error": f"Failed to save image: {e}"}

    return {
        "success": True,
        "path": save_path,
        "backend": "huggingface",
        "model": model,
        "prompt": prompt,
        "bytes": len(data),
    }


# ---------------------------------------------------------------------------
# Gemini (Imagen) backend — free tier via GEMINI_API_KEY, quality tier above
# FLUX-schnell. T-268: reuses the same key already wired for research mode
# and the LLM router's Gemini provider — no new account, no new dependency.
# ---------------------------------------------------------------------------

GEMINI_IMAGE_MODEL = "imagen-3.0-generate-002"

try:
    from google import genai as _genai
    from google.genai import types as _genai_types
    _GENAI_OK = True
except ImportError:
    _GENAI_OK = False


def _generate_gemini(
    prompt: str,
    save_path: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
) -> dict:
    if not _GENAI_OK:
        return {"success": False, "error": "google-genai not installed — pip install google-genai"}

    key = gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return {"success": False, "error": "No GEMINI_API_KEY found. Set GEMINI_API_KEY env var."}

    try:
        client = _genai.Client(api_key=key)
        response = client.models.generate_images(
            model=GEMINI_IMAGE_MODEL,
            prompt=prompt,
            config=_genai_types.GenerateImagesConfig(number_of_images=1),
        )
        data = response.generated_images[0].image.image_bytes
    except Exception as e:
        return {"success": False, "error": f"Gemini image request failed: {e}"}

    if save_path is None:
        ts = int(time.time())
        save_path = os.path.join(tempfile.gettempdir(), f"pi_image_{ts}.png")

    try:
        with open(save_path, "wb") as f:
            f.write(data)
    except Exception as e:
        return {"success": False, "error": f"Failed to save image: {e}"}

    return {
        "success": True,
        "path": save_path,
        "backend": "gemini",
        "model": GEMINI_IMAGE_MODEL,
        "prompt": prompt,
        "bytes": len(data),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_image(
    prompt: str,
    backend: str = "pollinations",
    width: int = 1024,
    height: int = 1024,
    seed: Optional[int] = None,
    save_path: Optional[str] = None,
    hf_model: str = HF_DEFAULT_MODEL,
    hf_token: Optional[str] = None,
) -> dict:
    """
    Generate an image from a text prompt.

    Args:
        prompt:    Text description of the image to generate.
        backend:   "pollinations" (default, no key), "huggingface" (HF_TOKEN
                   needed), or "gemini" (GEMINI_API_KEY needed — quality tier
                   above FLUX-schnell, T-268).
        width:     Image width in pixels (pollinations only, default 1024).
        height:    Image height in pixels (pollinations only, default 1024).
        seed:      Random seed (pollinations only; None = random).
        save_path: Where to save the image. None = system temp dir.
        hf_model:  HuggingFace model ID (huggingface backend only).
        hf_token:  Override HF_TOKEN env var.

    Returns:
        {
            "success": bool,
            "path":    str,      # absolute path to saved image
            "backend": str,
            "prompt":  str,
            ...
        }
    """
    if backend == "huggingface":
        return _generate_huggingface(
            prompt, model=hf_model, save_path=save_path, hf_token=hf_token
        )
    if backend == "gemini":
        return _generate_gemini(prompt, save_path=save_path)
    return _generate_pollinations(
        prompt, width=width, height=height, seed=seed, save_path=save_path
    )


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_image_gen(agent, tool_input, *, memory_override=None):
    result = generate_image(
        prompt=tool_input["prompt"],
        backend=tool_input.get("backend", "pollinations"),
        width=tool_input.get("width", 1024),
        height=tool_input.get("height", 1024),
        save_path=tool_input.get("save_path"),
    )
    if result.get("success") and result.get("path"):
        result["message"] = f"Image saved to: {result['path']}"
    return result


TOOLS = [
    ToolSpec(
        name="image_gen",
        description=(
            "Generate an image from a text prompt and save it to disk. Returns the "
            "absolute file path. Backend 'pollinations' is free with no API key. "
            "Backend 'huggingface' is higher quality but needs HF_TOKEN env var. "
            "Backend 'gemini' is quality tier above huggingface, needs GEMINI_API_KEY."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt":    {"type": "string",
                              "description": "Text description of the image to generate"},
                "backend":   {"type": "string",
                              "enum": ["pollinations", "huggingface", "gemini"],
                              "default": "pollinations",
                              "description": "Which backend to use (default: pollinations)"},
                "width":     {"type": "integer", "default": 1024,
                              "description": "Image width in pixels (pollinations only)"},
                "height":    {"type": "integer", "default": 1024,
                              "description": "Image height in pixels (pollinations only)"},
                "save_path": {"type": "string",
                              "description": "Where to save the image. Omit for auto temp path."},
            },
            "required": ["prompt"],
        },
        handler=_handle_image_gen,
        success_predicate=lambda r: r.get("success", False),
    ),
]
