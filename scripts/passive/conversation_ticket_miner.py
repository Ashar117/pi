"""
scripts/passive/conversation_ticket_miner.py — SKILL 14 (in registered list; the digest itself is unnumbered)

Mines the last 24h of conversation transcript (logs/turns.jsonl) for fixes,
decisions, and recurring complaints that did not become tickets. Writes
candidates to analysis/conversation_candidates.jsonl. NEVER opens tickets
automatically — surfaces them for human review.

Checks:
  1. Tail-stream last 24h from logs/turns.jsonl
  2. Ask Groq llama-3.3-70b to extract candidates
  3. Dedup against existing ticket titles in tickets/open|closed
  4. Append fresh candidates to analysis/conversation_candidates.jsonl
  5. WARN if 1-3 fresh candidates, FAIL if 4+

Output: reports/conversation_ticket_miner.md

CLI:
  python scripts/passive/conversation_ticket_miner.py --check
  python scripts/passive/conversation_ticket_miner.py --strict
  python scripts/passive/conversation_ticket_miner.py --quiet
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    REPORTS as _DEFAULT_REPORTS,
    Status,
    write_report,
    status_to_exit_code,
)

REPORT_FILE = "conversation_ticket_miner.md"
CANDIDATES_FILE = "analysis/conversation_candidates.jsonl"

_PROMPT = """You are reviewing the last 24 hours of a developer's conversation with their AI assistant.
Extract ONLY items that are clearly:
  (a) Fixes/changes that were applied (so a ticket should have been filed)
  (b) Decisions about future work (so it should become a tracked ticket)
  (c) Recurring complaints from the user about a real problem

Skip casual chat, speculation, or things already framed as "we could maybe...".
For each item, output a one-line title (≤80 chars) and a 1-sentence rationale.

Conversation excerpt (last 24h, truncated to 60 turns):
{excerpt}

Output JSON ONLY (no preamble). Example:
{{
  "candidates": [
    {{"title": "Fix vision provider chain to fall back on 429", "rationale": "User hit Gemini quota; no fallback existed."}},
    {{"title": "...", "rationale": "..."}}
  ]
}}

If nothing meaningful, output {{"candidates": []}}."""


def _ensure_env_loaded():
    if os.environ.get("GROQ_API_KEY"):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(_DEFAULT_ROOT / ".env")
    except Exception:
        pass


def _tail_turns(root: Path, hours: int = 24, max_turns: int = 60) -> List[dict]:
    """Read the last N hours of turns.jsonl, capped at max_turns."""
    log_path = root / "logs" / "turns.jsonl"
    if not log_path.exists():
        return []
    try:
        from agent.turn_log import _tail_jsonl
        # Read up to 2 MB tail; filter by ts
        raw = _tail_jsonl(log_path, n=max_turns * 2)
    except Exception:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for entry in raw:
        ts_str = entry.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts > cutoff:
                out.append(entry)
        except (ValueError, TypeError):
            continue
    return out[-max_turns:]


def _existing_titles(root: Path) -> List[str]:
    titles = []
    for sub in ("open", "closed"):
        for p in (root / "tickets" / sub).glob("T-*.json"):
            try:
                t = json.loads(p.read_text(encoding="utf-8"))
                title = t.get("title", "")
                if title:
                    titles.append(title.lower())
            except Exception:
                continue
    return titles


def _cosine_naive(a: str, b: str) -> float:
    """Cheap word-overlap proxy for cosine. 0..1; not real cosine."""
    aw = set(re.findall(r"\w+", a.lower()))
    bw = set(re.findall(r"\w+", b.lower()))
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / max(len(aw), len(bw))


def _dedup_against_existing(candidates: List[dict], existing: List[str], threshold: float = 0.7) -> List[dict]:
    fresh = []
    for c in candidates:
        title = c.get("title", "").lower()
        if not title:
            continue
        is_dup = any(_cosine_naive(title, ex) >= threshold for ex in existing)
        if not is_dup:
            fresh.append(c)
    return fresh


def _build_excerpt(turns: List[dict]) -> str:
    lines = []
    for t in turns:
        role = "user" if t.get("user_input") else "pi"
        text = t.get("user_input") or t.get("response_preview") or ""
        text = text.replace("\n", " ")[:300]
        lines.append(f"[{role}] {text}")
    return "\n".join(lines)


def _call_groq(prompt: str) -> List[dict]:
    """Returns list of candidate dicts, or [] on failure."""
    _ensure_env_loaded()
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return []
    try:
        from groq import Groq
        client = Groq(api_key=key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip()
        # Strip code fences if present
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        data = json.loads(text)
        return data.get("candidates", [])
    except Exception:
        return []


def mine_candidates(root: Path) -> Tuple[List[dict], List[dict]]:
    """Return (all_extracted, fresh_after_dedup)."""
    turns = _tail_turns(root)
    if not turns:
        return [], []
    excerpt = _build_excerpt(turns)
    extracted = _call_groq(_PROMPT.format(excerpt=excerpt))
    existing = _existing_titles(root)
    fresh = _dedup_against_existing(extracted, existing)
    return extracted, fresh


def _append_candidates(root: Path, candidates: List[dict]) -> Path:
    path = root / CANDIDATES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with open(path, "a", encoding="utf-8") as f:
        for c in candidates:
            record = {"discovered_at": ts, **c}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def run_check(
    strict: bool = False,
    root: Path = _DEFAULT_ROOT,
    reports: Path = _DEFAULT_REPORTS,
) -> Status:
    extracted, fresh = mine_candidates(root)

    if extracted is None:
        status = Status.BLOCKED
        body = "## 1. Conversation Mining\n\n**Result:** BLOCKED — Groq unavailable.\n"
        write_report(REPORT_FILE, body, status)
        return status

    if fresh:
        _append_candidates(root, fresh)

    n_fresh = len(fresh)
    if n_fresh == 0:
        status = Status.PASS
    elif n_fresh <= 3:
        status = Status.WARN
    else:
        status = Status.FAIL

    if strict and status == Status.WARN:
        status = Status.FAIL

    body_lines = ["## 1. Conversation Mining  \n", f"**Result:** {status.value}\n"]
    body_lines.append(f"- Turns analysed (last 24h): {len(_tail_turns(root))}")
    body_lines.append(f"- Candidates extracted: {len(extracted)}")
    body_lines.append(f"- Fresh after dedup: {len(fresh)}")
    if fresh:
        body_lines.append(f"\n**Fresh candidates** (written to `{CANDIDATES_FILE}`):\n")
        for c in fresh:
            body_lines.append(f"- **{c.get('title','?')}** — {c.get('rationale','')}")

    summary = (
        "## Summary\n\n"
        f"- Overall: **{status.value}**\n"
        + (f"- Mode: `--strict` (WARN -> FAIL)\n" if strict else "")
        + "\n"
    )
    write_report(REPORT_FILE, summary + "\n".join(body_lines), status)
    return status


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Passive Skill 15 — conversation ticket miner")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    status = run_check(strict=args.strict)
    if not args.quiet:
        icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]", "BLOCKED": "[BLOCKED]"}.get(status.value, "[?]")
        print(f"[conversation_ticket_miner] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")
    sys.exit(status_to_exit_code(status))
