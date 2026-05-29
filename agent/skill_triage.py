"""agent/skill_triage.py — LLM-backed triage for passive observer skills.

Passive skills currently report raw metric counts (e.g. "67 TODOs"). This
module lets them call an LLM to triage those findings: cluster, prioritise,
identify what's actually actionable vs. what's noise. The LLM never decides
PASS/WARN/FAIL — that stays with the skill's threshold logic. Triage only
adds a "What matters" section to the report.

Provider chain: Groq llama-3.3-70b (free) → Claude Haiku (paid fallback)
→ degrade silently (skill keeps its original output).

Usage:
    from agent.skill_triage import triage

    triage_md = triage(
        skill_name="tech_debt",
        findings_summary="67 TODO/FIXME comments, 12 skipped tests, ...",
        raw_lines=["- tools/foo.py:42 — TODO fix race", ...],
        question="Which 3-5 items actually warrant attention?",
    )
    if triage_md:
        section_texts.append(triage_md)

Failures are swallowed and recorded via track_silent; the helper returns
"" (empty string) and the caller falls back to its old behavior.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


def _ensure_env_loaded() -> None:
    """Load .env if neither API key is in the env yet (standalone skill scripts
    don't import the app's bootstrapping, so dotenv may not have run)."""
    if os.environ.get("GROQ_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        from dotenv import load_dotenv
        # walk up from this file to find .env
        here = Path(__file__).resolve().parent
        for p in (here, here.parent):
            env_path = p / ".env"
            if env_path.exists():
                load_dotenv(str(env_path))
                return
    except Exception:
        pass


_TRIAGE_PROMPT = """You are a code-review triage assistant for an autonomous AI agent ("Pi").
You are reviewing the output of a passive health-check skill. The skill found
the items below. Your job: identify the 3-5 items that ACTUALLY MATTER — the
ones a busy engineer should look at first — and dismiss the rest as noise.

Skill: {skill_name}
Summary: {summary}

Raw findings (truncated to first 40 lines):
{lines}

{question}

Reply in this exact markdown format (no preamble, no other sections):

**Top concerns:**
- <item 1, with file/path if relevant — 1 short sentence on why it matters>
- <item 2>
- <item 3>

**Dismissed as noise:** <one short sentence summarising what you're choosing to ignore and why>

**Recommended action:** <one concrete sentence the engineer can act on, or "No action — counts are within normal operating range">

If there are no real concerns, write "**Top concerns:** None" and explain in one line."""


def _truncate_lines(lines: List[str], n: int = 40) -> str:
    cleaned = [str(l) for l in lines if str(l).strip()]
    if len(cleaned) <= n:
        return "\n".join(cleaned)
    return "\n".join(cleaned[:n]) + f"\n... ({len(cleaned) - n} more)"


def _try_groq(prompt: str, max_tokens: int = 400) -> Optional[str]:
    _ensure_env_loaded()
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return None


def _try_haiku(prompt: str, max_tokens: int = 400) -> Optional[str]:
    _ensure_env_loaded()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip() if resp.content else None
    except Exception:
        return None


def triage(
    skill_name: str,
    findings_summary: str,
    raw_lines: List[str],
    question: str = "Which items warrant attention vs. noise?",
    use_haiku: bool = False,
) -> str:
    """Return a markdown triage section, or "" if no provider is available.

    Args:
        skill_name:        passive skill name (for the prompt context).
        findings_summary:  one-line summary of what the skill found.
        raw_lines:         raw bullet items the skill produced.
        question:          override the triage question if you want skill-specific phrasing.
        use_haiku:         if True, skip Groq and go straight to Haiku (use for deeper analysis).

    The returned section is already wrapped with a heading; the caller just
    appends it to section_texts. Empty string means "no triage available —
    fall back to raw output without breaking".
    """
    prompt = _TRIAGE_PROMPT.format(
        skill_name=skill_name,
        summary=findings_summary,
        lines=_truncate_lines(raw_lines),
        question=question,
    )

    summary = None
    if not use_haiku:
        summary = _try_groq(prompt)
    if summary is None:
        summary = _try_haiku(prompt)

    if not summary:
        try:
            from agent.observability import track_silent
            track_silent(
                "skill_triage.no_provider",
                RuntimeError(f"{skill_name}: both Groq and Haiku unavailable"),
            )
        except Exception:
            pass
        return ""

    return f"## Triage (LLM-prioritised)\n\n{summary}\n"


def deep_analysis(
    skill_name: str,
    context: str,
    question: str,
    max_tokens: int = 800,
) -> str:
    """Deeper Haiku-only reasoning pass for skills that need cross-cutting analysis.

    Used by solution_lesson_distiller etc. where simple triage isn't enough —
    the LLM needs to read all the records and extract patterns across them.
    """
    prompt = f"""You are reviewing data from a passive observer skill running over Pi's engineering history.

Skill: {skill_name}

Data:
{context}

Question: {question}

Reply in clean markdown. Be specific: cite IDs, dates, or file paths where relevant.
Aim for 3-6 short bullet points. No preamble."""

    summary = _try_haiku(prompt, max_tokens=max_tokens)
    if not summary:
        try:
            from agent.observability import track_silent
            track_silent(
                "skill_triage.deep_failed",
                RuntimeError(f"{skill_name}: Haiku deep analysis unavailable"),
            )
        except Exception:
            pass
        return ""
    return f"## Deep Analysis (Haiku)\n\n{summary}\n"
