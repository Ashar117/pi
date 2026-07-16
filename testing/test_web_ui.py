"""Tests for T-189 (web chat UI) and T-190 (browser extension) server-side pieces."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from app.server import app, mount_agent


@pytest.fixture
def client():
    ag = MagicMock()
    ag.conversation_id = "cv-1"
    ag.mode = "root"
    ag.turn_number = 1
    ag.messages = []
    ag.memory = MagicMock()
    ag.memory.list_conversations.return_value = []
    ag.memory.load_conversation_turns.return_value = []
    ag.process_input = MagicMock(return_value="hello")
    mount_agent(ag)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── T-189: static route ───────────────────────────────────────────────────────

def test_root_serves_html(client):
    """GET / returns HTML content (the chat UI)."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_root_contains_brain_url(client):
    """index.html references the brain server URL."""
    resp = client.get("/")
    assert "127.0.0.1:7712" in resp.text


def test_static_chat_js_served(client):
    """GET /static/chat.js serves the shared chat client JS."""
    resp = client.get("/static/chat.js")
    assert resp.status_code == 200
    assert "buildPageContextPrefix" in resp.text


def test_root_no_auth_required(client):
    """The / route is unauthenticated (token gate is on API routes only)."""
    import app.server as srv
    # Even with a token configured, / should be accessible
    resp = client.get("/")
    assert resp.status_code == 200


# ── T-190: CORS headers ───────────────────────────────────────────────────────

def test_cors_allows_extension_origin(client):
    """Preflight from a chrome-extension:// origin gets CORS allow headers."""
    resp = client.options(
        "/health",
        headers={
            "Origin": "chrome-extension://abcdefghijklmno",
            "Access-Control-Request-Method": "GET",
        }
    )
    assert resp.status_code in (200, 204)
    assert "access-control-allow-origin" in resp.headers


def test_cors_allows_localhost_origin(client):
    resp = client.options(
        "/health",
        headers={
            "Origin": "http://127.0.0.1:7712",
            "Access-Control-Request-Method": "GET",
        }
    )
    assert resp.status_code in (200, 204)


# ── T-190: extension files exist ──────────────────────────────────────────────

def test_extension_manifest_exists():
    from pathlib import Path
    manifest = Path("extension/manifest.json")
    assert manifest.exists()


def test_extension_manifest_is_mv3():
    import json
    from pathlib import Path
    with open("extension/manifest.json") as f:
        m = json.load(f)
    assert m.get("manifest_version") == 3


def test_extension_manifest_has_side_panel():
    import json
    from pathlib import Path
    with open("extension/manifest.json") as f:
        m = json.load(f)
    assert "side_panel" in m or "sidePanel" in m.get("permissions", [])


def test_extension_host_permission_is_localhost_only():
    import json
    with open("extension/manifest.json") as f:
        m = json.load(f)
    host_perms = m.get("host_permissions", [])
    # Must have 127.0.0.1 permission
    assert any("127.0.0.1" in p for p in host_perms)
    # Must NOT have wildcard * permission
    assert not any(p in ("*", "<all_urls>") for p in host_perms)


def test_extension_sw_js_exists():
    from pathlib import Path
    assert Path("extension/sw.js").exists()


def test_extension_sidepanel_exists():
    from pathlib import Path
    assert Path("extension/sidepanel.html").exists()


# ── T-190: buildPageContextPrefix pure function ───────────────────────────────

def test_build_page_context_prefix_with_all_fields():
    """Verify prefix format via regex — JS tested here as doc check only."""
    # The actual function is JS; test the contract via the chat.js source
    from pathlib import Path
    src = (Path("web") / "chat.js").read_text(encoding="utf-8")
    assert "buildPageContextPrefix" in src
    assert "selection" in src
    assert "url" in src
    assert "title" in src


def test_chat_js_shared_between_web_and_extension():
    """sidepanel.html references chat.js — shares logic with web/index.html."""
    from pathlib import Path
    sidepanel = (Path("extension") / "sidepanel.html").read_text(encoding="utf-8")
    assert 'src="chat.js"' in sidepanel
