"""
testing/test_awareness.py — Offline unit tests for tools/tools_awareness.py.

All HTTP calls are mocked. No network access, no API keys required.

Run:  python -m pytest testing/test_awareness.py -v
"""
import json
import time
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_at():
    from tools.tools_awareness import AwarenessTools
    return AwarenessTools(openweather_key="test-key")


def _mock_response(data, status=200, text=None):
    r = MagicMock()
    r.status_code = status
    r.raise_for_status = MagicMock()
    if text is not None:
        r.text = text
        r.content = text.encode()
    else:
        r.text = json.dumps(data)
        r.content = json.dumps(data).encode()
    r.json = MagicMock(return_value=data)
    return r


# ── get_time ──────────────────────────────────────────────────────────────────

def test_get_time_returns_utc_string():
    at = _make_at()
    result = at.get_time()
    assert result["success"] is True
    assert "UTC" in result["utc"]
    assert "iso" in result


def test_get_time_always_live():
    at = _make_at()
    t1 = at.get_time()["utc"]
    t2 = at.get_time()["utc"]
    assert t1[:16] == t2[:16]   # same minute is fine; not cached


# ── get_location ──────────────────────────────────────────────────────────────

def test_get_location_success():
    at = _make_at()
    payload = {
        "city": "Atlanta", "regionName": "Georgia",
        "country": "United States", "lat": 33.7, "lon": -84.4,
        "timezone": "America/New_York",
    }
    with patch("tools.tools_awareness.requests.get",
               return_value=_mock_response(payload)):
        result = at.get_location()
    assert result["success"] is True
    assert result["city"] == "Atlanta"
    assert result["country"] == "United States"


def test_get_location_cached_on_second_call():
    at = _make_at()
    payload = {"city": "Atlanta", "regionName": "Georgia",
               "country": "US", "lat": 33.7, "lon": -84.4, "timezone": "ET"}
    with patch("tools.tools_awareness.requests.get",
               return_value=_mock_response(payload)) as mock_get:
        at.get_location()
        at.get_location()          # second call should use cache
    assert mock_get.call_count == 1


def test_get_location_failure():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               side_effect=Exception("timeout")):
        result = at.get_location()
    assert result["success"] is False
    assert "error" in result


# ── get_weather ───────────────────────────────────────────────────────────────

_WTTR_PAYLOAD = {
    "current_condition": [{
        "temp_C": "22", "temp_F": "72", "humidity": "60",
        "weatherDesc": [{"value": "Partly cloudy"}],
        "windspeedKmph": "14", "FeelsLikeC": "21", "uvIndex": "5",
        "visibility": "10",
    }],
    "nearest_area": [{
        "areaName": [{"value": "Atlanta"}],
        "country": [{"value": "United States of America"}],
    }],
}


def test_get_weather_wttr_success():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               return_value=_mock_response(_WTTR_PAYLOAD)):
        result = at.get_weather("Atlanta,US")
    assert result["success"] is True
    assert result["temp_c"] == "22"
    assert result["description"] == "Partly cloudy"
    assert result["humidity"] == "60"


def test_get_weather_falls_back_to_openweather_on_wttr_fail():
    at = _make_at()
    ow_payload = {
        "name": "Atlanta",
        "sys": {"country": "US"},
        "weather": [{"description": "clear sky"}],
        "main": {"temp": 24.0, "feels_like": 23.0, "humidity": 55},
        "wind": {"speed": 3.5},
        "visibility": 10000,
    }

    call_count = [0]

    def side_effect(url, **kwargs):
        call_count[0] += 1
        if "wttr.in" in url:
            raise Exception("wttr down")
        return _mock_response(ow_payload)

    with patch("tools.tools_awareness.requests.get", side_effect=side_effect):
        result = at.get_weather("Atlanta,US", force=True)

    assert result["success"] is True
    assert result["description"] == "Clear Sky"


def test_get_weather_both_fail():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               side_effect=Exception("all down")):
        result = at.get_weather("Nowhere")
    assert result["success"] is False


# ── get_news ──────────────────────────────────────────────────────────────────

