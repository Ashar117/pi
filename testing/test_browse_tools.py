"""Tests for tools/tools_browse.py."""

import os
import pytest
from unittest.mock import patch, MagicMock
from tools.tools_browse import BrowseTools


# ── web_browse (trafilatura) ───────────────────────────────────────────────────

def test_web_browse_success_html():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.text = "<html><body><h1>Hello World</h1><p>Some content here.</p></body></html>"
    mock_resp.raise_for_status = MagicMock()

    # trafilatura may not extract from minimal HTML, so allow fallback path
    with patch("tools.tools_browse.requests.get", return_value=mock_resp):
        r = BrowseTools.fetch("https://example.com")
    assert r["success"] is True
    assert "content" in r


def test_web_browse_network_error():
    import requests
    # Mock both trafilatura.fetch_url and requests.get so all paths fail
    with patch("tools.tools_browse.trafilatura.fetch_url", return_value=None):
        with patch("tools.tools_browse.requests.get", side_effect=requests.ConnectionError("timeout")):
            r = BrowseTools.fetch("https://example.com")
    assert r["success"] is False


# ── Reddit (public JSON API) ───────────────────────────────────────────────────

def test_reddit_browse_public_json():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "children": [
                {"data": {
                    "title": "Test ML post", "score": 500, "num_comments": 42,
                    "url": "https://arxiv.org/abs/test", "permalink": "/r/MachineLearning/comments/test/",
                    "link_flair_text": "Research", "author": "researcher", "selftext": ""
                }}
            ]
        }
    }
    # No PRAW credentials → falls through to public JSON
    with patch.dict(os.environ, {}, clear=True):
        with patch("tools.tools_browse.requests.get", return_value=mock_resp):
            r = BrowseTools.reddit_browse("MachineLearning", count=1)
    assert r["success"] is True
    assert r["count"] == 1
    assert r["posts"][0]["title"] == "Test ML post"


def test_reddit_browse_network_error():
    import requests
    with patch.dict(os.environ, {}, clear=True):
        with patch("tools.tools_browse.requests.get", side_effect=requests.ConnectionError("down")):
            r = BrowseTools.reddit_browse("test")
    assert r["success"] is False


def test_reddit_search_public_json():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "children": [
                {"data": {
                    "title": "GNN paper discussion", "score": 200, "subreddit": "MachineLearning",
                    "num_comments": 15, "permalink": "/r/MachineLearning/comments/abc/", "selftext": ""
                }}
            ]
        }
    }
    with patch.dict(os.environ, {}, clear=True):
        with patch("tools.tools_browse.requests.get", return_value=mock_resp):
            r = BrowseTools.reddit_search("graph neural network")
    assert r["success"] is True
    assert r["posts"][0]["subreddit"] == "MachineLearning"


# ── Scholar / ArXiv fallback ───────────────────────────────────────────────────

def test_scholar_arxiv_fallback():
    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://arxiv.org/abs/1706.03762</id>
        <title>Attention Is All You Need</title>
        <summary>The dominant sequence transduction models.</summary>
        <author><name>Ashish Vaswani</name></author>
        <link href="https://arxiv.org/pdf/1706.03762v5" title="pdf" type="application/pdf"/>
      </entry>
    </feed>"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = sample_xml
    mock_resp.raise_for_status = MagicMock()

    with patch("tools.tools_browse.requests.get", return_value=mock_resp):
        r = BrowseTools._arxiv_search("attention transformer", 5)
    assert r["success"] is True
    assert r["papers"][0]["title"] == "Attention Is All You Need"
    assert "Vaswani" in r["papers"][0]["authors"]
    assert "1706.03762" in r["papers"][0]["pdf_url"]


# ── Discord ────────────────────────────────────────────────────────────────────

def test_discord_read_no_token():
    env = {k: v for k, v in os.environ.items() if k != "DISCORD_BOT_TOKEN"}
    with patch.dict(os.environ, env, clear=True):
        r = BrowseTools.discord_read("123456789")
    assert r["success"] is False
    assert "DISCORD_BOT_TOKEN" in r["error"]


def test_discord_read_invalid_token():
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.raise_for_status = MagicMock(side_effect=Exception("401"))
    with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "bad_token"}):
        with patch("tools.tools_browse.requests.get", return_value=mock_resp):
            r = BrowseTools.discord_read("123456789")
    assert r["success"] is False


def test_discord_read_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {"id": "1", "author": {"username": "ash"}, "content": "hello pi",
         "timestamp": "2026-05-05T00:00:00Z", "attachments": []}
    ]
    with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "valid_token"}):
        with patch("tools.tools_browse.requests.get", return_value=mock_resp):
            r = BrowseTools.discord_read("123456789", count=1)
    assert r["success"] is True
    assert r["messages"][0]["author"] == "ash"
    assert r["messages"][0]["content"] == "hello pi"
