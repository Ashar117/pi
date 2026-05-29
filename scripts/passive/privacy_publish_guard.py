"""
scripts/passive/privacy_publish_guard.py — SKILL 1

Passive privacy and publish-safety guard.  Scans for public/private
boundary violations before any commit/push.  NEVER auto-fixes anything.

Checks:
  1. Private implementation files tracked by git         → FAIL
  2. Private data/logs/vault content tracked             → FAIL / WARN
  3. Credential patterns in staged or public-doc files   → FAIL / WARN
  4. Private-mode references in public docs              → WARN
  5. .gitignore inline-comment bugs                      → WARN
  6. Files tracked despite matching a .gitignore rule    → WARN

CLI:
  python scripts/passive/privacy_publish_guard.py --check
  python scripts/passive/privacy_publish_guard.py --strict   # WARN → FAIL
  python scripts/passive/privacy_publish_guard.py --quiet    # exit code only
  python scripts/passive/privacy_publish_guard.py --help
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from scripts.passive.common import (
    ROOT as _DEFAULT_ROOT,
    Status,
    git_ls_files,
    git_staged_files,
    run_git,
    write_report,
    status_to_exit_code,
    worst,
)

REPORT_FILE = "privacy_publish_guard.md"

# ── Private path definitions ──────────────────────────────────────────────────

# Files / prefixes that must NEVER appear in `git ls-files` (FAIL)
PRIVATE_IMPL_PREFIXES: List[str] = [
    "pi_agent.py",
    "agent/",
    "tools/",
    "prompts/",
    "core/",
    "memory/",
    "llm/",
    "app/",
    "testing/",
    "requirements.txt",
]

# scripts/ is private except the passive skill-pack itself
SCRIPTS_EXEMPT_PREFIX = "scripts/passive/"

# Private data paths — any tracked file under these → FAIL
PRIVATE_DATA_PREFIXES: List[str] = [
    "logs/",
    "local_models/",
    "vault/memory/",
    "vault/notes/per-ticket/",
    "vault/.god/",
    "data/pi.db",
    "data/god_memory.db",
]

# data/ dir: README is a WARN (tracked-despite-gitignore), everything else FAIL
DATA_PREFIX = "data/"
DATA_ALLOWLIST = {"data/README.md"}

# Names of public doc files to scan for credential/private-mode patterns
PUBLIC_DOC_NAMES: List[str] = ["README.md", "ABOUT.md", "PI.md", "CLAUDE.md"]

# Credential patterns. Each tuple is (pattern, description, needs_value_filter).
# T-157: the assignment pattern (needs_value_filter=True) captures the RHS in a
# `val` group and only flags it as a secret if it is a quoted literal or a
# known key shape — NOT a bare variable reference like `api_key=CEREBRAS_API_KEY`
# or `token=os.environ["X"]`. The shape patterns (sk-/Bearer/JWT) are always real.
_SECRET_PATS: List[Tuple[re.Pattern, str, bool]] = [
    (re.compile(r'(?i)(?:api[_\-]?key|token|password|secret|passwd)\s*[=:]\s*(?P<val>["\']?\S{8,})'),
     "credential assignment", True),
    (re.compile(r'sk-[a-zA-Z0-9]{32,}'), "OpenAI-style API key", False),
    (re.compile(r'Bearer\s+[a-zA-Z0-9._\-]{20,}'), "Bearer token", False),
    (re.compile(r'eyJ[a-zA-Z0-9+/=]{10,}\.eyJ[a-zA-Z0-9+/=]{10,}'), "JWT token", False),
]

# Markers that make a value an obvious placeholder, never a real secret.
_PLACEHOLDER_MARKERS = (
    "your", "xxx", "changeme", "placeholder", "example", "redacted",
    "<", "${", "...", "***", "dummy", "fake", "test",
)

# Concrete secret shapes that ARE real even when unquoted (in code).
_KEY_SHAPES = re.compile(
    r'(sk-[a-zA-Z0-9]{16,}|eyJ[a-zA-Z0-9]|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|[a-f0-9]{32,})'
)


def _is_real_secret(raw_value: str) -> bool:
    """T-157: decide whether the RHS of a credential assignment is a real secret.

    Quoted literal of real length  → secret (unless it reads as a placeholder).
    Unquoted token                 → a code reference (VARNAME, obj.attr,
                                      os.environ[...]); only a secret if it
                                      matches a known key shape.
    """
    v = raw_value.strip()
    if not v:
        return False
    if v[0] in "\"'":
        inner = v[1:].rstrip("\"'")
        low = inner.lower()
        if any(mk in low for mk in _PLACEHOLDER_MARKERS):
            return False
        return len(inner) >= 8
    # Unquoted: a bare identifier / attribute / env lookup is not a secret.
    return bool(_KEY_SHAPES.search(v))

# Files allowed to mention private-mode terms (they document the architecture boundary)
PRIVATE_MODE_ALLOWLIST_FILES: set = {"PI.md", "FEATURE_LIST.md"}

# Private-mode references in public docs → WARN
_PRIVATE_MODE_PATS: List[re.Pattern] = [
    re.compile(r'\bgod[_ ]mode\b', re.IGNORECASE),
    re.compile(r'\bgod_consciousness\b', re.IGNORECASE),
    re.compile(r'agent[/\\]god\.py', re.IGNORECASE),
    re.compile(r'god_memory\.db', re.IGNORECASE),
]


# ── Individual checks (accept root so tests can inject tmp_path) ──────────────

def check_private_impl(tracked: List[str]) -> Tuple[Status, List[str]]:
    """FAIL if any private implementation path is tracked."""
    hits: List[str] = []
    for f in tracked:
        if f.startswith("scripts/") and not f.startswith(SCRIPTS_EXEMPT_PREFIX):
            hits.append(
                f"`{f}` — private script tracked  "
                f"*(fix: `git rm --cached {f}`)*"
            )
            continue
        for prefix in PRIVATE_IMPL_PREFIXES:
            if f == prefix or f.startswith(prefix):
                hits.append(
                    f"`{f}` — private implementation tracked  "
                    f"*(fix: `git rm --cached {f}`)*"
                )
                break
    if not hits:
        return Status.PASS, ["✅ No private implementation files tracked"]
    return Status.FAIL, hits


def check_private_data(tracked: List[str]) -> Tuple[Status, List[str]]:
    """FAIL for private data/logs/vault; WARN for data/README.md."""
    hits: List[str] = []
    local_statuses: List[Status] = []

    for f in tracked:
        matched = False
        for prefix in PRIVATE_DATA_PREFIXES:
            if f == prefix or f.startswith(prefix):
                hits.append(f"`{f}` — private data tracked *(fix: `git rm --cached {f}`)*")
                local_statuses.append(Status.FAIL)
                matched = True
                break
        if not matched and f.startswith(DATA_PREFIX):
            if f in DATA_ALLOWLIST:
                hits.append(
                    f"`{f}` — tracked despite `data/` being gitignored  "
                    f"*(consider: `git rm --cached {f}` or add exception)*"
                )
                local_statuses.append(Status.WARN)
            else:
                hits.append(f"`{f}` — private data file tracked *(fix: `git rm --cached {f}`)*")
                local_statuses.append(Status.FAIL)

    if not hits:
        return Status.PASS, ["✅ No private data files tracked"]
    return worst(local_statuses), hits


def check_secrets(
    staged: List[str],
    public_doc_paths: List[Path],
) -> Tuple[Status, List[str]]:
    """FAIL if credentials found in staged files; WARN in public docs."""
    hits: List[str] = []
    local_statuses: List[Status] = []

    def _scan(path: Path, label: str, severity: Status) -> None:
        if not path.exists() or path.stat().st_size > 500_000:
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for pat, desc, needs_filter in _SECRET_PATS:
            for m in pat.finditer(text):
                if needs_filter and not _is_real_secret(m.groupdict().get("val", "")):
                    continue  # T-157: variable reference, not a literal secret
                snippet = m.group(0)[:8] + "…"
                line_no = text[: m.start()].count("\n") + 1
                hits.append(
                    f"`{path.name}:{line_no}` — {desc}: `{snippet}` [{label}]  "
                    f"*(do not commit this file)*"
                )
                local_statuses.append(severity)

    staged_set = set(staged)
    # Check staged files first (about to be committed) → FAIL
    for rel in staged:
        _scan(Path(rel) if Path(rel).is_absolute() else Path.cwd() / rel,
              "staged", Status.FAIL)

    # Check public docs not already staged → WARN
    for p in public_doc_paths:
        if p.name not in staged_set:
            _scan(p, "public doc", Status.WARN)

    if not hits:
        return Status.PASS, ["✅ No credential patterns found in staged/public files"]
    return worst(local_statuses), hits


def check_private_mode_refs(public_doc_paths: List[Path]) -> Tuple[Status, List[str]]:
    """WARN if private-mode strings appear in public docs."""
    hits: List[str] = []

    for path in public_doc_paths:
        if path.name in PRIVATE_MODE_ALLOWLIST_FILES:
            continue  # architecture docs may document private components
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat in _PRIVATE_MODE_PATS:
            for m in pat.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                hits.append(
                    f"`{path.name}:{line_no}` — private reference: `{m.group(0)}`  "
                    f"*(review: may be acceptable architecture mention)*"
                )

    if not hits:
        return Status.PASS, ["✅ No private mode references found in public docs"]
    return Status.WARN, hits


def check_gitignore_inline_comments(gitignore_path: Path) -> Tuple[Status, List[str]]:
    """WARN if .gitignore has inline # comments (they break pattern matching)."""
    if not gitignore_path.exists():
        return Status.WARN, ["⚠ `.gitignore` not found"]

    hits: List[str] = []
    for i, line in enumerate(
        gitignore_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # blank or pure comment line — fine
        if "#" in stripped:
            hits.append(
                f"`.gitignore:{i}` — inline comment breaks pattern: `{line.rstrip()}`  "
                f"*(move comment to its own line)*"
            )

    if not hits:
        return Status.PASS, ["✅ No inline comment bugs in `.gitignore`"]
    return Status.WARN, hits


def check_tracked_but_ignored() -> Tuple[Status, List[str]]:
    """WARN if tracked files match active .gitignore rules."""
    r = run_git(["ls-files", "--cached", "--ignored", "--exclude-standard"])
    if r.returncode != 0:
        # git unavailable or flags not supported — degrade gracefully
        return Status.BLOCKED, [
            "⚠ `git ls-files -i` failed — skipping tracked-vs-ignored check"
        ]

    flagged = [f for f in r.stdout.splitlines() if f.strip()]
    if not flagged:
        return Status.PASS, ["✅ No tracked files match .gitignore rules"]

    hits = [
        f"`{f}` — tracked despite .gitignore rule  "
        f"*(fix: `git rm --cached {f}`)*"
        for f in flagged
    ]
    return Status.WARN, hits


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_check(strict: bool = False, root: Path = _DEFAULT_ROOT) -> Status:
    """Run all six privacy checks; write report; return overall Status."""
    # Collect public doc paths from the given root (allows test injection)
    public_doc_paths: List[Path] = []
    for name in PUBLIC_DOC_NAMES:
        p = root / name
        public_doc_paths.append(p)
    docs_dir = root / "docs"
    if docs_dir.exists():
        public_doc_paths.extend(docs_dir.rglob("*.md"))

    try:
        tracked = git_ls_files()
        staged = git_staged_files()
    except Exception as exc:
        msg = f"BLOCKED: git unavailable — {exc}"
        write_report(REPORT_FILE, msg, Status.BLOCKED)
        return Status.BLOCKED

    # Resolve staged paths relative to root for content scanning
    staged_paths: List[Path] = [root / f for f in staged]

    checks = [
        ("## 1. Private Implementation Tracked",
         lambda: check_private_impl(tracked)),
        ("## 2. Private Data / Logs Tracked",
         lambda: check_private_data(tracked)),
        ("## 3. Credential Patterns",
         lambda: check_secrets(staged, public_doc_paths)),
        ("## 4. Private-Mode References in Public Docs",
         lambda: check_private_mode_refs(public_doc_paths)),
        ("## 5. `.gitignore` Inline-Comment Bugs",
         lambda: check_gitignore_inline_comments(root / ".gitignore")),
        ("## 6. Tracked Files That Match `.gitignore`",
         check_tracked_but_ignored),
    ]

    section_texts: List[str] = []
    all_statuses: List[Status] = []

    for heading, fn in checks:
        status, lines = fn()
        all_statuses.append(status)
        section_texts.append(f"{heading}  \n**Result:** {status.value}\n")
        for line in lines:
            section_texts.append(f"- {line}")
        section_texts.append("")

    overall = worst(all_statuses)
    if strict and overall == Status.WARN:
        overall = Status.FAIL

    summary = (
        "## Summary\n\n"
        f"- Tracked files: {len(tracked)}\n"
        f"- Staged files: {len(staged)}"
        + (" *(nothing staged)*" if not staged else "") + "\n"
        f"- Overall: **{overall.value}**\n"
        + (f"- Mode: `--strict` (WARN → FAIL)\n" if strict else "")
        + "\n"
    )

    write_report(REPORT_FILE, summary + "\n".join(section_texts), overall)
    return overall


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    args = sys.argv[1:]
    if "--help" in args:
        print(__doc__)
        return 0

    strict = "--strict" in args
    quiet  = "--quiet" in args

    status = run_check(strict=strict)

    if not quiet:
        icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]",
                "BLOCKED": "[BLOCKED]"}.get(status.value, "[?]")
        print(f"[privacy_publish_guard] {icon} {status.value}")
        print(f"  Report: reports/{REPORT_FILE}")

    return status_to_exit_code(status)


if __name__ == "__main__":
    sys.exit(main())
