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
    "aapl", "nvda", "btc", "eth", "bitcoin", "ethereum", "crypto",
    "nasdaq", "s&p", "dow", "trading", "ticker", "portfolio",
}
# Crypto-specific intent words. Used to decide whether a crypto-only snapshot
# line actually answers the question: "how's the market" (equity intent) must
# NOT be served the crypto line, but "how's btc" must.
_CRYPTO_WORDS = frozenset({
    "btc", "eth", "bitcoin", "ethereum", "crypto", "cryptos", "cryptocurrency",
    "sol", "solana", "doge", "dogecoin", "xrp", "ada", "bnb", "coin", "coins",
})
# Instruments the generic crypto/index/stock snapshot does NOT carry. When the
# user names one of these and it isn't literally present in the Markets snapshot
# line, the shortcut declines so the LLM+tools fetch the real figure instead of
# returning the canned snapshot (e.g. "wheat/soybean futures" must not return
# the BTC/ETH line). Mirrors the news specificity guard (T-160).
_SPECIFIC_INSTRUMENT_WORDS = frozenset({
    "futures", "future", "commodity", "commodities",
    "wheat", "soybean", "soybeans", "soy", "corn", "oat", "oats", "rice",
    "coffee", "sugar", "cotton", "cocoa", "cattle", "hogs", "lumber",
    "oil", "crude", "brent", "wti", "gas", "gasoline", "natgas",
    "gold", "silver", "copper", "platinum", "palladium",
    "forex", "fx", "eur", "gbp", "jpy", "yen", "euro", "pound",
    "bond", "bonds", "treasury", "treasuries", "yield", "yields",
})
_NEWS_SIGNALS = {
    "news", "headlines", "headline", "happening", "latest",
}
# Multi-word phrases not catchable by single-token set intersection.
_NEWS_PHRASES = ("current events", "what's going on", "today's news")
# T-160: self-location phrases. Specific so "where is the store" doesn't match —
# these all refer to the USER's own current location.
_LOCATION_PHRASES = (
    "where am i", "where i am", "where am i right now", "my location",
    "my current location", "current location", "exact location",
    "whats my location", "what's my location", "whereabouts", "where are we",
)

# T-211: state-changing action verbs. A message that LEADS with one of these is an
# imperative command ("add the ticker", "create a ticket", "buy NVDA") — the user
# wants Pi to DO something, not read a snapshot. The shortcut must decline so the
# LLM + tools handle it. Info-request verbs (tell/show/give/get) are deliberately
# EXCLUDED so "tell me the markets" still fires.
_ACTION_VERBS = frozenset({
    "add", "create", "make", "build", "write", "file", "open", "buy", "sell",
    "set", "schedule", "remove", "delete", "send", "update", "track", "watch",
    "remind", "draft", "generate", "run", "execute", "fix", "change", "edit",
    "save", "store", "log", "put", "append", "register", "book", "cancel",
    "start", "stop", "enable", "disable",
})
# Leading words skipped when finding the message's first meaningful word, so
# "hey can you add the ticker" still trips the action-verb guard on "add".
_LEADING_FILLERS = frozenset({
    "hey", "yo", "ok", "okay", "so", "please", "pls", "plz", "pi", "hi",
    "hello", "can", "could", "would", "you", "u", "just", "now", "um", "umm",
})


def _leads_with_action_verb(lower_message: str) -> bool:
    """True if the first meaningful word (after fillers) is a state-changing verb."""
    for w in re.findall(r"[a-z']+", lower_message):
        if w in _LEADING_FILLERS:
            continue
        return w in _ACTION_VERBS
    return False


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

    # T-211: decline imperative commands — they want an action, not a snapshot.
    if _leads_with_action_verb(lower):
        return None

    topic = _detect_topic(lower)
    if topic is None:
        return None

    if topic == "location":
        return _extract_location(snapshot)
    if topic == "weather":
        return _extract_weather(snapshot, lower)
    if topic == "markets":
        return _extract_markets(snapshot, lower)
    if topic == "news":
        return _extract_news(snapshot, lower)

    return None


# ── Topic detection ───────────────────────────────────────────────────────────

def _detect_topic(lower_message: str) -> Optional[str]:
    """Return 'location', 'weather', 'markets', 'news', or None."""
    # T-160: self-location is checked first — it's the most specific intent and
    # would otherwise be missed (no single keyword; "outside"/"now" overlap weather).
    if any(p in lower_message for p in _LOCATION_PHRASES):
        return "location"
    words = set(re.findall(r"\b\w[\w&]*\b", lower_message))
    if words & _WEATHER_SIGNALS:
        return "weather"
    if words & _MARKET_SIGNALS:
        return "markets"
    if words & _NEWS_SIGNALS:
        return "news"
    if any(p in lower_message for p in _NEWS_PHRASES):
        return "news"
    return None


# ── Section extractors ────────────────────────────────────────────────────────

def _extract_location(snapshot: str) -> Optional[str]:
    """Return a direct answer from the snapshot's Location line, else None.

    T-160: gives 'where am I' a deterministic, consistent answer from live
    geo-IP — avoiding the LLM answering once from a stale L3 fact and then
    contradicting itself on the next turn.
    """
    for line in snapshot.splitlines():
        if line.startswith("Location:"):
            value = line[len("Location:"):].strip()
            if not value or "unavailable" in value.lower():
                return None
            return f"You're in {value}."
    return None


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


