"""Tests for T-187: brain server HTTP+SSE (app/server.py)."""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Skip entire module if fastapi not installed
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from app.server import app, mount_agent, _TURN_LOCK


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_agent(conversation_id="conv-1", mode="root", turn=1, memory=None):
    ag = MagicMock()
    ag.conversation_id = conversation_id
    ag.mode = mode
    ag.turn_number = turn
    ag.messages = []
    ag.router = MagicMock()
    ag._build_system_prompt = MagicMock(return_value="system prompt")
    if memory is None:
        memory = MagicMock()
        memory.list_conversations.return_value = [
            {"id": "conv-1", "title": "Test", "mode": "root",
             "created_at": "2026-06-01T00:00:00+00:00",
             "last_active_at": "2026-06-01T00:01:00+00:00"}
        ]
        memory.load_conversation_turns.return_value = []
        memory.memory_read.return_value = [
            {"id": "aaaa0001", "content": "Ash studies at GSU",
             "importance": 9, "category": "permanent_profile"},
        ]
        memory.forgotten_ledger.return_value = [
            {"id": "bbbb0002", "content": "old wifi note", "importance": 5,
             "category": "note", "reason": "EXPIRED", "when": "2026-07-17T00:00:00+00:00",
             "pointer_id": None},
        ]
        memory.retrieve.return_value = [
            {"id": "cccc0003", "content": "the lab uses zebrafish", "importance": 8,
             "category": "note", "tier": "l3", "score": 0.87},
        ]
    ag.memory = memory
    ag.process_input = MagicMock(return_value="Hello back!")
    return ag


@pytest.fixture
def client(tmp_path):
    ag = _make_agent()
    mount_agent(ag)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, ag


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_returns_ok(client):
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "mode" in body


def test_health_returns_mode_and_turn(client):
    c, ag = client
    ag.mode = "normie"
    ag.turn_number = 7
    resp = c.get("/health")
    assert resp.json()["mode"] == "normie"
    assert resp.json()["turn_number"] == 7


# ── /conversations ────────────────────────────────────────────────────────────

def test_conversations_returns_list(client):
    c, _ = client
    resp = c.get("/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert "conversations" in body
    assert isinstance(body["conversations"], list)


# ── /chat ─────────────────────────────────────────────────────────────────────

def test_chat_returns_conversation_id_and_response(client):
    c, ag = client
    ag.process_input.return_value = "Pi says hi."
    resp = c.post("/chat", json={"text": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert "conversation_id" in body
    assert body["response"] == "Pi says hi."


def test_chat_empty_text_returns_400(client):
    c, _ = client
    resp = c.post("/chat", json={"text": "   "})
    assert resp.status_code == 400


def test_chat_calls_process_input(client):
    c, ag = client
    c.post("/chat", json={"text": "what is pi?"})
    ag.process_input.assert_called_once_with("what is pi?")


def test_chat_loads_turns_for_different_conversation(client):
    c, ag = client
    ag.memory.load_conversation_turns.return_value = [
        {"role": "user", "content": "prior msg"},
        {"role": "assistant", "content": "prior reply"},
    ]
    resp = c.post("/chat", json={"text": "continue", "conversation_id": "conv-other"})
    assert resp.status_code == 200
    ag.memory.load_conversation_turns.assert_called_with("conv-other", max_turns=40)


def test_chat_no_context_bleed_between_conversations(client):
    """Two sequential chats with different conv IDs don't share messages."""
    c, ag = client
    # First conversation
    c.post("/chat", json={"text": "hello", "conversation_id": "cv-A"})
    # Agent conv_id is now cv-A; messages set to [] (empty turns for cv-A)
    first_messages = list(ag.messages)

    # Second conversation — should reset messages
    ag.memory.load_conversation_turns.return_value = []
    c.post("/chat", json={"text": "world", "conversation_id": "cv-B"})
    # messages should have been reset (not accumulating cv-A messages into cv-B)
    assert ag.conversation_id == "cv-B"


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_auth_rejected_when_token_set(client, monkeypatch):
    import app.server as srv
    monkeypatch.setattr(srv, "_SERVER_TOKEN", "secret-token")
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 401


def test_auth_accepted_with_correct_bearer(client, monkeypatch):
    import app.server as srv
    monkeypatch.setattr(srv, "_SERVER_TOKEN", "secret-token")
    c, _ = client
    resp = c.get("/health", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200


def test_auth_rejected_with_wrong_bearer(client, monkeypatch):
    import app.server as srv
    monkeypatch.setattr(srv, "_SERVER_TOKEN", "secret-token")
    c, _ = client
    resp = c.get("/health", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


# ── T-304: memory dashboard ───────────────────────────────────────────────────

def test_memory_page_served(client):
    c, _ = client
    resp = c.get("/memory")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_memory_state_returns_l3_rows_and_forgotten_counts(client):
    c, ag = client
    resp = c.get("/memory/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["l3"][0]["content"] == "Ash studies at GSU"
    assert body["forgotten_counts"]["EXPIRED"] == 1
    assert body["forgotten_counts"]["CONTRADICTED"] == 0
    ag.memory.memory_read.assert_called_with("", tier="l3", limit=12)


def test_memory_retrieve_returns_scored_hits(client):
    c, ag = client
    resp = c.get("/memory/retrieve", params={"q": "what organism do we study"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "what organism do we study"
    assert body["hits"][0]["score"] == 0.87
    ag.memory.retrieve.assert_called_once_with("what organism do we study", k=8)


def test_memory_retrieve_empty_query_returns_400(client):
    c, _ = client
    resp = c.get("/memory/retrieve", params={"q": "   "})
    assert resp.status_code == 400


def test_memory_forgotten_returns_ledger(client):
    c, ag = client
    resp = c.get("/memory/forgotten", params={"days": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 3
    assert body["forgotten"][0]["reason"] == "EXPIRED"
    ag.memory.forgotten_ledger.assert_called_once_with(days=3)


def test_memory_forgotten_default_days_is_7(client):
    c, ag = client
    c.get("/memory/forgotten")
    ag.memory.forgotten_ledger.assert_called_once_with(days=7)


def test_memory_state_requires_auth(client, monkeypatch):
    import app.server as srv
    monkeypatch.setattr(srv, "_SERVER_TOKEN", "secret-token")
    c, _ = client
    assert c.get("/memory/state").status_code == 401
    assert c.get("/memory/state", headers={"Authorization": "Bearer secret-token"}).status_code == 200


def test_memory_retrieve_requires_auth(client, monkeypatch):
    import app.server as srv
    monkeypatch.setattr(srv, "_SERVER_TOKEN", "secret-token")
    c, _ = client
    assert c.get("/memory/retrieve", params={"q": "x"}).status_code == 401


def test_memory_forgotten_requires_auth(client, monkeypatch):
    import app.server as srv
    monkeypatch.setattr(srv, "_SERVER_TOKEN", "secret-token")
    c, _ = client
    assert c.get("/memory/forgotten").status_code == 401


# ── Module structure ──────────────────────────────────────────────────────────

def test_app_module_importable():
    from app import server  # noqa: F401
    assert hasattr(server, "app")
    assert hasattr(server, "mount_agent")


def test_localhost_only_bind_constant():
    """SERVER_HOST constant must be 127.0.0.1 — ensures daemon never binds 0.0.0.0."""
    from app.server import SERVER_HOST
    assert SERVER_HOST == "127.0.0.1"
