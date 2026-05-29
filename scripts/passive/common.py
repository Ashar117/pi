"""
scripts/passive/common.py — Shared utilities for all passive skills.

Every passive skill imports from here. Contains:
  - Status enum (PASS / WARN / FAIL / BLOCKED)
  - Canonical path constants
  - Git helpers (read-only)
  - JSONL reader/writer
  - Markdown report writer
  - Exit-code mapper
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Status ────────────────────────────────────────────────────────────────────

class Status(Enum):
    PASS    = "PASS"
    WARN    = "WARN"
    FAIL    = "FAIL"
    BLOCKED = "BLOCKED"


# ── Canonical paths ───────────────────────────────────────────────────────────

# common.py lives at  <ROOT>/scripts/passive/common.py
#   parent            → scripts/passive
#   parent.parent     → scripts
#   parent.parent.parent → ROOT (e:\pi)
ROOT           = Path(__file__).resolve().parent.parent.parent
REPORTS        = ROOT / "reports"
ANALYSIS       = ROOT / "analysis"
TICKETS_OPEN   = ROOT / "tickets" / "open"
TICKETS_CLOSED = ROOT / "tickets" / "closed"
SOLUTIONS      = ROOT / "solutions" / "SOLUTIONS.jsonl"
STATUS_MD      = ROOT / "docs" / "STATUS.md"
CHECKPOINTS    = ROOT / "CHECKPOINTS" / "current.md"


# ── Git helpers (read-only) ───────────────────────────────────────────────────

def run_git(args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a git command in ROOT; always returns CompletedProcess (never raises)."""
    try:
        return subprocess.run(
            ["git"] + args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        # Return a fake failed result so callers can check returncode safely.
        result = subprocess.CompletedProcess(args=["git"] + args, returncode=1)
        result.stdout = ""
        result.stderr = str(exc)
        return result


def git_ls_files() -> List[str]:
    """Return sorted list of all files tracked by git."""
    r = run_git(["ls-files"])
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


def git_staged_files() -> List[str]:
    """Return list of files currently staged (index vs HEAD)."""
    r = run_git(["diff", "--cached", "--name-only"])
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


def git_status_short() -> str:
    """Return `git status --short` output (empty string = clean tree)."""
    r = run_git(["status", "--short"])
    return r.stdout.strip() if r.returncode == 0 else ""


def git_check_ignore(path: str) -> bool:
    """Return True if *path* is matched by a .gitignore rule."""
    r = run_git(["check-ignore", "-q", path])
    return r.returncode == 0


# ── JSONL helpers ─────────────────────────────────────────────────────────────

def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL file; silently skip blank lines and malformed JSON."""
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            items.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return items


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """Append one JSON record to a JSONL file (creates file if missing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(filename: str, content: str, status: Status) -> Path:
    """Write a markdown skill report to reports/<filename>.

    The file always starts with a standard header containing the status and
    ISO timestamp, followed by the caller-supplied *content*.

    Returns the Path of the written file.
    """
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / filename

    title = (
        filename.removesuffix(".md")
               .replace("_", " ")
               .title()
    )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    header = (
        f"# {title}\n"
        f"**Status:** {status.value}  \n"
        f"**Generated:** {ts}\n\n"
        "---\n\n"
    )
    path.write_text(header + content, encoding="utf-8")
    return path


# ── Exit code ─────────────────────────────────────────────────────────────────

def status_to_exit_code(status: Status) -> int:
    """Map Status → standard exit code (0=PASS, 1=WARN, 2=FAIL/BLOCKED)."""
    return {
        Status.PASS:    0,
        Status.WARN:    1,
        Status.FAIL:    2,
        Status.BLOCKED: 2,
    }[status]


# ── Convenience ───────────────────────────────────────────────────────────────

def worst(statuses: List[Status]) -> Status:
    """Return the most severe Status from a list; defaults to PASS."""
    order = [Status.PASS, Status.WARN, Status.FAIL, Status.BLOCKED]
    if not statuses:
        return Status.PASS
    return max(statuses, key=lambda s: order.index(s) if s in order else 0)