_BBC_RSS = """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Breaking: Major Event Happens</title>
    <link>https://bbc.co.uk/news/1</link>
    <description>A major event occurred today...</description>
    <pubDate>Sun, 03 May 2026 18:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Second Headline Here</title>
    <link>https://bbc.co.uk/news/2</link>
    <description>Details about the second story.</description>
    <pubDate>Sun, 03 May 2026 17:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


def test_get_news_parses_rss():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               return_value=_mock_response({}, text=_BBC_RSS)):
        result = at.get_news("global", count=5)
    assert result["success"] is True
    assert len(result["articles"]) == 2
    assert result["articles"][0]["title"] == "Breaking: Major Event Happens"


def test_get_news_cached():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               return_value=_mock_response({}, text=_BBC_RSS)) as mock_get:
        at.get_news("global")
        at.get_news("global")
    assert mock_get.call_count == 1


def test_get_news_failure():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               side_effect=Exception("no network")):
        result = at.get_news("global")
    assert result["success"] is False
    assert result["articles"] == []


def test_get_news_unknown_category_defaults_to_global():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               return_value=_mock_response({}, text=_BBC_RSS)):
        result = at.get_news("INVALID_CAT")
    assert result["category"] == "global"


# ── get_stocks ────────────────────────────────────────────────────────────────

_YF_PAYLOAD = {
    "quoteResponse": {
        "result": [
            {"symbol": "AAPL", "shortName": "Apple Inc.",
             "regularMarketPrice": 189.42, "regularMarketChange": 2.1,
             "regularMarketChangePercent": 1.12, "currency": "USD"},
            {"symbol": "BTC-USD", "shortName": "Bitcoin USD",
             "regularMarketPrice": 63100.0, "regularMarketChange": 1200.0,
             "regularMarketChangePercent": 1.94, "currency": "USD"},
        ]
    }
}


def test_get_stocks_success():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               return_value=_mock_response(_YF_PAYLOAD)):
        result = at.get_stocks(["AAPL", "BTC-USD"])
    assert result["success"] is True
    assert len(result["quotes"]) == 2
    assert result["quotes"][0]["symbol"] == "AAPL"
    assert result["quotes"][0]["price"] == 189.42
    assert result["quotes"][1]["change_pct"] == pytest.approx(1.94, abs=0.01)


def test_get_stocks_cached():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               return_value=_mock_response(_YF_PAYLOAD)) as mock_get:
        at.get_stocks(["AAPL"])
        at.get_stocks(["AAPL"])
    assert mock_get.call_count == 1


def test_get_stocks_failure():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               side_effect=Exception("Yahoo down")):
        result = at.get_stocks()
    assert result["success"] is False
    assert result["quotes"] == []


# ── get_tech_updates ──────────────────────────────────────────────────────────

_HN_PAYLOAD = {
    "hits": [
        {"title": "LLM beats human at coding", "url": "https://example.com",
         "points": 450, "num_comments": 200},
        {"title": "New Rust framework released", "url": "https://rust.example",
         "points": 300, "num_comments": 150},
    ]
}

_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Attention Is All You Need (Again)</title>
    <summary>We revisit the transformer architecture...</summary>
    <link rel="alternate" href="https://arxiv.org/abs/2606.00001"/>
    <category term="cs.AI"/>
    <category term="cs.LG"/>
  </entry>
  <entry>
    <title>LoRA: Low-Rank Adaptation Revisited</title>
    <summary>We propose a new fine-tuning method...</summary>
    <link rel="alternate" href="https://arxiv.org/abs/2606.00002"/>
    <category term="cs.CL"/>
  </entry>
</feed>"""


def test_get_tech_updates_hn():
    at = _make_at()

    def side_effect(url, **kwargs):
        if "algolia" in url:
            return _mock_response(_HN_PAYLOAD)
        return _mock_response({}, text=_ARXIV_XML)

    with patch("tools.tools_awareness.requests.get", side_effect=side_effect):
        result = at.get_tech_updates(count=5)

    assert result["success"] is True
    assert len(result["hn_stories"]) == 2
    assert result["hn_stories"][0]["title"] == "LLM beats human at coding"
    assert result["hn_stories"][0]["points"] == 450


