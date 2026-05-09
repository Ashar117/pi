"""
agent/awareness_shortcut.py — fast snapshot lookup for awareness questions.

try_answer_from_awareness(user_message, snapshot) -> Optional[str]

Returns a direct answer extracted from the cached awareness snapshot when the
user is asking about weather, markets, or news — bypassing the LLM entirely.
Returns None for anything else so the caller falls through to its normal path.

No imports from the rest of Pi. No side effects. Pure function.
"""
import re
from typing import Optional


# Keywords that signal each topic — checked against lowercased user message.
_WEATHER_SIGNALS = {
    "weather", "temperature", "temp", "hot", "cold", "rain", "sunny",
    "cloudy", "forecast", "humidity", "wind", "outside", "degrees",
}
_MARKET_SIGNALS = {
    "stock", "stocks", "market", "markets", "price", "share", "shares",
    "aapl", "nvda", "btc", "crypto", "nasdaq", "s&p", "dow",
    "trading", "ticker", "portfolio",
}
_NEWS_SIGNALS = {
    "news", "headlines", "headline", "happening", "latest",
    "current events", "today's news", "what's going on",
}


def try_answer_from_awareness(user_message: str, snapshot: str) -> Optional[str]:
    """Return a snapshot-derived answer or None.

    Only fires when:
    - snapshot is non-empty and contains live data (not just the header)
    - the user message contains a clear awareness-topic signal word
    - the relevant section of the snapshot has actual data (not 'unavailable')
    """
    if not snapshot or "=== LIVE AWARENESS ===" not in snapshot:
        return None

    lower = user_message.lower()

    topic = _detect_topic(lower)
    if topic is None:
        return None

    if topic == "weather":
        return _extract_weather(snapshot)
    if topic == "markets":
        return _extract_markets(snapshot)
    if topic == "news":
        return _extract_news(snapshot)

    return None


# ── Topic detection ───────────────────────────────────────────────────────────

def _detect_topic(lower_message: str) -> Optional[str]:
    """Return 'weather', 'markets', 'news', or None."""
    words = set(re.findall(r"\b\w[\w&]*\b", lower_message))
    if words & _WEATHER_SIGNALS:
        return "weather"
    if words & _MARKET_SIGNALS:
        return "markets"
    if words & _NEWS_SIGNALS:
        return "news"
    # Multi-word phrases not caught by word split
    if "current events" in lower_message or "what's going on" in lower_message:
        return "news"
    return None


# ── Section extractors ────────────────────────────────────────────────────────

def _extract_weather(snapshot: str) -> Optional[str]:
    """Return the Weather line if it has real data, else None."""
    for line in snapshot.splitlines():
        if line.startswith("Weather:"):
            if "unavailable" in line.lower():
                return None
            return line.strip()
    return None


def _extract_markets(snapshot: str) -> Optional[str]:
    """Return the Markets line if it has real data, else None."""
    for line in snapshot.splitlines():
        if line.startswith("Markets:"):
            if "unavailable" in line.lower():
                return None
            return line.strip()
    return None


def _extract_news(snapshot: str) -> Optional[str]:
    """Return all news headlines from the snapshot, or None if none found."""
    lines = snapshot.splitlines()
    in_news = False
    collected: list[str] = []

    for line in lines:
        if any(line.startswith(h) for h in ("Global News", "Tech Headlines", "AI/ML News",
                                              "Hacker News")):
            in_news = True
            collected.append(line.strip())
            continue
        if in_news:
            if line.startswith("  •"):
                collected.append(line.strip())
            elif line.strip() == "" or line.startswith("Latest"):
                # Keep going through blank lines and sub-sections
                if line.startswith("Latest"):
                    in_news = False
                continue
            elif line.startswith("="):
                break
            else:
                in_news = False

    if not collected:
        return None
    return "\n".join(collected)
