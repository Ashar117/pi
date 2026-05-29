"""
scripts/sanity_check_normie.py — T-094

Verifies that normie-mode traffic actually routes through Cerebras
(not silently falling back to Groq due to a wire-up bug).

Run:
    python scripts/sanity_check_normie.py

Exits 0 on success, 1 on failure.
Output format: one result line per check, PASS/FAIL/WARN prefix.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import CEREBRAS_API_KEY, GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY
from core.llm_router import LLMRouter, LLMResponse

PROMPT = "Reply with exactly one word: ready"
EXPECTED_PROVIDER = "cerebras"


def _build_router() -> LLMRouter:
    from app.config import ANTHROPIC_API_KEY
    return LLMRouter(
        anthropic_key=ANTHROPIC_API_KEY or "",
        groq_key=GROQ_API_KEY or "",
        gemini_key=GEMINI_API_KEY or "",
        cerebras_key=CEREBRAS_API_KEY or "",
        openrouter_key=OPENROUTER_API_KEY or "",
    )


def check_cerebras_key() -> bool:
    ok = bool(CEREBRAS_API_KEY)
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  CEREBRAS_API_KEY set: {ok}")
    return ok


def check_normie_routes_cerebras() -> tuple[bool, str]:
    router = _build_router()
    messages = [{"role": "user", "content": PROMPT}]
    t0 = time.time()
    try:
        resp: LLMResponse = router.chat(
            messages=messages,
            system="You are Pi, a helpful assistant.",
            tools=[],
            max_tokens=16,
            tier="cheap",
        )
    except Exception as e:
        print(f"  FAIL  router.chat(tier='cheap') raised: {e}")
        return False, "error"

    elapsed = time.time() - t0
    provider = resp.provider
    response_len = len(resp.text or "")

    if provider == EXPECTED_PROVIDER:
        print(f"  PASS  provider={provider} response_len={response_len} ({elapsed:.1f}s)")
        return True, provider
    else:
        print(f"  WARN  provider={provider} (expected {EXPECTED_PROVIDER}) — "
              f"Cerebras may be down, check key/quota. response_len={response_len} ({elapsed:.1f}s)")
        return False, provider


def check_response_shape(resp_text: str) -> bool:
    ok = isinstance(resp_text, str) and len(resp_text) > 0
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  response shape ok (non-empty string): {ok}")
    return ok


def main() -> int:
    print("=== normie provider sanity check (T-094) ===")
    failures = 0

    if not check_cerebras_key():
        failures += 1

    ok, provider = check_normie_routes_cerebras()
    if not ok:
        failures += 1
        if provider == "error":
            print("  INFO  router failed entirely — check API keys and network")
        else:
            print(f"  INFO  fell back to {provider} — Cerebras unreachable or key invalid")

    print()
    if failures == 0:
        print("RESULT: PASS — Cerebras is live and serving normie traffic")
        return 0
    else:
        print(f"RESULT: FAIL — {failures} check(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
