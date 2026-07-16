"""tools/tools_video_gen.py — Video generation (T-101).

Provider chain:
  1. Replicate (REPLICATE_API_TOKEN env var) — zeroscope-v2-xl, free credits.
  2. HuggingFace Inference API (HF_TOKEN) — zeroscope_v2_576w, free but slow.
  3. Fails gracefully with a clear error message.

Saves output to a temp .mp4 file and returns {"success": True, "path": ...}.
"""
from __future__ import annotations

import os
import tempfile
import time
from typing import Optional

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False

_REPLICATE_POLL_URL = "https://api.replicate.com/v1/predictions"
_REPLICATE_MODEL = "anotherjesse/zeroscope-v2-xl:9f747673945c62801b13b84701c783929c0ee784e4748ec062204894dda1a351"
_HF_VIDEO_MODEL = "damo-vilab/text-to-video-ms-1.7b"
_HF_API_URL = "https://api-inference.huggingface.co/models/{model}"


def _generate_replicate(prompt: str, save_path: Optional[str] = None) -> dict:
    token = os.environ.get("REPLICATE_API_TOKEN", "")
    if not token:
        return {"success": False, "error": "REPLICATE_API_TOKEN not set."}
    if not _HTTPX_OK:
        return {"success": False, "error": "httpx not installed — pip install httpx"}

    headers = {"Authorization": f"Token {token}", "Content-Type": "application/json"}
    payload = {
        "version": _REPLICATE_MODEL.split(":")[1],
        "input": {"prompt": prompt, "num_frames": 24, "fps": 8},
    }

    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(_REPLICATE_POLL_URL, headers=headers, json=payload)
            r.raise_for_status()
            prediction = r.json()
    except Exception as e:
        return {"success": False, "error": f"Replicate submit failed: {e}"}

    # Poll until complete (max ~120s)
    poll_url = prediction.get("urls", {}).get("get") or prediction.get("url")
    for _ in range(40):
        time.sleep(3)
        try:
            with httpx.Client(timeout=15) as client:
                r = client.get(poll_url, headers=headers)
                r.raise_for_status()
                status = r.json()
        except Exception as e:
            return {"success": False, "error": f"Replicate poll failed: {e}"}
        if status.get("status") == "succeeded":
            outputs = status.get("output") or []
            video_url = outputs[0] if outputs else None
            if not video_url:
                return {"success": False, "error": "Replicate returned no output URL."}
            break
        if status.get("status") in ("failed", "canceled"):
            return {"success": False, "error": f"Replicate prediction {status.get('status')}: {status.get('error', '')}"}
    else:
        return {"success": False, "error": "Replicate timed out after 120s."}

    # Download video
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(video_url)
            r.raise_for_status()
            data = r.content
    except Exception as e:
        return {"success": False, "error": f"Replicate download failed: {e}"}

    if save_path is None:
        ts = int(time.time())
        save_path = os.path.join(tempfile.gettempdir(), f"pi_video_{ts}.mp4")
    with open(save_path, "wb") as f:
        f.write(data)

    return {"success": True, "path": save_path, "backend": "replicate", "prompt": prompt, "bytes": len(data)}


def _generate_huggingface(prompt: str, save_path: Optional[str] = None) -> dict:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN", "")
    if not token:
        return {"success": False, "error": "HF_TOKEN not set — needed for HuggingFace video gen."}
    if not _HTTPX_OK:
        return {"success": False, "error": "httpx not installed — pip install httpx"}

    url = _HF_API_URL.format(model=_HF_VIDEO_MODEL)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=120) as client:
            r = client.post(url, headers=headers, json={"inputs": prompt})
            if r.status_code == 503:
                return {"success": False, "error": "HF model loading (cold start ~20s). Try again."}
            r.raise_for_status()
            data = r.content
    except Exception as e:
        return {"success": False, "error": f"HuggingFace video request failed: {e}"}

    if save_path is None:
        ts = int(time.time())
        save_path = os.path.join(tempfile.gettempdir(), f"pi_video_{ts}.mp4")
    with open(save_path, "wb") as f:
        f.write(data)

    return {"success": True, "path": save_path, "backend": "huggingface", "model": _HF_VIDEO_MODEL, "prompt": prompt, "bytes": len(data)}


def generate_video(prompt: str, save_path: Optional[str] = None) -> dict:
    """Generate a short video clip from a text prompt.

    Provider chain: Replicate → HuggingFace → error.
    Returns {"success": True, "path": "<path>.mp4"} or {"success": False, "error": "..."}.

    T-255: on full exhaustion, the error names every backend actually tried
    and why — previously only the last backend's message survived, so a
    Replicate failure followed by an HF failure silently hid the Replicate
    reason from the user.
    """
    attempted = []
    if os.environ.get("REPLICATE_API_TOKEN"):
        result = _generate_replicate(prompt, save_path)
        if result.get("success"):
            return result
        attempted.append(f"Replicate ({result.get('error', 'unknown error')})")

    result = _generate_huggingface(prompt, save_path)
    if result.get("success"):
        return result
    attempted.append(f"HuggingFace ({result.get('error', 'unknown error')})")

    return {"success": False, "error": "Video generation unavailable — tried " + "; ".join(attempted)}


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_generate_video(agent, tool_input, *, memory_override=None):
    return generate_video(prompt=tool_input.get("prompt", ""))


TOOLS = [
    ToolSpec(
        name="generate_video",
        description=(
            "Generate a short video clip from a text prompt. "
            "Uses Replicate (REPLICATE_API_TOKEN) or HuggingFace (HF_TOKEN). "
            "Returns path to an .mp4 file."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Text description of the video to generate.",
                },
            },
            "required": ["prompt"],
        },
        handler=_handle_generate_video,
        success_predicate=lambda r: r.get("success", False),
    ),
]
