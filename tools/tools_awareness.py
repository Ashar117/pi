"""
tools/tools_awareness.py — Live world awareness for Pi.

Every data category has a PRIMARY source and one or more BACKUP sources.
Sources are tried in waterfall order — first success wins.
All failures are caught; if every source fails the section is omitted
from the snapshot rather than crashing Pi.

Source map (all free — keys optional for higher limits):
┌──────────┬──────────────────────────┬─────────────────────────────────────┐
│ Category │ Primary (no key)         │ Backups                             │
├──────────┼──────────────────────────┼─────────────────────────────────────┤
│ Location │ ip-api.com               │ ipapi.co → ipinfo.io                │
│ Weather  │ wttr.in                  │ Open-Meteo → OpenWeatherMap (key)   │
│ Global   │ BBC RSS                  │ Reuters RSS → AP RSS → NewsAPI(key) │
│ Tech     │ HN RSS (hnrss.org)       │ TechCrunch RSS → The Verge RSS      │
│ AI news  │ HN RSS (AI filter)       │ MIT Tech Review RSS                 │
│ Stocks   │ Yahoo Finance v7/query1  │ Yahoo Finance v7/query2 →           │
│          │                          │ CoinGecko (crypto) → AlphaVantage   │
│ HN top   │ HN Algolia API           │ HN Firebase API                     │
│ Research │ ArXiv API                │ Papers With Code API                │
└──────────┴──────────────────────────┴─────────────────────────────────────┘

Optional env vars (graceful no-ops if absent):
  OPENWEATHER_API_KEY  — weather backup
  ALPHA_VANTAGE_KEY    — stocks backup (25 req/day free at alphavantage.co)
  NEWS_API_KEY         — news backup  (100 req/day free at newsapi.org)
"""

import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote as url_quote

import requests

# ── Constants ─────────────────────────────────────────────────────────────────

_TIMEOUT = 8
_HEADERS = {"User-Agent": "Pi-Agent/2.0 (personal assistant)"}

_NEWS_FEEDS = {
    "global":   ["http://feeds.bbci.co.uk/news/rss.xml",
                 "https://feeds.reuters.com/reuters/topNews",
                 "https://feeds.apnews.com/apnews/topnews"],
    "tech":     ["https://hnrss.org/frontpage?points=50",
                 "https://techcrunch.com/feed/",
                 "https://www.theverge.com/rss/index.xml"],
    "business": ["https://feeds.reuters.com/reuters/businessNews",
                 "https://feeds.bbci.co.uk/news/business/rss.xml"],
    "science":  ["https://feeds.reuters.com/reuters/scienceNews",
                 "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"],
    "ai":       ["https://hnrss.org/newest?q=AI+LLM+machine+learning&points=20",
                 "https://feeds.feedburner.com/mit-technology-review"],
}

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "SPY", "BTC-USD", "ETH-USD"]

# CoinGecko IDs for crypto symbols (for backup path)
CRYPTO_IDS = {
    "BTC-USD":  "bitcoin",
    "ETH-USD":  "ethereum",
    "SOL-USD":  "solana",
    "BNB-USD":  "binancecoin",
    "ADA-USD":  "cardano",
    "XRP-USD":  "ripple",
    "DOGE-USD": "dogecoin",
}

# Open-Meteo WMO weather codes
_WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Rain showers", 81: "Showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Thunderstorm + hail",
}

_NEWS_TTL   = 900
_STOCKS_TTL = 300
_GEO_TTL    = 86400
_WEATHER_TTL = 1800
_TECH_TTL   = 1800
_SNAP_TTL   = 1800


# ── AwarenessTools ────────────────────────────────────────────────────────────