def test_get_tech_updates_arxiv():
    at = _make_at()

    def side_effect(url, **kwargs):
        if "algolia" in url:
            return _mock_response({"hits": []})
        return _mock_response({}, text=_ARXIV_XML)

    with patch("tools.tools_awareness.requests.get", side_effect=side_effect):
        result = at.get_tech_updates(count=5)

    assert len(result["arxiv_papers"]) == 2
    assert "Attention" in result["arxiv_papers"][0]["title"]
    assert "cs.AI" in result["arxiv_papers"][0]["categories"]


def test_get_tech_updates_failure_returns_empty():
    at = _make_at()
    with patch("tools.tools_awareness.requests.get",
               side_effect=Exception("no internet")):
        result = at.get_tech_updates()
    assert result["hn_stories"] == []
    assert result["arxiv_papers"] == []


# ── get_awareness_snapshot ────────────────────────────────────────────────────

def test_snapshot_contains_key_sections():
    at = _make_at()

    def fake_get(url, **kwargs):
        if "ip-api" in url:
            return _mock_response({"city": "Atlanta", "regionName": "GA",
                                   "country": "US", "lat": 33.7, "lon": -84.4,
                                   "timezone": "ET"})
        if "wttr.in" in url:
            return _mock_response(_WTTR_PAYLOAD)
        if "yahoo" in url:
            return _mock_response(_YF_PAYLOAD)
        if "algolia" in url:
            return _mock_response(_HN_PAYLOAD)
        if "arxiv" in url:
            return _mock_response({}, text=_ARXIV_XML)
        # RSS feeds
        return _mock_response({}, text=_BBC_RSS)

    with patch("tools.tools_awareness.requests.get", side_effect=fake_get):
        snap = at.get_awareness_snapshot()

    assert "LIVE AWARENESS" in snap
    assert "Time:" in snap
    assert "Weather:" in snap
    assert "Markets:" in snap
    assert "AAPL" in snap


def test_snapshot_cached_on_second_call():
    at = _make_at()

    def fake_get(url, **kwargs):
        if "ip-api" in url:
            return _mock_response({"city": "A", "regionName": "", "country": "",
                                   "lat": 0, "lon": 0, "timezone": ""})
        if "wttr.in" in url:
            return _mock_response(_WTTR_PAYLOAD)
        if "yahoo" in url:
            return _mock_response(_YF_PAYLOAD)
        if "algolia" in url:
            return _mock_response(_HN_PAYLOAD)
        if "arxiv" in url:
            return _mock_response({}, text=_ARXIV_XML)
        return _mock_response({}, text=_BBC_RSS)

    with patch("tools.tools_awareness.requests.get",
               side_effect=fake_get) as mock_get:
        at.get_awareness_snapshot()
        first_call_count = mock_get.call_count
        at.get_awareness_snapshot()           # should use cache
        assert mock_get.call_count == first_call_count


def test_snapshot_force_bypasses_cache():
    at = _make_at()

    call_counts = [0]

    def fake_get(url, **kwargs):
        call_counts[0] += 1
        if "ip-api" in url:
            return _mock_response({"city": "A", "regionName": "", "country": "",
                                   "lat": 0, "lon": 0, "timezone": ""})
        if "wttr.in" in url:
            return _mock_response(_WTTR_PAYLOAD)
        if "yahoo" in url:
            return _mock_response(_YF_PAYLOAD)
        if "algolia" in url:
            return _mock_response(_HN_PAYLOAD)
        if "arxiv" in url:
            return _mock_response({}, text=_ARXIV_XML)
        return _mock_response({}, text=_BBC_RSS)

    with patch("tools.tools_awareness.requests.get", side_effect=fake_get):
        at.get_awareness_snapshot()
        after_first = call_counts[0]
        at.get_awareness_snapshot(force=True)
        assert call_counts[0] > after_first


# ── helper functions ──────────────────────────────────────────────────────────

def test_clean_html_strips_tags():
    from tools.tools_awareness import _clean_html
    assert _clean_html("<b>Hello</b> &amp; <i>World</i>") == "Hello & World"


def test_first_extracts_value():
    from tools.tools_awareness import _first
    assert _first([{"value": "Atlanta"}], "default") == "Atlanta"
    assert _first([], "default") == "default"
    assert _first(None, "fallback") == "fallback"
