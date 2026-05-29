"""agent/provider_router.py — T-121: generalised provider chain with circuit breaker.

A ProviderRouter walks a list of provider callables until one succeeds. Failures
that look like rate limits (429) put the provider in cooldown for the parsed
retry-after duration. Three consecutive failures of the same provider open a
hard circuit for circuit_cooldown_s (default 5 min). All cooldown opens and
exhaustions are recorded via agent.observability.track_silent.

This module is the generalised version of the compression-with-Haiku-fallback
pattern (T-092). First user is vision (T-121); will be reused for STT, TTS,
and any future LLM-backed tool with multiple providers.

Provider callable contract:
    def provider(*args, **kwargs) -> result
    May raise RateLimitError(provider_name, retry_after_s, original) on 429.
    May raise any other Exception on hard failure (router advances to next).

Usage:
    from agent.provider_router import ProviderRouter, RateLimitError

    router = ProviderRouter(
        name="vision",
        providers=[
            ("gemini_flash", call_gemini_flash),
            ("gemini_pro", call_gemini_pro),
            ("claude_haiku_vision", call_claude_haiku),
            ("claude_sonnet_vision", call_claude_sonnet),
        ],
    )
    try:
        text = router.call(image_paths, question)
    except AllProvidersExhausted as e:
        # graceful degradation
        ...
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, List, Optional, Tuple


class ProviderError(Exception):
    """Base for provider-routing errors. Other code should catch this OR its subclasses."""


class RateLimitError(ProviderError):
    """Raised by a provider callable to signal 429 (rate limit / quota exhausted).

    retry_after_s: seconds to back off (parsed from the error response if possible).
                   None means router applies a default (60s).
    original:      the underlying exception, kept for logging.
    """
    def __init__(self, provider: str, retry_after_s: Optional[float] = None, original: Optional[Exception] = None):
        self.provider = provider
        self.retry_after_s = retry_after_s
        self.original = original
        super().__init__(f"{provider}: rate limited (retry in {retry_after_s}s)")


class AllProvidersExhausted(ProviderError):
    """Raised when every provider in the chain failed or was in cooldown."""
    def __init__(self, chain_history: List[Tuple[str, str]]):
        self.chain_history = chain_history
        msg = "all providers exhausted: " + "; ".join(f"{n}={r}" for n, r in chain_history)
        super().__init__(msg)


# ── 429 retry-after parsers ────────────────────────────────────────────────────

# Google: error message contains "Please retry in 40.698557649s."
_GOOGLE_RETRY_RE = re.compile(r"retry in (\d+(?:\.\d+)?)s", re.IGNORECASE)
# Google: structured retryDelay: '40s'
_GOOGLE_DELAY_RE = re.compile(r"retryDelay['\"]\s*:\s*['\"](\d+(?:\.\d+)?)s", re.IGNORECASE)


def parse_retry_after_google(exc: Exception) -> Optional[float]:
    """Extract retry-after seconds from a Google google-genai error."""
    msg = str(exc)
    m = _GOOGLE_RETRY_RE.search(msg)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = _GOOGLE_DELAY_RE.search(msg)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def parse_retry_after_anthropic(exc: Exception) -> Optional[float]:
    """Extract retry-after seconds from an anthropic.RateLimitError.

    The anthropic library exposes response.headers; the 'retry-after' header
    is in seconds per RFC 7231.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    val = headers.get("retry-after") or headers.get("Retry-After")
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def is_rate_limit_error(exc: Exception) -> bool:
    """Detect 429-like errors across providers by class name OR message inspection."""
    cls_name = type(exc).__name__
    if cls_name in ("RateLimitError", "TooManyRequestsError", "ResourceExhausted"):
        return True
    msg = str(exc)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "rate limit" in msg.lower():
        return True
    return False


# ── Router ────────────────────────────────────────────────────────────────────

