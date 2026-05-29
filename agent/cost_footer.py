"""agent/cost_footer.py — T-130: per-turn inline cost + token footer.

Pure formatter + one env check. Used by pi_agent._respond_via_config at the
end of each public-mode turn. Output goes to stderr (NEVER into final_text)
so Telegram + voice paths never see the footer.

ENV: PI_SHOW_COST=on enables. Default off.

Format:
    [$0.0034 · 8120 tok in · 1240 out · sonnet · 1.4s]
"""
from __future__ import annotations

import os
import sys
from typing import Optional


_ENV_FLAG = "PI_SHOW_COST"


def is_enabled() -> bool:
    """True when PI_SHOW_COST=on. Anything else (unset, '0', 'off') → False."""
    return os.environ.get(_ENV_FLAG, "").lower() == "on"


def format_cost_footer(
    cost: float,
    tokens_in: int,
    tokens_out: int,
    model: str,
    duration_s: float,
) -> str:
    """Render the one-line footer. Pure function — easy to unit-test.

    `model` may include a provider prefix ("anthropic/claude-sonnet-4-6"); we
    keep it as-is so the user can see which provider/router branch served the
    turn. Cost is in USD, formatted to 4 decimal places.
    """
    return (
        f"[${cost:.4f} · {int(tokens_in)} tok in · "
        f"{int(tokens_out)} out · {model or 'unknown'} · "
        f"{duration_s:.1f}s]"
    )


def emit_if_enabled(
    cost: float,
    tokens_in: int,
    tokens_out: int,
    model: str,
    duration_s: float,
    *,
    stream=None,
) -> Optional[str]:
    """If PI_SHOW_COST=on, print the footer to stderr and return the line.

    Never raises — a print failure (closed stream, encoding) must not break
    the turn. Caller already has the data; this is observability only.

    `stream` override exists for unit tests to capture output without
    swapping sys.stderr globally.
    """
    if not is_enabled():
        return None
    try:
        line = format_cost_footer(cost, tokens_in, tokens_out, model, duration_s)
        print(line, file=stream or sys.stderr, flush=True)
        return line
    except Exception:
        return None
