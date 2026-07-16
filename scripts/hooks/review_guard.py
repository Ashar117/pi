#!/usr/bin/env python3
"""review_guard.py - deterministic Claude Code review guard for Project Pi.

Fast, dependency-free static checks on a changed Python source file. Covers the
*objectively checkable* subset of the review checklist:

  - Secrets             (regex for common credential shapes)
  - Error Handling      (bare `except:` / `except ...: pass`)
  - Hallucinated Imports (top-level imports that resolve to nothing)
  - Test Coverage       (source module with no testing/test_<name>.py)
  - Syntax              (file no longer parses)

The JUDGMENT dimensions - Edge Cases, Input Validation, Logging - are deliberately
NOT done here. They need an LLM and live in the /pi-review slash command. A regex
that pretends to check "edge cases" is just green-by-construction noise.

Usage:
  - As a Claude Code hook: reads the hook JSON from stdin, uses tool_input.file_path.
  - Manually: `python scripts/hooks/review_guard.py <file.py>`

Side effects: appends one findings record to logs/claude_review.jsonl. When there
are HIGH-severity findings it prints a hookSpecificOutput JSON so the findings are
injected back into the model's context. Never fails the hook (exit 0). Imports are
inspected via AST + find_spec only - no module code from the target file is run.
"""
import ast
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "logs" / "claude_review.jsonl"

SKIP_PARTS = ("pi_env", "__pycache__", ".git", ".claude")
SOURCE_DIRS = ("agent", "tools", "core", "scripts", "memory", "app")

# (name, regex, severity) - conservative, high-confidence credential shapes.
SECRET_PATTERNS = [
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "high"),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "high"),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "high"),
    ("github_fine_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"), "high"),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "high"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "high"),
    ("openai_anthropic_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "high"),
    ("generic_secret_assign", re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|token|access[_-]?key)\b"
        r"\s*[:=]\s*['\"]([^'\"]{8,})['\"]"), "medium"),
]
# values that look like placeholders / env lookups, not real secrets
_PLACEHOLDER = re.compile(
    r"(?i)(os\.|getenv|environ|<|>|your[_-]|example|placeholder|xxx|\*\*\*|\$\{|changeme|dummy|fake)")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan_secrets(text: str):
    out = []
    for name, rx, sev in SECRET_PATTERNS:
        for m in rx.finditer(text):
            if name == "generic_secret_assign":
                val = m.group(2)
                if _PLACEHOLDER.search(val) or _PLACEHOLDER.search(m.group(0)):
                    continue
            out.append({"category": "Secrets", "severity": sev,
                        "line": _line_of(text, m.start()), "detail": name})
    return out


def scan_ast(text: str, path: Path):
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        out.append({"category": "Syntax", "severity": "high",
                    "line": e.lineno or 0, "detail": f"file does not parse: {e.msg}"})
        return out  # can't do AST-based checks if it won't parse

    # Error handling: bare except / except that only swallows
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                out.append({"category": "Error Handling", "severity": "medium",
                            "line": node.lineno, "detail": "bare `except:`"})
            body = node.body
            if len(body) == 1 and (
                isinstance(body[0], ast.Pass)
                or (isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and body[0].value.value is Ellipsis)
            ):
                out.append({"category": "Error Handling", "severity": "low",
                            "line": node.lineno, "detail": "except body silently swallows"})

    # Hallucinated imports: top-level module names that resolve to nothing
    tops = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                tops.add((a.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                tops.add((node.module.split(".")[0], node.lineno))
    for name, lineno in sorted(tops):
        if not _resolvable(name):
            out.append({"category": "Hallucinated Imports", "severity": "high",
                        "line": lineno, "detail": f"`{name}` does not resolve (typo / not installed?)"})
    return out


def _resolvable(name: str) -> bool:
    if name in sys.stdlib_module_names:
        return True
    if (ROOT / f"{name}.py").exists() or (ROOT / name / "__init__.py").exists():
        return True
    try:
        return importlib.util.find_spec(name) is not None  # locates only; no target code runs
    except Exception:
        return False


def scan_test_coverage(path: Path):
    rel = path.relative_to(ROOT)
    if rel.parts and rel.parts[0] == "testing":
        return []
    if rel.parts and rel.parts[0] not in SOURCE_DIRS and len(rel.parts) > 1:
        return []  # only flag top-level source dirs and root modules
    test_file = ROOT / "testing" / f"test_{path.stem}.py"
    if not test_file.exists():
        return [{"category": "Test Coverage", "severity": "low", "line": 0,
                 "detail": f"no testing/test_{path.stem}.py"}]
    return []


def resolve_target() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return ""
    return ((data.get("tool_input") or {}).get("file_path") or "")


def main() -> int:
    target = resolve_target()
    if not target:
        return 0
    path = Path(target)
    if not path.is_absolute():
        path = (ROOT / path)
    try:
        path = path.resolve()
    except Exception:
        return 0

    if path.suffix != ".py" or not path.exists():
        return 0
    if any(p in path.parts for p in SKIP_PARTS):
        return 0
    # don't scan the guard's own dir (its secret patterns would self-trip)
    if "hooks" in path.parts and "scripts" in path.parts:
        return 0
    try:
        path.relative_to(ROOT)
    except ValueError:
        return 0

    text = path.read_text(encoding="utf-8", errors="replace")
    findings = scan_secrets(text) + scan_ast(text, path) + scan_test_coverage(path)

    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "file": rel, "n": len(findings), "findings": findings}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")

    # Inject only HIGH-severity findings back into the model's context.
    high = [x for x in findings if x["severity"] == "high"]
    if high:
        msg = "review_guard - HIGH-severity findings in " + rel + ":\n" + "\n".join(
            f"  [{x['category']}] line {x['line']}: {x['detail']}" for x in high)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": msg}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
