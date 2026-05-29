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
    - for weather: the user is NOT asking about a city other than the cached one
      (T-146: prevents returning Atlanta weather when user asks about Multan)

    Deliberately conservative — returning None falls through to the LLM which
    can use tools or reason from the snapshot in context.
    """
    if not snapshot or "LIVE AWARENESS" not in snapshot:
        return None

    lower = user_message.lower()

    # T-147: require message to be at least 2 words and not be a tiny fragment;
    # single-word or ambiguous fragments ("wb", "and", "yes") should go to LLM.
    words = lower.split()
    if len(words) < 2:
        return None

    topic = _detect_topic(lower)
    if topic is None:
        return None

    if topic == "weather":
        return _extract_weather(snapshot, lower)
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

def _extract_weather(snapshot: str, user_lower: str = "") -> Optional[str]:
    """Return the Weather line if it has real data and matches what the user asked, else None.

    T-146: If the user message contains a place name that differs from the
    cached city, return None so the LLM (with tools) can fetch the right data.
    """
    for line in snapshot.splitlines():
        if line.startswith("Weather:"):
            if "unavailable" in line.lower():
                return None
            # Extract cached city name: "Weather: Atlanta — ..."
            m = re.match(r"Weather:\s+([^—\-]+)", line)
            if m and user_lower:
                cached_city = m.group(1).strip().lower()
                # If user mentioned any word that looks like a different place
                # (not in the cached city name), decline the shortcut
                user_words = set(re.findall(r"\b[a-z]{3,}\b", user_lower))
                # Remove generic weather words from the check
                user_place_hints = user_words - _WEATHER_SIGNALS - {
                    "weather", "what", "like", "right", "now", "the", "there",
                    "raining", "sunny", "outside",
                }
                cached_city_words = set(cached_city.split())
                # If user hints at a place that isn't the cached city → skip shortcut
                if user_place_hints and not user_place_hints.issubset(cached_city_words):
                    # Check if any hint word appears in the cached city line itself
                    if not any(w in line.lower() for w in user_place_hints):
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