class AwarenessTools:
    """Multi-source live awareness with automatic waterfall failover."""

    def __init__(self, openweather_key: str = "", alpha_vantage_key: str = "",
                 news_api_key: str = ""):
        self._cache: Dict[str, tuple] = {}
        self._ow_key  = openweather_key
        self._av_key  = alpha_vantage_key
        self._news_key = news_api_key

    # ── cache ─────────────────────────────────────────────────────────────────

    def _fresh(self, key: str, ttl: int) -> bool:
        if key not in self._cache:
            return False
        _, ts = self._cache[key]
        return (time.monotonic() - ts) < ttl

    def _read(self, key: str):
        return self._cache[key][0]

    def _write(self, key: str, data):
        self._cache[key] = (data, time.monotonic())
        return data

    def _waterfall(self, sources: List[Callable], *args, **kwargs) -> Dict:
        """Try each source in order; return first result with success=True."""
        last_err = "no sources"
        for fn in sources:
            try:
                result = fn(*args, **kwargs)
                if result.get("success"):
                    return result
                last_err = result.get("error", "returned success=False")
            except Exception as e:
                last_err = str(e)
        return {"success": False, "error": f"All sources failed — last: {last_err}"}

    # ── time ─────────────────────────────────────────────────────────────────

    def get_time(self) -> Dict:
        now = datetime.now(timezone.utc)
        return {"utc": now.strftime("%A, %Y-%m-%d %H:%M UTC"),
                "iso": now.isoformat(), "success": True}

    # ── location ─────────────────────────────────────────────────────────────

    def get_location(self, force: bool = False) -> Dict:
        key = "location"
        if not force and self._fresh(key, _GEO_TTL):
            return self._read(key)
        result = self._waterfall([
            self._loc_ipapi,
            self._loc_ipapiCo,
            self._loc_ipinfo,
        ])
        return self._write(key, result)

    def _loc_ipapi(self) -> Dict:
        r = requests.get("http://ip-api.com/json/", timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        d = r.json()
        if d.get("status") == "fail":
            return {"success": False, "error": d.get("message", "ip-api failed")}
        return {"city": d.get("city",""), "region": d.get("regionName",""),
                "country": d.get("country",""), "lat": d.get("lat",0.0),
                "lon": d.get("lon",0.0), "timezone": d.get("timezone",""),
                "source": "ip-api.com", "success": True}

    def _loc_ipapiCo(self) -> Dict:
        r = requests.get("https://ipapi.co/json/", timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        d = r.json()
        if "error" in d:
            return {"success": False, "error": d["reason"]}
        return {"city": d.get("city",""), "region": d.get("region",""),
                "country": d.get("country_name",""), "lat": d.get("latitude",0.0),
                "lon": d.get("longitude",0.0), "timezone": d.get("timezone",""),
                "source": "ipapi.co", "success": True}

    def _loc_ipinfo(self) -> Dict:
        r = requests.get("https://ipinfo.io/json", timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        d = r.json()
        lat, lon = 0.0, 0.0
        if "loc" in d:
            parts = d["loc"].split(",")
            if len(parts) == 2:
                lat, lon = float(parts[0]), float(parts[1])
        return {"city": d.get("city",""), "region": d.get("region",""),
                "country": d.get("country",""), "lat": lat, "lon": lon,
                "timezone": d.get("timezone",""), "source": "ipinfo.io",
                "success": True}

    # ── weather ──────────────────────────────────────────────────────────────

    def get_weather(self, location: str = "", force: bool = False) -> Dict:
        """Current weather. Empty location = auto-detect from IP."""
        if not location:
            loc = self.get_location()
            if loc.get("city"):
                location = f"{loc['city']},{loc.get('country','')}"
            else:
                location = "auto"

        key = f"weather:{location.lower()}"
        if not force and self._fresh(key, _WEATHER_TTL):
            return self._read(key)

        sources = [
            lambda: self._wx_wttr(location),
            lambda: self._wx_openmeteo(location),
        ]
        if self._ow_key:
            sources.append(lambda: self._wx_openweather(location))
        result = self._waterfall(sources)
        return self._write(key, result)

    def _wx_wttr(self, location: str) -> Dict:
        r = requests.get(
            f"https://wttr.in/{url_quote(location)}",
            params={"format": "j1"},
            timeout=_TIMEOUT,
            headers={**_HEADERS, "Accept": "application/json"},
        )
        r.raise_for_status()
        d = r.json()
        cc   = d["current_condition"][0]
        area = d.get("nearest_area", [{}])[0]
        return {
            "location":     _first(area.get("areaName"), location),
            "country":      _first(area.get("country"), ""),
            "description":  _first(cc.get("weatherDesc"), ""),
            "temp_c":       cc.get("temp_C", "?"),
            "temp_f":       cc.get("temp_F", "?"),
            "feels_like_c": cc.get("FeelsLikeC", "?"),
            "humidity":     cc.get("humidity", "?"),
            "wind_kmph":    cc.get("windspeedKmph", "?"),
            "uv_index":     cc.get("uvIndex", "?"),
            "visibility_km": cc.get("visibility", "?"),
            "source": "wttr.in", "success": True,
        }

    def _wx_openmeteo(self, location: str) -> Dict:
        """Open-Meteo: free, no key, needs lat/lon from location lookup."""
        loc = self.get_location()
        lat, lon = loc.get("lat", 0), loc.get("lon", 0)
        if not lat and not lon:
            return {"success": False, "error": "no coordinates for Open-Meteo"}
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current": ("temperature_2m,relative_humidity_2m,"
                            "apparent_temperature,weather_code,"
                            "wind_speed_10m,uv_index"),
                "wind_speed_unit": "kmh",
            },
            timeout=_TIMEOUT,
            headers=_HEADERS,
        )
        r.raise_for_status()
        d   = r.json()
        cur = d.get("current", {})
        code = cur.get("weather_code", -1)
        desc = _WMO.get(code, f"Code {code}")
        tc   = cur.get("temperature_2m", "?")
        tf   = round(float(tc) * 9 / 5 + 32, 1) if isinstance(tc, (int, float)) else "?"
        return {
            "location":     loc.get("city", ""),
            "country":      loc.get("country", ""),
            "description":  desc,
            "temp_c":       str(tc),
            "temp_f":       str(tf),
            "feels_like_c": str(cur.get("apparent_temperature", "?")),
            "humidity":     str(cur.get("relative_humidity_2m", "?")),
            "wind_kmph":    str(cur.get("wind_speed_10m", "?")),
            "uv_index":     str(cur.get("uv_index", "?")),
            "visibility_km": "?",
            "source": "open-meteo.com", "success": True,
        }

    def _wx_openweather(self, location: str) -> Dict:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": location, "appid": self._ow_key, "units": "metric"},
            timeout=_TIMEOUT, headers=_HEADERS,
        )
        r.raise_for_status()
        d  = r.json()
        tc = d["main"]["temp"]
        return {
            "location":     d.get("name", location),
            "country":      d.get("sys", {}).get("country", ""),
            "description":  d["weather"][0]["description"].title(),
            "temp_c":       str(round(tc, 1)),
            "temp_f":       str(round(tc * 9 / 5 + 32, 1)),
            "feels_like_c": str(round(d["main"]["feels_like"], 1)),
            "humidity":     str(d["main"]["humidity"]),
            "wind_kmph":    str(round(d["wind"]["speed"] * 3.6, 1)),
            "uv_index":     "?",
            "visibility_km": str(round(d.get("visibility", 0) / 1000, 1)),
            "source": "openweathermap.org", "success": True,
        }

    # ── news ─────────────────────────────────────────────────────────────────

    def get_news(self, category: str = "global", count: int = 6,
                 force: bool = False) -> Dict:
        """Recent headlines — tries all RSS feeds for the category in order."""
        category = category if category in _NEWS_FEEDS else "global"
        key = f"news:{category}"
        if not force and self._fresh(key, _NEWS_TTL):
            return self._read(key)

        # Build waterfall: all RSS feeds for this category + NewsAPI if key present
        sources = [
            (lambda url: lambda: self._news_rss(url, count))(feed)
            for feed in _NEWS_FEEDS[category]
        ]
        if self._news_key:
            sources.append(lambda: self._news_newsapi(category, count))

        result = self._waterfall(sources)
        if result.get("success"):
            result["category"] = category
        else:
            result = {"success": False,
                      "error": result.get("error", "all news sources failed"),
                      "articles": [], "category": category}
        return self._write(key, result)

    def _news_rss(self, url: str, count: int) -> Dict:
        r = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS)
        r.raise_for_status()
        root     = ET.fromstring(r.content)
        items    = root.findall(".//item")
        articles = []
        for item in items[:count]:
            title = _text(item, "title")
            if title:
                articles.append({
                    "title":     title,
                    "url":       _text(item, "link"),
                    "snippet":   _clean_html(_text(item, "description"))[:150],
                    "published": _text(item, "pubDate"),
                })
        if not articles:
            return {"success": False, "error": f"no items in {url}"}
        return {"articles": articles, "source": url, "success": True}

    def _news_newsapi(self, category: str, count: int) -> Dict:
        cat_map = {"global": "general", "tech": "technology",
                   "business": "business", "science": "science", "ai": "technology"}
        r = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"category": cat_map.get(category, "general"),
                    "pageSize": count, "language": "en",
                    "apiKey": self._news_key},
            timeout=_TIMEOUT, headers=_HEADERS,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("status") != "ok":
            return {"success": False, "error": d.get("message", "NewsAPI error")}
        articles = [
            {"title": a["title"] or "", "url": a["url"] or "",
             "snippet": (a.get("description") or "")[:150],
             "published": a.get("publishedAt", "")}
            for a in d.get("articles", [])[:count]
            if a.get("title")
        ]
        return {"articles": articles, "source": "newsapi.org", "success": bool(articles)}

    # ── stocks ────────────────────────────────────────────────────────────────

    def get_stocks(self, symbols: Optional[List[str]] = None,
                   force: bool = False) -> Dict:
        """Live prices — Yahoo Finance → Yahoo query2 → CoinGecko + Alpha Vantage."""
        if symbols is None:
            symbols = DEFAULT_SYMBOLS
        key = f"stocks:{','.join(sorted(symbols))}"
        if not force and self._fresh(key, _STOCKS_TTL):
            return self._read(key)

        # Split equity vs crypto for targeted fallbacks
        crypto_syms = [s for s in symbols if s in CRYPTO_IDS]
        equity_syms = [s for s in symbols if s not in CRYPTO_IDS]

        # Try Yahoo v7 on two servers
        for base in ("https://query1.finance.yahoo.com",
                     "https://query2.finance.yahoo.com"):
            try:
                result = self._stocks_yahoo(symbols, base)
                if result.get("success"):
                    return self._write(key, result)
            except Exception:
                continue

        # Yahoo failed — assemble from specialist backups
        quotes: List[Dict] = []

        if crypto_syms:
            try:
                cg = self._stocks_coingecko(crypto_syms)
                if cg.get("success"):
                    quotes.extend(cg["quotes"])
            except Exception:
                pass

        if equity_syms and self._av_key:
            try:
                av = self._stocks_alphavantage(equity_syms[:5])  # respect 25/day limit
                if av.get("success"):
                    quotes.extend(av["quotes"])
            except Exception:
                pass

        if quotes:
            return self._write(key, {"quotes": quotes, "symbols": symbols,
                                     "success": True, "source": "mixed fallback"})

        return self._write(key, {"quotes": [], "symbols": symbols, "success": False,
                                 "error": "all stock sources failed"})

    def _stocks_yahoo(self, symbols: List[str], base: str) -> Dict:
        r = requests.get(
            f"{base}/v7/finance/quote",
            params={"symbols": ",".join(symbols)},
            headers={**_HEADERS, "User-Agent": "Mozilla/5.0"},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        raw = r.json().get("quoteResponse", {}).get("result", [])
        if not raw:
            return {"success": False, "error": "empty Yahoo response"}
        quotes = [
            {"symbol":     q.get("symbol", ""),
             "name":       q.get("shortName", ""),
             "price":      round(q.get("regularMarketPrice", 0), 2),
             "change":     round(q.get("regularMarketChange", 0), 2),
             "change_pct": round(q.get("regularMarketChangePercent", 0), 2),
             "currency":   q.get("currency", "USD"),
             "source":     base}
            for q in raw
        ]
        return {"quotes": quotes, "symbols": symbols, "success": True, "source": base}

    def _stocks_coingecko(self, crypto_symbols: List[str]) -> Dict:
        ids = [CRYPTO_IDS[s] for s in crypto_symbols if s in CRYPTO_IDS]
        if not ids:
            return {"success": False, "error": "no recognised crypto symbols"}
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(ids), "vs_currencies": "usd",
                    "include_24hr_change": "true"},
            timeout=_TIMEOUT, headers=_HEADERS,
        )
        r.raise_for_status()
        d = r.json()
        quotes = []
        id_to_sym = {v: k for k, v in CRYPTO_IDS.items()}
        for cg_id, data in d.items():
            sym  = id_to_sym.get(cg_id, cg_id.upper())
            pct  = round(data.get("usd_24h_change", 0), 2)
            price = data.get("usd", 0)
            quotes.append({"symbol": sym, "name": cg_id.title(),
                           "price": price, "change": round(price * pct / 100, 2),
                           "change_pct": pct, "currency": "USD",
                           "source": "coingecko.com"})
        return {"quotes": quotes, "success": bool(quotes)}

    def _stocks_alphavantage(self, equity_symbols: List[str]) -> Dict:
        quotes = []
        for sym in equity_symbols:
            try:
                r = requests.get(
                    "https://www.alphavantage.co/query",
                    params={"function": "GLOBAL_QUOTE", "symbol": sym,
                            "apikey": self._av_key},
                    timeout=_TIMEOUT, headers=_HEADERS,
                )
                r.raise_for_status()
                gq = r.json().get("Global Quote", {})
                if not gq:
                    continue
                price = float(gq.get("05. price", 0))
                chg   = float(gq.get("09. change", 0))
                pct   = float(gq.get("10. change percent", "0%").replace("%", ""))
                quotes.append({"symbol": sym, "name": sym, "price": round(price, 2),
                               "change": round(chg, 2), "change_pct": round(pct, 2),
                               "currency": "USD", "source": "alphavantage.co"})
            except Exception:
                continue
        return {"quotes": quotes, "success": bool(quotes)}

    # ── tech updates ─────────────────────────────────────────────────────────

    def get_tech_updates(self, count: int = 5, force: bool = False) -> Dict:
        key = "tech_updates"
        if not force and self._fresh(key, _TECH_TTL):
            return self._read(key)
        hn    = self._waterfall([self._hn_algolia, self._hn_firebase], count)
        arxiv = self._waterfall([self._arxiv, self._papers_with_code], count)
        result = {
            "hn_stories":   hn.get("items", []) if hn.get("success") else [],
            "arxiv_papers": arxiv.get("papers", []) if arxiv.get("success") else [],
            "success": True,
        }
        return self._write(key, result)

    def _hn_algolia(self, count: int) -> Dict:
        r = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={"tags": "front_page", "hitsPerPage": count},
            timeout=_TIMEOUT, headers=_HEADERS,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])[:count]
        items = [{"title": h.get("title",""), "url": h.get("url",""),
                  "points": h.get("points",0), "comments": h.get("num_comments",0)}
                 for h in hits if h.get("title")]
        return {"items": items, "success": bool(items), "source": "hn.algolia.com"}

    def _hn_firebase(self, count: int) -> Dict:
        r = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=_TIMEOUT, headers=_HEADERS,
        )
        r.raise_for_status()
        ids = r.json()[:count]
        items = []
        with ThreadPoolExecutor(max_workers=count) as ex:
            futures = {ex.submit(self._hn_item, i): i for i in ids}
            for future in as_completed(futures):
                item = future.result()
                if item:
                    items.append(item)
        items.sort(key=lambda x: -x.get("points", 0))
        return {"items": items[:count], "success": bool(items), "source": "firebase HN"}

    def _hn_item(self, item_id: int) -> Optional[Dict]:
        try:
            r = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json",
                timeout=5, headers=_HEADERS,
            )
            d = r.json()
            return {"title": d.get("title",""), "url": d.get("url",""),
                    "points": d.get("score",0), "comments": d.get("descendants",0)}
        except Exception:
            return None

    def _arxiv(self, count: int) -> Dict:
        r = requests.get(
            "http://export.arxiv.org/api/query",
            params={"search_query": "cat:cs.AI OR cat:cs.LG OR cat:cs.CL",
                    "sortBy": "submittedDate", "sortOrder": "descending",
                    "max_results": count},
            timeout=_TIMEOUT, headers=_HEADERS,
        )
        r.raise_for_status()
        ns    = {"a": "http://www.w3.org/2005/Atom"}
        root  = ET.fromstring(r.content)
        papers = []
        for entry in root.findall("a:entry", ns)[:count]:
            title = " ".join((entry.findtext("a:title", namespaces=ns) or "").split())
            link  = next((el.get("href","") for el in entry.findall("a:link", ns)
                          if el.get("rel") == "alternate"), "")
            cats  = [c.get("term","") for c in entry.findall("a:category", ns)]
            summary = (entry.findtext("a:summary", namespaces=ns) or "").strip()[:200]
            papers.append({"title": title, "url": link,
                           "categories": cats[:3], "summary": summary})
        return {"papers": papers, "success": bool(papers), "source": "arxiv.org"}

    def _papers_with_code(self, count: int) -> Dict:
        r = requests.get(
            "https://paperswithcode.com/api/v1/papers/",
            params={"format": "json", "ordering": "-published", "page_size": count},
            timeout=_TIMEOUT, headers=_HEADERS,
        )
        r.raise_for_status()
        results = r.json().get("results", [])[:count]
        papers  = [{"title": p.get("title",""), "url": p.get("url_abs",""),
                    "categories": [], "summary": (p.get("abstract") or "")[:200]}
                   for p in results if p.get("title")]
        return {"papers": papers, "success": bool(papers),
                "source": "paperswithcode.com"}

    # ── full snapshot ─────────────────────────────────────────────────────────

    def get_awareness_snapshot(self, force: bool = False) -> str:
        key = "snapshot"
        if not force and self._fresh(key, _SNAP_TTL):
            return self._read(key)

        tasks = {
            "time":        lambda: self.get_time(),
            "location":    lambda: self.get_location(force),
            "weather":     lambda: self.get_weather("", force),
            "news_global": lambda: self.get_news("global", 5, force),
            "news_tech":   lambda: self.get_news("tech", 5, force),
            "news_ai":     lambda: self.get_news("ai", 4, force),
            "stocks":      lambda: self.get_stocks(None, force),
            "tech":        lambda: self.get_tech_updates(5, force),
        }
        results: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            fmap = {ex.submit(fn): name for name, fn in tasks.items()}
            for future in as_completed(fmap):
                name = fmap[future]
                try:
                    results[name] = future.result()
                except Exception:
                    results[name] = None

        snapshot = _format_snapshot(results)
        return self._write(key, snapshot)


