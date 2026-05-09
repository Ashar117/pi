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
        backend:   "pollinations" (default, no key) or "huggingface" (HF_TOKEN needed).
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
    return _generate_pollinations(
        prompt, width=width, height=height, seed=seed, save_path=save_path
    )
