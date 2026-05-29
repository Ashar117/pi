"""core/llm_router.py — Multi-provider LLM router with fallback (T-048).

Priority order: Claude (Anthropic) → Groq → Gemini
Brownout window: 5 minutes after a hard failure on any provider.
Cost tracking + response cache via core/cost_tracker.py (SQLite data/llm_cost.db).

Usage:
    from core.llm_router import LLMRouter
    router = LLMRouter(anthropic_key=..., groq_key=..., gemini_key=...)
    resp = router.chat(messages, system=prompt, tools=tool_defs)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.cost_tracker import CostTracker

log = logging.getLogger(__name__)

BROWNOUT_SECS = 300  # 5-min cooldown after a provider hard-fails

# T-115: error message substrings that signal a model-generation quality failure.
# These are provider-healthy — the request was understood but the model choked.
# Do NOT brownout on these; a retry without tools is often sufficient.
_GENERATION_ERROR_MARKERS = frozenset([
    "tool_use_failed",
    "context_length_exceeded",
    "content_policy_violation",
    "content_filter",
])

# T-084 R3: per-provider daily token budget (in + out combined). Matches
# commonly-published free-tier limits. Override via env var per provider
# (e.g. GROQ_DAILY_TOKEN_BUDGET=200000). None = no budget, never TPD-browned.
TPD_UTILIZATION_THRESHOLD = 0.9  # mark browned at 90% of budget

import os as _os  # noqa: E402

def _budget(name: str, default):
    """Read provider daily token budget from env var with fallback to default."""
    val = _os.environ.get(f"{name.upper()}_DAILY_TOKEN_BUDGET")
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default

PROVIDER_DAILY_TOKEN_BUDGET: Dict[str, "int | None"] = {
    "groq":       _budget("groq",       100_000),
    "cerebras":   _budget("cerebras", 1_000_000),
    "gemini":     _budget("gemini",   1_000_000),
    "openrouter": _budget("openrouter",  50_000),
    "anthropic":  _budget("anthropic",       None),  # paid; no daily cap
    "ollama":     None,                              # local; no cap
}


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"   # "end_turn" | "tool_use" | "max_tokens"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class LLMRouter:
    """Route LLM calls to the first healthy provider, with automatic failover.

    Messages must be in Anthropic canonical format (plain dicts, not SDK objects).
    Each provider translates to its own wire format internally.
    Cost tracking + response cache are handled transparently via CostTracker.
    """

    def __init__(
        self,
        anthropic_key: str = "",
        groq_key: str = "",
        gemini_key: str = "",
        cerebras_key: str = "",
        openrouter_key: str = "",
        claude_model: str = "claude-sonnet-4-6",
        groq_model: str = "llama-3.3-70b-versatile",
        gemini_model: str = "gemini-2.0-flash",
        cerebras_model: str = "llama-3.3-70b",
        openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free",
        ollama_host: str = "http://localhost:11434",
        ollama_model: str = "dolphin-mistral",
        enable_ollama: bool = True,
        session_id: str = "",
        enable_cache: bool = False,
    ):
        from core.providers.anthropic import AnthropicProvider
        from core.providers.groq_tools import GroqProvider
        from core.providers.gemini import GeminiProvider
        from core.providers.cerebras import CerebrasProvider
        from core.providers.openrouter import OpenRouterProvider

        self._providers = []
        if anthropic_key:
            self._providers.append(AnthropicProvider(anthropic_key, claude_model))
        if groq_key:
            self._providers.append(GroqProvider(groq_key, groq_model))
        if gemini_key:
            self._providers.append(GeminiProvider(gemini_key, gemini_model))
        if cerebras_key:
            self._providers.append(CerebrasProvider(cerebras_key, cerebras_model))
        if openrouter_key:
            self._providers.append(OpenRouterProvider(openrouter_key, openrouter_model))
        # T-082 step 5: Ollama provider for tier='private' (god mode).
        # Constructor is cheap (no connection) — the call itself raises if the
        # daemon is unreachable, which is caught by chat()'s brownout path.
        if enable_ollama:
            try:
                from core.providers.ollama import OllamaProvider
                self._providers.append(OllamaProvider(model=ollama_model, host=ollama_host))
            except ImportError:
                pass

        if not self._providers:
            raise ValueError("LLMRouter: at least one API key must be provided")

        self._brownout: Dict[str, float] = {}
        self._session_id = session_id
        self._enable_cache = enable_cache
        try:
            self._cost = CostTracker()
        except Exception:
            self._cost = None

    # ── Routing ────────────────────────────────────────────────────────────────

    def _is_browned_out(self, name: str) -> bool:
        """True when the provider should be skipped — hard-failure cooldown
        OR preemptive TPD-budget brownout (T-084).

        Hard-failure brownout: 5-min cooldown after a 4xx/5xx that
        _mark_brownout recorded. Existing behavior.

        TPD brownout: if today's usage / daily_budget > 0.9 for this
        provider, route around it. CostTracker outage is fail-open
        (tokens_today returns 0 on error) — never strand a call because
        the tracker DB is locked.
        """
        if time.time() - self._brownout.get(name, 0) < BROWNOUT_SECS:
            return True
        budget = PROVIDER_DAILY_TOKEN_BUDGET.get(name)
        cost = getattr(self, "_cost", None)  # tests may build router without __init__
        if budget is not None and cost is not None:
            try:
                used = cost.tokens_today(name)
            except Exception:
                used = 0
            if budget and used / budget > TPD_UTILIZATION_THRESHOLD:
                log.info(
                    "[LLMRouter] %s TPD brownout: %d/%d (%.0f%%)",
                    name, used, budget, 100 * used / budget,
                )
                return True
        return False

    def _mark_brownout(self, name: str) -> None:
        self._brownout[name] = time.time()
        log.warning("[LLMRouter] %s brownout for %ds", name, BROWNOUT_SECS)

    @staticmethod
    def _is_generation_error(exc: Exception) -> bool:
        """True for model-quality failures that should NOT trigger brownout.

        These are cases where the provider is healthy but the model generated
        a bad response (malformed tool call, context overflow, policy block).
        A 400 with 'invalid_api_key' is NOT a generation error — must not match.
        """
        msg = str(exc).lower()
        if any(marker in msg for marker in _GENERATION_ERROR_MARKERS):
            return True
        # Also catch HTTP 400s on SDK exceptions that have a status_code attr
        status = getattr(exc, "status_code", None)
        if status == 400:
            return any(marker in msg for marker in _GENERATION_ERROR_MARKERS)
        return False

    # T-084 (R3) tier matrix. Each tier maps to an ordered provider preference.
    # 'default' is retained as an alias for 'balanced' so call sites that did
    # not pass tier= keep working; remove the alias in a followup ticket once
    # every site declares its tier explicitly.
    _TIER_ORDERS: Dict[str, tuple] = {
        "private":  ("groq", "ollama"),                                       # god mode (ADR-001)
        "premium":  ("anthropic", "gemini"),                                  # paid quality; code edits / complex planning
        "balanced": ("anthropic", "groq", "gemini", "cerebras", "openrouter"),# current default
        "cheap":    ("cerebras", "groq", "gemini", "openrouter"),             # free tiers first, Claude excluded
        "fast":     ("cerebras", "groq"),                                     # low-latency hot path
    }

    def _providers_for_tier(self, tier: str) -> List:
        """Filter + order providers per call-site tier (T-082 / T-084).

        Returns providers matching the tier's preference list, in order.
        Unknown tier or 'default' falls back to the full provider list in
        init order (= effectively 'balanced' plus ollama as last resort).
        Providers absent from the router (e.g. no API key) are silently
        skipped — chat() handles the empty-list case by raising.
        """
        if tier == "default":
            tier = "balanced"
        order = self._TIER_ORDERS.get(tier)
        if order is None:
            return self._providers
        return sorted(
            (p for p in self._providers if p.name in order),
            key=lambda p: order.index(p.name),
        )

    def chat(
        self,
        messages: List[Dict],
        system=None,
        tools: Optional[List[Dict]] = None,
        max_tokens: int = 2048,
        tier: str = "default",
    ) -> LLMResponse:
        """Call the first available provider; fall back on failure.

        Cost is recorded to data/llm_cost.db after every successful call.
        Responses without tool_calls are optionally served from cache when
        enable_cache=True was set at construction time. The `tier` kwarg
        restricts the provider rotation (T-082): 'private' keeps god-mode
        traffic on Groq → Ollama only.
        """
        if system is None:
            system = ""
        tools_list = tools or []
        _cost = getattr(self, "_cost", None)
        _enable_cache = getattr(self, "_enable_cache", False)
        _session_id = getattr(self, "_session_id", "")

        # Flatten tuple to string for cache key (Anthropic-only caching happens inside the provider)
        system_for_cache = "\n\n".join(system) if isinstance(system, tuple) else system

        # Cache lookup (skip when tools are present — tool responses are stateful)
        if _enable_cache and _cost and not tools_list:
            cached = _cost.cache_get(messages, system_for_cache, tools_list)
            if cached:
                log.debug("[LLMRouter] cache hit")
                return LLMResponse(**{k: v for k, v in cached.items()
                                      if k in LLMResponse.__dataclass_fields__})

        errors: List[str] = []
        for provider in self._providers_for_tier(tier):
            if self._is_browned_out(provider.name):
                errors.append(f"{provider.name}: browned out")
                continue
            try:
                # Only Anthropic understands the (static, dynamic) tuple — flatten for others
                provider_system = system if provider.name == "anthropic" else system_for_cache
                resp = provider.chat(messages, provider_system, tools_list, max_tokens)
                # Record cost
                if _cost:
                    cost = _cost.record(
                        provider=resp.provider,
                        model=resp.model,
                        tokens_in=resp.tokens_in,
                        tokens_out=resp.tokens_out,
                        session_id=_session_id,
                        tier=tier if tier != "default" else "balanced",
                    )
                    resp_dict = {
                        "text": resp.text, "provider": resp.provider,
                        "model": resp.model, "stop_reason": resp.stop_reason,
                        "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
                        "cost_usd": cost,
                        "tool_calls": [{"id": tc.id, "name": tc.name, "input": tc.input}
                                       for tc in resp.tool_calls],
                    }
                    if _enable_cache and not resp.tool_calls:
                        _cost.cache_put(messages, system_for_cache, tools_list, resp_dict)
                return resp
            except Exception as e:
                if self._is_generation_error(e):
                    # T-115: model-quality failure — provider is healthy, no brownout.
                    log.warning("[LLMRouter] %s generation error (no brownout): %s", provider.name, e)
                    errors.append(f"{provider.name}: generation_error: {e}")
                    # No-tools retry when the failure was tool_use_failed with tools active
                    if tools_list and "tool_use_failed" in str(e).lower():
                        try:
                            log.warning("[LLMRouter] %s retrying without tools", provider.name)
                            resp_notool = provider.chat(messages, provider_system, [], max_tokens)
                            if _cost:
                                _cost.record(
                                    provider=resp_notool.provider,
                                    model=resp_notool.model,
                                    tokens_in=resp_notool.tokens_in,
                                    tokens_out=resp_notool.tokens_out,
                                    session_id=_session_id,
                                    tier=tier if tier != "default" else "balanced",
                                )
                            return resp_notool
                        except Exception as retry_e:
                            log.warning("[LLMRouter] %s no-tools retry failed: %s", provider.name, retry_e)
                            errors.append(f"{provider.name}: retry: {retry_e}")
                else:
                    log.error("[LLMRouter] %s error: %s", provider.name, e)
                    self._mark_brownout(provider.name)
                    errors.append(f"{provider.name}: {e}")

        raise RuntimeError(
            "All LLM providers failed or browned out.\n" + "\n".join(errors)
        )

    # ── Health ─────────────────────────────────────────────────────────────────

    def health(self) -> Dict[str, bool]:
        """Ping all providers; clear brownout on success. Returns {name: alive}."""
        result = {}
        for p in self._providers:
            try:
                p.ping()
                result[p.name] = True
                self._brownout.pop(p.name, None)
            except Exception:
                result[p.name] = False
        return result

    @property
    def primary_provider(self) -> Optional[str]:
        """Name of the first non-browned-out provider, or None."""
        for p in self._providers:
            if not self._is_browned_out(p.name):
                return p.name
        return None

    @property
    def brownout_status(self) -> Dict[str, bool]:
        """Dict of {provider_name: is_browned_out}."""
        return {p.name: self._is_browned_out(p.name) for p in self._providers}

    def set_session_id(self, session_id: str) -> None:
        """Update the session id used for cost attribution."""
        self._session_id = session_id

    # ── Cost helpers ───────────────────────────────────────────────────────────

    def cost_summary(self, hours: int = 24) -> Dict:
        """Return cost/token totals for the last N hours across all providers."""
        if not self._cost:
            return {"error": "cost tracker unavailable"}
        return self._cost.summary(hours=hours)

    def session_cost(self) -> float:
        """Total USD cost attributed to the current session_id."""
        if not self._cost or not self._session_id:
            return 0.0
        return self._cost.session_cost(self._session_id)

    def cache_stats(self) -> Dict:
        """Return LLM response cache hit/miss stats."""
        if not self._cost:
            return {}
        return self._cost.cache_stats()