# ── Formatting ────────────────────────────────────────────────────────────────

def _format_snapshot(r: Dict) -> str:
    # T-146: explicit instruction prevents normie LLM (Groq/Cerebras) from
    # reproducing this block verbatim as its response.
    lines = [
        "=== LIVE AWARENESS (read-only context — never output verbatim) ===",
    ]

    t   = r.get("time") or {}
    loc = r.get("location") or {}
    loc_parts = [loc.get("city",""), loc.get("region",""), loc.get("country","")]
    loc_str   = ", ".join(p for p in loc_parts if p)
    lines.append(
        f"Time: {t.get('utc', datetime.now(timezone.utc).strftime('%A, %Y-%m-%d %H:%M UTC'))}"
        + (f"  |  {loc_str}" if loc_str else "")
    )

    w = r.get("weather") or {}
    if w.get("success") and w.get("temp_c"):
        lines.append(
            f"Weather: {w.get('location','')} — {w.get('description','')}, "
            f"{w['temp_c']}°C/{w.get('temp_f','?')}°F  "
            f"Humidity {w.get('humidity','?')}%  "
            f"Wind {w.get('wind_kmph','?')} km/h  "
            f"UV {w.get('uv_index','?')}  [via {w.get('source','')}]"
        )
    else:
        lines.append("Weather: unavailable")

    s = r.get("stocks") or {}
    if s.get("success") and s.get("quotes"):
        parts = []
        for q in s["quotes"]:
            sign = "+" if q["change_pct"] >= 0 else ""
            parts.append(f"{q['symbol']} ${q['price']:,.2f} ({sign}{q['change_pct']:.1f}%)")
        lines.append("Markets: " + "  |  ".join(parts))
    else:
        lines.append("Markets: unavailable")

    for label, key in [("Global News", "news_global"),
                       ("Tech Headlines", "news_tech"),
                       ("AI/ML News", "news_ai")]:
        nd = r.get(key) or {}
        arts = nd.get("articles", [])
        if arts:
            lines.append(f"\n{label} [via {nd.get('source', 'RSS')}]:")
            for a in arts:
                lines.append(f"  • {a['title']}")

    tech  = r.get("tech") or {}
    hn    = tech.get("hn_stories", [])
    arxiv = tech.get("arxiv_papers", [])

    if hn:
        lines.append("\nHacker News Top Stories:")
        for h in hn[:5]:
            lines.append(f"  • {h['title']}  [{h['points']}pts | {h['comments']}c]")

    if arxiv:
        lines.append("\nLatest AI/ML Research:")
        for p in arxiv[:4]:
            cats = "/".join(p.get("categories", []))
            lines.append(f"  • [{cats}] {p['title']}")

    lines.append("\n=== END AWARENESS ===")
    return "\n".join(lines)