def _extract_markets(snapshot: str, user_lower: str = "") -> Optional[str]:
    """Return the Markets line if it has real data AND covers what the user asked.

    Declines (returns None) when:
    - the user names a specific instrument the snapshot doesn't carry
      (commodities, forex, bonds, futures) — the "wheat/soybean futures →
      BTC/ETH" bug; or
    - the user asks a general/equity market question ("how's the market",
      "how are stocks doing") but the snapshot line has only crypto (Yahoo
      equity fetch failed, leaving CoinGecko crypto only) — serving BTC/ETH as
      "the market" is the "i didnt ask for bitcoin" bug. Crypto-specific
      questions ("how's btc") still fire.

    Both defer to the LLM+tools instead of returning misleading snapshot data.
    Mirrors the _extract_news specificity guard (T-160).
    """
    for line in snapshot.splitlines():
        if line.startswith("Markets:"):
            if "unavailable" in line.lower():
                return None
            if user_lower:
                tokens = set(re.findall(r"[a-z]{2,}", user_lower))
                named = tokens & _SPECIFIC_INSTRUMENT_WORDS
                snap_tokens = set(re.findall(r"[a-z]{2,}", line.lower()))
                if named and not named.issubset(snap_tokens):
                    return None
                # Crypto-only-line guard: symbols are "SYM $price"; crypto pairs
                # carry a "-USD" suffix in this snapshot, equities don't.
                line_syms = re.findall(r"([A-Za-z0-9.\-]+)\s*\$", line)
                has_equity = any("-usd" not in s.lower() for s in line_syms)
                if line_syms and not has_equity and not (tokens & _CRYPTO_WORDS):
                    return None
            return line.strip()
    return None


# T-160: section headers present in the awareness snapshot.
_ALL_NEWS_HEADERS = (
    "Global News", "Tech Headlines", "AI/ML News", "Hacker News", "Latest AI/ML Research",
)

# Map a requested category → the snapshot section header(s) that satisfy it.
_NEWS_CATEGORY_KEYWORDS = {
    "Global News":        {"world", "global", "international", "politics", "political"},
    "Tech Headlines":     {"tech", "technology", "gadgets", "software", "startup", "startups"},
    "AI/ML News":         {"ai", "ml", "llm", "llms", "machine", "learning", "models"},
    "Hacker News":        {"hacker", "hackernews", "hn", "ycombinator"},
}

# Business/markets/finance news is NOT in the generic snapshot — defer these to
# the LLM/tools (root: get_news('business')/web_search) so the user gets the
# specific thing they asked for instead of stale generic headlines.
_BUSINESS_NEWS_WORDS = {
    "business", "market", "markets", "finance", "financial", "economy", "economic",
    "stocks", "stock", "cnbc", "earnings", "fed", "inflation", "ipo", "merger",
}

# Generic news words that DON'T make a query "specific".
_GENERIC_NEWS_WORDS = _NEWS_SIGNALS | {
    "the", "what", "whats", "is", "are", "any", "me", "today", "now", "right",
    "tell", "give", "show", "on", "in", "of", "to", "you", "got", "get", "some",
    "most", "important", "going", "anything", "new", "update", "updates", "for",
}


def _collect_news_sections(snapshot: str, wanted_headers: Optional[set]) -> Optional[str]:
    """Collect snapshot news bullets. wanted_headers=None → all sections;
    otherwise only sections whose header starts with a wanted label."""
    want = wanted_headers if wanted_headers is not None else set(_ALL_NEWS_HEADERS)
    collected: list[str] = []
    capture = False
    for line in snapshot.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(h) for h in _ALL_NEWS_HEADERS):
            capture = any(stripped.startswith(w) for w in want)
            if capture:
                collected.append(stripped)
            continue
        if capture:
            if stripped.startswith("•"):
                collected.append(stripped)
            elif stripped == "":
                continue
            elif stripped.startswith("="):
                break
            else:
                capture = False
    return "\n".join(collected) if collected else None


def _extract_news(snapshot: str, user_lower: str = "") -> Optional[str]:
    """Return news from the snapshot, scoped to what the user actually asked.

    T-160: previously returned the whole generic blob for ANY news query, so
    'tech news' / 'news about X' got undifferentiated headlines. Now:
    - business/markets/finance → None (defer to tools; not in the snapshot)
    - a specific category (tech/ai/world/hn) → only that section
    - any other specific topic/entity → None (defer to LLM/web_search)
    - generic ('what's the news') → all sections
    """
    uw = set(re.findall(r"[a-z]{2,}", user_lower))

    if uw & _BUSINESS_NEWS_WORDS:
        return None

    wanted = {label for label, kws in _NEWS_CATEGORY_KEYWORDS.items() if uw & kws}
    if wanted:
        if "AI/ML News" in wanted:
            wanted.add("Latest AI/ML Research")
        return _collect_news_sections(snapshot, wanted)

    # Words left after removing generic + category terms = a specific topic/entity.
    category_words = {w for kws in _NEWS_CATEGORY_KEYWORDS.values() for w in kws}
    topic = uw - _GENERIC_NEWS_WORDS - category_words
    if topic:
        return None  # specific ask → let the LLM/tools fetch it fresh

    return _collect_news_sections(snapshot, None)