class ProviderRouter:
    """Walks providers in order, applying cooldowns on rate-limit errors.

    Thread-safe — cooldown state and failure counters guarded by an internal lock.
    State is in-memory only (process restart = clean slate; intentional).
    """

    def __init__(
        self,
        name: str,
        providers: List[Tuple[str, Callable]],
        circuit_threshold: int = 3,
        circuit_cooldown_s: float = 300.0,
        default_429_cooldown_s: float = 60.0,
    ):
        self.name = name
        self.providers = providers
        self.circuit_threshold = circuit_threshold
        self.circuit_cooldown_s = circuit_cooldown_s
        self.default_429_cooldown_s = default_429_cooldown_s
        self._cooldown_until: dict = {}
        self._consecutive_failures: dict = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.time()

    def _in_cooldown(self, provider_name: str) -> Tuple[bool, float]:
        cd_until = self._cooldown_until.get(provider_name, 0.0)
        if self._now() < cd_until:
            return True, cd_until - self._now()
        return False, 0.0

    def _record_failure(self, provider_name: str, retry_s: float) -> bool:
        """Record a failure; return True if the circuit just opened."""
        self._cooldown_until[provider_name] = self._now() + retry_s
        failures = self._consecutive_failures.get(provider_name, 0) + 1
        self._consecutive_failures[provider_name] = failures
        if failures >= self.circuit_threshold:
            self._cooldown_until[provider_name] = self._now() + self.circuit_cooldown_s
            return True
        return False

    def _record_success(self, provider_name: str) -> None:
        self._consecutive_failures[provider_name] = 0

    def _track(self, event: str, exc: Optional[Exception] = None, **context) -> None:
        """Best-effort telemetry — never crash the router."""
        try:
            from agent.observability import track_silent
            track_silent(f"{self.name}.{event}", exc, context=context)
        except Exception:
            pass

    def call(self, *args, **kwargs) -> Any:
        """Walk providers; return first success; raise AllProvidersExhausted on no success."""
        history: List[Tuple[str, str]] = []

        for provider_name, fn in self.providers:
            with self._lock:
                in_cd, remaining = self._in_cooldown(provider_name)
            if in_cd:
                history.append((provider_name, f"cooldown {remaining:.0f}s remaining"))
                continue

            try:
                result = fn(*args, **kwargs)
            except RateLimitError as e:
                retry_s = e.retry_after_s if e.retry_after_s is not None else self.default_429_cooldown_s
                with self._lock:
                    opened = self._record_failure(provider_name, retry_s)
                self._track("rate_limit", e, provider=provider_name, retry_s=retry_s)
                if opened:
                    self._track("circuit_open", e, provider=provider_name, cooldown_s=self.circuit_cooldown_s)
                history.append((provider_name, f"429 (cooldown {retry_s:.0f}s)"))
                continue
            except Exception as e:
                # Soft inspect — if it LOOKS like a rate limit, treat as one
                if is_rate_limit_error(e):
                    parsed = parse_retry_after_google(e)
                    if parsed is None:
                        parsed = parse_retry_after_anthropic(e)
                    retry_s = parsed if parsed is not None else self.default_429_cooldown_s
                    with self._lock:
                        opened = self._record_failure(provider_name, retry_s)
                    self._track("rate_limit", e, provider=provider_name, retry_s=retry_s, detected="implicit")
                    if opened:
                        self._track("circuit_open", e, provider=provider_name, cooldown_s=self.circuit_cooldown_s)
                    history.append((provider_name, f"429-like (cooldown {retry_s:.0f}s)"))
                    continue

                with self._lock:
                    failures = self._consecutive_failures.get(provider_name, 0) + 1
                    self._consecutive_failures[provider_name] = failures
                self._track("provider_error", e, provider=provider_name)
                history.append((provider_name, f"{type(e).__name__}: {str(e)[:80]}"))
                continue

            with self._lock:
                self._record_success(provider_name)
            return result

        # No provider succeeded
        self._track("all_exhausted", None, history=history)
        raise AllProvidersExhausted(history)

    def reset(self) -> None:
        """Wipe cooldown + failure state. Useful for tests and ops resets."""
        with self._lock:
            self._cooldown_until.clear()
            self._consecutive_failures.clear()
