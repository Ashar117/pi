"""T-187: Brain HTTP+SSE server — FastAPI wrapper around the single PiAgent.

Bind: 127.0.0.1:7712 (localhost-only, hard-coded; never 0.0.0.0).
Auth: Bearer token from PI_SERVER_TOKEN env var.  401 if missing or wrong.

Endpoints:
  POST /chat              — {text, conversation_id?} → {conversation_id, response}
  GET  /chat/stream       — SSE token stream for last POST (per-client, fire-and-forget)
  GET  /conversations     — list recent conversations (from T-186 store)
  GET  /health            — {status, mode, turn_number}

Concurrency model:
  The PiAgent is single-threaded by design.  A global asyncio.Lock serialises
  all /chat calls FIFO.  Per-conversation context switching uses the T-186
  resume path (load_conversation_turns → messages rebuild).
"""
from __future__ import annotations

import asyncio
import hmac
import os
import time
from typing import AsyncIterator, Optional

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

_WEB_DIR = Path(__file__).parent.parent / "web"

# Default 127.0.0.1 — localhost boundary is the auth perimeter. Cloud deploys
# (e.g. Alibaba ECS) may override via PI_SERVER_HOST, but then PI_SERVER_TOKEN
# becomes mandatory: binding beyond localhost without auth is refused at import.
SERVER_HOST = os.environ.get("PI_SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("PI_HTTP_PORT", "7712"))

_SERVER_TOKEN: str = os.environ.get("PI_SERVER_TOKEN", "")

if SERVER_HOST not in ("127.0.0.1", "localhost") and not _SERVER_TOKEN:
    raise RuntimeError(
        "PI_SERVER_HOST is non-localhost but PI_SERVER_TOKEN is unset — "
        "refusing to expose an unauthenticated server. Set PI_SERVER_TOKEN."
    )
_TURN_LOCK = asyncio.Lock()
_AGENT = None  # set by mount_agent()

_bearer_scheme = HTTPBearer(auto_error=False)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _verify_token(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)) -> None:
    if not _SERVER_TOKEN:
        return  # token not configured — open (localhost-only anyway)
    if creds is None or not hmac.compare_digest(creds.credentials, _SERVER_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Models ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    text: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    mode: str


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Pi Brain Server", version="1.0.0", docs_url=None, redoc_url=None)

# T-190: allow chrome-extension:// and null origins (dev) — 127.0.0.1-only so safe
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"(chrome-extension://.*|null|http://127\.0\.0\.1(:\d+)?)",
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# T-189: serve web/ as /static and index.html at /
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_ui():
    index = _WEB_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Pi Brain Server</h1><p>web/index.html not found.</p>")


def mount_agent(agent) -> None:
    """Called by pi_daemon after the agent is warm."""
    global _AGENT
    _AGENT = agent


def _get_agent():
    if _AGENT is None:
        raise HTTPException(status_code=503, detail="Agent not ready")
    return _AGENT


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health(_: None = Depends(_verify_token)):
    ag = _get_agent()
    return {"status": "ok", "mode": ag.mode, "turn_number": ag.turn_number}


# ── T-304: memory dashboard (read-only) ───────────────────────────────────────

@app.get("/memory", response_class=HTMLResponse, include_in_schema=False)
async def serve_memory_dashboard():
    page = _WEB_DIR / "memory.html"
    if page.exists():
        return HTMLResponse(page.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Pi Memory</h1><p>web/memory.html not found.</p>")


@app.get("/memory/state")
async def memory_state(_: None = Depends(_verify_token)):
    """Hot L3 rows (importance-ordered) + forgetting counts for the last week."""
    ag = _get_agent()
    rows = ag.memory.memory_read("", tier="l3", limit=12) or []
    counts = {"EXPIRED": 0, "CONTRADICTED": 0, "MERGED": 0}
    for entry in ag.memory.forgotten_ledger(days=7):
        counts[entry["reason"]] = counts.get(entry["reason"], 0) + 1
    return {"l3": rows, "forgotten_counts": counts}


@app.get("/memory/retrieve")
async def memory_retrieve(q: str, _: None = Depends(_verify_token)):
    """Live hybrid retrieval (dense cosine + BM25 fusion) with fused scores."""
    ag = _get_agent()
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must not be empty")
    hits = ag.memory.retrieve(q, k=8) or []
    return {"query": q, "hits": hits}


@app.get("/memory/forgotten")
async def memory_forgotten(days: int = 7, _: None = Depends(_verify_token)):
    """The forgetting ledger — what died, when, and why."""
    ag = _get_agent()
    return {"days": days, "forgotten": ag.memory.forgotten_ledger(days=days)}


@app.get("/conversations")
async def list_conversations(_: None = Depends(_verify_token)):
    ag = _get_agent()
    convs = ag.memory.list_conversations(limit=20)
    return {"conversations": convs}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _: None = Depends(_verify_token)):
    ag = _get_agent()
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    async with _TURN_LOCK:
        # Switch conversation context if needed
        if req.conversation_id and req.conversation_id != ag.conversation_id:
            turns = ag.memory.load_conversation_turns(req.conversation_id, max_turns=40)
            # Rebuild messages from stored turns
            from agent.truncation import truncate_messages_safely as _tms
            ag.messages = _tms(turns, max_messages=20) if turns else []
            ag.conversation_id = req.conversation_id

        # Run the turn in a thread pool (process_input is blocking)
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, ag.process_input, req.text)
        return ChatResponse(
            conversation_id=ag.conversation_id,
            response=response or "",
            mode=ag.mode,
        )


@app.get("/chat/stream")
async def chat_stream(text: str, conversation_id: Optional[str] = None,
                      _: None = Depends(_verify_token)):
    """SSE endpoint: GET /chat/stream?text=hello&conversation_id=abc.

    Streams tokens as `data: <chunk>\n\n` events, followed by a final
    `data: [DONE]\n\n` sentinel.  Falls back to a single chunk if the
    provider does not support streaming.
    """
    ag = _get_agent()
    if not text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

    async def _produce():
        async with _TURN_LOCK:
            if conversation_id and conversation_id != ag.conversation_id:
                turns = ag.memory.load_conversation_turns(conversation_id, max_turns=40)
                from agent.truncation import truncate_messages_safely as _tms
                ag.messages = _tms(turns, max_messages=20) if turns else []
                ag.conversation_id = conversation_id

            loop = asyncio.get_running_loop()
            chunks: list[str] = []

            def _on_delta(chunk: str):
                chunks.append(chunk)
                asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)

            try:
                # Attempt streaming; falls back silently if unsupported.
                await loop.run_in_executor(
                    None,
                    lambda: ag.router.chat(
                        ag.messages + [{"role": "user", "content": text}],
                        ag._build_system_prompt(),
                        [],
                        4096,
                        on_delta=_on_delta,
                    ),
                )
            except Exception:
                # Fallback: run a normal turn and push result as single chunk.
                resp = await loop.run_in_executor(None, ag.process_input, text)
                await queue.put(resp or "")
            finally:
                await queue.put(None)  # sentinel

    asyncio.create_task(_produce())

    async def _stream() -> AsyncIterator[str]:
        while True:
            chunk = await queue.get()
            if chunk is None:
                yield "data: [DONE]\n\n"
                break
            safe = chunk.replace("\n", " ")
            yield f"data: {safe}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")