# ── helpers ───────────────────────────────────────────────────────────────────

def _text(element, tag: str) -> str:
    el = element.find(tag)
    return (el.text or "").strip() if el is not None else ""


def _first(lst: Optional[list], default: str) -> str:
    if lst and isinstance(lst, list) and lst[0]:
        return lst[0].get("value", default) if isinstance(lst[0], dict) else str(lst[0])
    return default


def _clean_html(text: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in [("&amp;","&"),("&lt;","<"),("&gt;",">"),
                          ("&quot;",'"'),("&#39;","'"),("&nbsp;"," ")]:
        text = text.replace(entity, char)
    return " ".join(text.split())


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_get_weather(agent, tool_input, *, memory_override=None):
    return agent.awareness.get_weather(
        location=tool_input.get("location", ""),
        force=True,
    )


def _handle_get_news(agent, tool_input, *, memory_override=None):
    return agent.awareness.get_news(
        category=tool_input.get("category", "global"),
        count=min(tool_input.get("count", 6), 10),
        force=True,
    )


def _handle_get_stocks(agent, tool_input, *, memory_override=None):
    return agent.awareness.get_stocks(
        symbols=tool_input.get("symbols") or None,
        force=True,
    )


def _handle_get_tech_updates(agent, tool_input, *, memory_override=None):
    return agent.awareness.get_tech_updates(
        count=tool_input.get("count", 5),
        force=True,
    )


def _handle_refresh_awareness(agent, tool_input, *, memory_override=None):
    # Forces a snapshot refresh and stashes it on the agent so subsequent
    # turns see the fresh data; mirrors the legacy execute_tool behavior.
    agent.awareness_snapshot = agent.awareness.get_awareness_snapshot(force=True)
    return {"success": True, "preview": agent.awareness_snapshot[:300]}


def _handle_get_location(agent, tool_input, *, memory_override=None):
    return agent.awareness.get_location(force=True)


TOOLS = [
    ToolSpec(
        name="get_weather",
        description="Get current weather for any location. Empty location = auto-detect from IP.",
        input_schema={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name or 'city,country'. Leave empty to use current location.",
                },
            },
            "required": [],
        },
        handler=_handle_get_weather,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="get_news",
        description="Get recent news headlines. Categories: global | tech | business | science | ai",
        input_schema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["global", "tech", "business", "science", "ai"],
                    "description": "News category (default: global)",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of headlines to return (default 6, max 10)",
                    "default": 6,
                },
            },
            "required": [],
        },
        handler=_handle_get_news,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="get_stocks",
        description="Get live stock/crypto prices from Yahoo Finance. Returns price and % change.",
        input_schema={
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ticker symbols e.g. ['AAPL','NVDA','BTC-USD']. Omit for default watchlist.",
                },
            },
            "required": [],
        },
        handler=_handle_get_stocks,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="get_tech_updates",
        description="Get latest HN front-page stories and ArXiv AI/ML/NLP research papers.",
        input_schema={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of items per source (default 5)",
                    "default": 5,
                },
            },
            "required": [],
        },
        handler=_handle_get_tech_updates,
        success_predicate=lambda r: r.get("success", False),
    ),
    ToolSpec(
        name="refresh_awareness",
        description=(
            "Force-refresh the full live awareness snapshot (weather, news, stocks, "
            "research). Use when Pi needs the absolute latest data."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_refresh_awareness,
    ),
    ToolSpec(
        name="get_location",
        description=(
            "Detect Ash's current approximate location via IP geolocation (ipinfo.io fallback chain). "
            "Returns city, region, country, timezone, and coordinates. Use when Ash asks where he is "
            "or when location context is needed for weather/search."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_handle_get_location,
        success_predicate=lambda r: bool(r.get("city")),
    ),
]
