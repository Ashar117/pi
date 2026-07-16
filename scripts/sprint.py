#!/usr/bin/env python3
"""
scripts/sprint.py — Autonomous ticket runner for Pi (T-043).

Picks an open ticket, generates a plan via Claude, optionally auto-implements
it with Claude's tool-use loop, runs verify, closes the ticket on a branch.

USAGE
-----
    # Plan-only (default, safe). Generates a plan, writes to logs/sprint/<id>/.
    python scripts/sprint.py

    # Auto-implement, but only for safe components (scripts/, testing/, docs/).
    python scripts/sprint.py --auto-implement

    # Different limits and dry-run options.
    python scripts/sprint.py --max-tickets 3 --max-cost 1.00
    python scripts/sprint.py --dry-run            # plan only, no commit
    python scripts/sprint.py --ticket T-042       # force a specific ticket

GUARDRAILS
----------
* `--max-tickets N` (default 1) — hard cap on tickets per run.
* `--max-cost USD` (default $0.50) — stops when cumulative spend exceeds.
* 15-minute hard wall-clock timeout per ticket.
* RISK_FLAGGED components (pi_agent.py, agent/, prompts/, app/config.py,
  requirements.txt, memory/) NEVER auto-implement — always escalate.
* Only commits to a NEW branch `sprint/T-NNN-slug`. Never main, never push.
* On verify FAIL: 1 retry with a focused fix prompt; if still failing, escalate.

ESCALATION
----------
Sends a Telegram message (if configured) summarising plan, status, branch.
Marks ticket field `status="escalated"` so the runner skips it next time.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Config ───────────────────────────────────────────────────────────────────

PI_MD = ROOT / "PI.md"
TICKETS_OPEN = ROOT / "tickets" / "open"
TICKETS_CLOSED = ROOT / "tickets" / "closed"
SOLUTIONS = ROOT / "solutions" / "SOLUTIONS.jsonl"
SPRINT_LOG_DIR = ROOT / "logs" / "sprint"
VERIFY_SCRIPT = ROOT / "scripts" / "verify.py"

# Components that ALWAYS escalate (never auto-implement)
RISK_FLAGGED = [
    "pi_agent.py",
    "agent/tools.py",
    "agent/prompt.py",
    "prompts/consciousness.txt",
    "app/config.py",
    "requirements.txt",
    "memory/",
    ".env",
]

# Component prefixes safe for auto-implement
SAFE_COMPONENTS = [
    "scripts/",
    "testing/",
    "docs/",
    "vault/",
]

# Anthropic model + cost rates
CLAUDE_MODEL = "claude-sonnet-4-6"
COST_PER_MILLION_IN = 0.80
COST_PER_MILLION_OUT = 4.00

PER_TICKET_TIMEOUT_S = 15 * 60
MAX_TOOL_LOOP_ITERATIONS = 30


# ── Data classes ─────────────────────────────────────────────────────────────

class SprintError(Exception):
    """Raised when the runner can't continue with a ticket."""


# ── Ticket selection ─────────────────────────────────────────────────────────

def load_ticket(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def list_open_tickets() -> List[Dict]:
    """Return all parseable open tickets, sorted by severity then created."""
    if not TICKETS_OPEN.exists():
        return []

    tickets: List[Tuple[Path, Dict]] = []
    for p in TICKETS_OPEN.glob("*.json"):
        data = load_ticket(p)
        if not data:
            continue
        if data.get("status") == "escalated":
            continue
        data["_path"] = str(p)
        tickets.append((p, data))

    sev_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
    tickets.sort(key=lambda x: (
        sev_rank.get(x[1].get("severity", "P3"), 3),
        x[1].get("created", ""),
    ))
    return [t[1] for t in tickets]


def ticket_confidence(ticket: Dict) -> str:
    """T-154: classify a ticket's root-cause confidence for autonomy gating.

    Explicit `root_cause_confidence` wins (first token, so
    "hypothesis (original)..." → "hypothesis"). Otherwise infer: a Pi
    self-report is a HYPOTHESIS (its suggested fix may target the wrong
    cause — see T-143), while any other source defaults to "verified" so
    legacy human-curated tickets keep auto-running.
    """
    explicit = (ticket.get("root_cause_confidence") or "").strip().lower()
    if explicit:
        return explicit.split()[0]
    src = (ticket.get("source") or "").lower()
    if "self-report" in src:
        return "hypothesis"
    return "verified"


def pick_ticket(forced_id: Optional[str] = None) -> Optional[Dict]:
    """Pick highest-priority open ticket. If forced_id given, return that one.

    Auto-selection (no forced_id) refuses any ticket whose root cause is not
    verified (T-154): a wrong self-diagnosis + green verify would falsely
    "close" a bug. Such tickets need a human to confirm the cause (or attach a
    reproducing test) before the runner will implement them. A forced_id
    bypasses this — Ash explicitly choosing a ticket is the human confirmation.
    """
    tickets = list_open_tickets()
    if forced_id:
        for t in tickets:
            if t.get("id") == forced_id:
                return t
        # Search closed too in case Ash typed an already-closed ticket
        for p in TICKETS_CLOSED.glob(f"{forced_id}-*.json"):
            data = load_ticket(p)
            if data:
                data["_path"] = str(p)
                return data
        return None
    for t in tickets:
        if ticket_confidence(t) == "verified":
            return t
        print(
            f"[sprint] skipping {t.get('id')}: root_cause_confidence="
            f"{ticket_confidence(t)!r} — needs human confirmation before "
            f"auto-implement (T-154). Force it explicitly to override.",
            file=sys.stderr,
        )
    return None


# ── Risk classification ──────────────────────────────────────────────────────

def is_risk_flagged(ticket: Dict) -> bool:
    component = ticket.get("component", "") or ""
    where = ticket.get("where_failed", "") or ""
    blob = f"{component} {where}"
    return any(rf in blob for rf in RISK_FLAGGED)


def is_safe_component(ticket: Dict) -> bool:
    component = ticket.get("component", "") or ""
    return any(component.startswith(safe) for safe in SAFE_COMPONENTS)


# ── Anthropic plumbing ───────────────────────────────────────────────────────

def _claude_client():
    try:
        import anthropic
        from app.config import ANTHROPIC_API_KEY
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception as e:
        raise SprintError(f"Anthropic SDK or API key unavailable: {e}")


def _cost(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in / 1_000_000 * COST_PER_MILLION_IN
            + tokens_out / 1_000_000 * COST_PER_MILLION_OUT)


# ── Plan generation ──────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are Pi's autonomous engineering planner.

Given an open ticket, produce a focused implementation plan in markdown:

## Plan for {ticket_id}: {title}

### Root cause
(1-2 sentences — what is actually broken, not just symptoms)

### Approach
(3-6 bullets — the concrete change you'd make)

### Files to touch
(bulleted list — every file path that needs editing or creating)

### Tests to add or update
(specific test names + what they assert)

### Risk
LOW / MEDIUM / HIGH — with one sentence why.

### Estimated effort
"trivial" / "small" / "medium" / "large".

Be precise. No prose padding. If the ticket is ambiguous, say so explicitly under
a final "### Questions" section instead of guessing."""


def generate_plan(client, ticket: Dict) -> Tuple[str, float]:
    """Use Claude to draft the plan. Returns (plan_text, cost_usd)."""
    # T-279: these are the fields every current ticket actually carries; the
    # old what_failed/where_failed/why_likely/suggested_fix schema died in
    # 2026-05 and rendered the evidence lines empty for every plan.
    plan_steps = "\n".join(f"- {s}" for s in ticket.get("migration_plan", []))
    user = (
        f"# Ticket {ticket.get('id', '?')}: {ticket.get('title', '')}\n\n"
        f"**Severity:** {ticket.get('severity', 'P3')}\n"
        f"**Component:** {ticket.get('component', '')}\n"
        f"**Current state (evidence):** {ticket.get('current_state', '')}\n"
        f"**Target state:** {ticket.get('target_state', '')}\n"
        f"**Filer's migration plan:**\n{plan_steps}\n"
        f"**Risk notes:** {ticket.get('risk_notes', '')}\n\n"
        "Produce the plan now."
    )

    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=PLANNER_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    cost = _cost(resp.usage.input_tokens, resp.usage.output_tokens)
    return text, cost


# ── Auto-implement (Claude tool-use loop) ────────────────────────────────────

CODER_SYSTEM = """You are Pi's autonomous code-editor.

You have a plan and a ticket. Use the provided tools to implement the change:
- read_file: inspect existing source
- write_file: create a new file
- edit_file: replace an exact string (must be unique in target file)
- run_bash: run a non-interactive shell command (tests, listing, etc.)

Rules you MUST follow:
1. Make the smallest correct change. Don't refactor unrelated code.
2. After editing, run the relevant tests to verify the change.
3. When the work is done AND tests pass, output exactly: DONE
4. If you hit something you can't safely do (e.g. network call, secret needed),
   output: ESCALATE: <one-line reason>
5. NEVER edit files in the RISK_FLAGGED list:
{risk_list}
6. NEVER run git push, git commit, rm, or anything destructive.
7. Stay focused. The ticket's component is "{component}" — work mostly there.

You have at most {max_iter} tool-loop iterations. Be efficient."""


CODER_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from the project workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create a new file with given content. Fails if file exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace old_str with new_str in path. old_str MUST be unique.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": "run_bash",
        "description": "Run a shell command; non-interactive only. Returns stdout/stderr.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]


def _safe_path(path: str) -> Path:
    """Resolve path inside ROOT; refuse paths that escape."""
    p = (ROOT / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        p.relative_to(ROOT)
    except ValueError:
        raise SprintError(f"path escapes workspace: {path}")
    return p


def _exec_tool(name: str, args: Dict, files_changed: set) -> str:
    if name == "read_file":
        p = _safe_path(args["path"])
        if not p.exists():
            return f"ERROR: file not found: {args['path']}"
        return p.read_text(encoding="utf-8", errors="replace")[:30000]

    if name == "write_file":
        p = _safe_path(args["path"])
        if p.exists():
            return f"ERROR: file already exists: {args['path']} (use edit_file)"
        # Block risky paths
        for rf in RISK_FLAGGED:
            if rf in str(p.relative_to(ROOT)).replace(os.sep, "/"):
                return f"ERROR: path is risk-flagged, cannot auto-write: {args['path']}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        files_changed.add(str(p.relative_to(ROOT)).replace(os.sep, "/"))
        return f"OK: wrote {len(args['content'])} chars to {args['path']}"

    if name == "edit_file":
        p = _safe_path(args["path"])
        if not p.exists():
            return f"ERROR: file not found: {args['path']}"
        for rf in RISK_FLAGGED:
            if rf in str(p.relative_to(ROOT)).replace(os.sep, "/"):
                return f"ERROR: path is risk-flagged, cannot auto-edit: {args['path']}"
        text = p.read_text(encoding="utf-8")
        if args["old_str"] not in text:
            return "ERROR: old_str not found"
        if text.count(args["old_str"]) > 1:
            return "ERROR: old_str matches multiple times — must be unique"
        new_text = text.replace(args["old_str"], args["new_str"], 1)
        p.write_text(new_text, encoding="utf-8")
        files_changed.add(str(p.relative_to(ROOT)).replace(os.sep, "/"))
        return f"OK: edited {args['path']}"

    if name == "run_bash":
        cmd = args["command"]
        forbidden = ["git push", "git commit", "rm -rf", "del /f", "format ", "shutdown"]
        if any(f in cmd for f in forbidden):
            return f"ERROR: command blocked by safety policy: {cmd}"
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                cwd=str(ROOT), timeout=120,
            )
            out = (r.stdout + "\n" + r.stderr).strip()
            return f"exit={r.returncode}\n{out[:8000]}"
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out (120s)"
        except Exception as e:
            return f"ERROR: {e}"

    return f"ERROR: unknown tool: {name}"


def auto_implement(client, ticket: Dict, plan: str, deadline_ts: float) -> Dict:
    """Drive Claude through the tool-use loop. Returns dict with status."""
    files_changed: set = set()
    cost = 0.0
    final_text = ""

    system = CODER_SYSTEM.format(
        risk_list="\n".join(f"  - {r}" for r in RISK_FLAGGED),
        component=ticket.get("component", "?"),
        max_iter=MAX_TOOL_LOOP_ITERATIONS,
    )

    user_first = (
        f"# Ticket {ticket.get('id')}: {ticket.get('title')}\n\n"
        f"**Current state (evidence):** {ticket.get('current_state', '')}\n"
        f"**Target state:** {ticket.get('target_state', '')}\n"
        f"**Risk notes:** {ticket.get('risk_notes', '')}\n\n"
        f"## Plan\n\n{plan}\n\n"
        f"Now implement. Begin."
    )

    messages = [{"role": "user", "content": user_first}]

    for it in range(MAX_TOOL_LOOP_ITERATIONS):
        if time.time() > deadline_ts:
            return {"status": "timeout", "files": sorted(files_changed),
                    "cost": cost, "iter": it, "final": final_text}

        try:
            resp = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=4000,
                system=system, tools=CODER_TOOLS, messages=messages,
            )
        except Exception as e:
            return {"status": "api_error", "error": str(e),
                    "files": sorted(files_changed), "cost": cost, "iter": it}

        cost += _cost(resp.usage.input_tokens, resp.usage.output_tokens)
        text_blocks = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        final_text = "\n".join(text_blocks)

        if final_text.strip().startswith("ESCALATE:"):
            return {"status": "escalated_by_model", "reason": final_text,
                    "files": sorted(files_changed), "cost": cost, "iter": it}
        if "DONE" in final_text and not tool_uses:
            return {"status": "done", "files": sorted(files_changed),
                    "cost": cost, "iter": it, "final": final_text}

        if not tool_uses:
            # Model said neither DONE nor ESCALATE and called no tools — stop.
            return {"status": "stalled", "files": sorted(files_changed),
                    "cost": cost, "iter": it, "final": final_text}

        # Append assistant turn + tool results
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            try:
                out = _exec_tool(tu.name, tu.input, files_changed)
            except SprintError as e:
                out = f"ERROR: {e}"
            except Exception as e:
                out = f"ERROR (unhandled): {e}"
            results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": out[:30000],
            })
        messages.append({"role": "user", "content": results})

    return {"status": "max_iter", "files": sorted(files_changed),
            "cost": cost, "iter": MAX_TOOL_LOOP_ITERATIONS, "final": final_text}


# ── Verify ───────────────────────────────────────────────────────────────────

def run_verify() -> Dict:
    try:
        r = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), "--quiet"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=PER_TICKET_TIMEOUT_S,
        )
        return {"success": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "verify timed out"}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


# ── Ticket lifecycle ─────────────────────────────────────────────────────────

def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower())[:60].strip("-") or "no-title"


def close_ticket(ticket: Dict, summary: str, files_changed: List[str]) -> str:
    """Append SOLUTIONS.jsonl, move ticket open→closed, return solution_id."""
    # Generate next solution id
    sol_id = "S-NEXT"
    if SOLUTIONS.exists():
        nums = []
        for line in SOLUTIONS.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r'.*"id":\s*"S-(\d+)"', line)
            if m:
                nums.append(int(m.group(1)))
        sol_id = f"S-{(max(nums) + 1) if nums else 1:03d}"

    sol_entry = {
        "id": sol_id,
        "ticket": ticket.get("id"),
        "title": ticket.get("title", "")[:100],
        "date": datetime.now(timezone.utc).isoformat(),
        "root_cause": (ticket.get("what_failed", "") or "")[:300],
        "fix": summary[:500],
        "files_changed": files_changed,
        "tests": "covered by verify.py PASS",
        "auto_runner": "scripts/sprint.py",
    }
    SOLUTIONS.parent.mkdir(parents=True, exist_ok=True)
    with open(SOLUTIONS, "a", encoding="utf-8") as f:
        f.write(json.dumps(sol_entry, ensure_ascii=False) + "\n")

    # Move ticket file
    src = Path(ticket.get("_path", ""))
    if src.exists():
        ticket["status"] = "closed"
        ticket["closed"] = datetime.now(timezone.utc).isoformat()
        ticket["linked_solution"] = sol_id
        TICKETS_CLOSED.mkdir(parents=True, exist_ok=True)
        dest = TICKETS_CLOSED / src.name
        # write updated, then remove source
        ticket_no_path = {k: v for k, v in ticket.items() if k != "_path"}
        dest.write_text(json.dumps(ticket_no_path, indent=2), encoding="utf-8")
        src.unlink()
    return sol_id


def commit_branch(ticket: Dict, files_changed: List[str], sol_id: str) -> str:
    """Create branch sprint/T-NNN-slug, stage tracked files, commit. NEVER pushes."""
    slug = _slugify(ticket.get("title", "no-title"))
    branch = f"sprint/{ticket.get('id', 'T-XXX')}-{slug}"

    subprocess.run(["git", "checkout", "-b", branch], cwd=str(ROOT), check=False,
                   capture_output=True, text=True)

    files = list({*files_changed, "solutions/SOLUTIONS.jsonl",
                  f"tickets/closed/{Path(ticket.get('_path', '')).name}",
                  "PI.md"})
    files = [f for f in files if (ROOT / f).exists()]

    if files:
        subprocess.run(["git", "add"] + files, cwd=str(ROOT), check=False,
                       capture_output=True, text=True)

    msg = (
        f"{ticket.get('id', 'T-XXX')}: {ticket.get('title', '')}\n\n"
        f"Auto-closed by scripts/sprint.py.\n"
        f"Solution: {sol_id}\n"
        f"Files: {', '.join(files)}\n"
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=str(ROOT), check=False,
                   capture_output=True, text=True)
    return branch


def escalate(ticket: Dict, reason: str, plan_path: Path) -> None:
    """Mark ticket as escalated and Telegram-notify."""
    src = Path(ticket.get("_path", ""))
    if src.exists() and src.parent == TICKETS_OPEN:
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            data["status"] = "escalated"
            data["escalated_at"] = datetime.now(timezone.utc).isoformat()
            data["escalated_reason"] = reason[:500]
            src.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    print(f"[sprint] ESCALATE {ticket.get('id')}: {reason}")

    try:
        from tools.tools_telegram import send_message
        try:
            plan_rel = plan_path.relative_to(ROOT)
        except ValueError:
            plan_rel = plan_path
        send_message(
            f"*Sprint runner escalation*\n"
            f"Ticket: {ticket.get('id')} {ticket.get('title', '')}\n"
            f"Reason: {reason}\n"
            f"Plan: `{plan_rel}`\n"
            f"Component: `{ticket.get('component', '?')}`"
        )
    except Exception:
        pass


def notify_success(ticket: Dict, branch: str, sol_id: str) -> None:
    print(f"[sprint] CLOSED {ticket.get('id')} on branch {branch} (solution {sol_id})")
    try:
        from tools.tools_telegram import send_message
        send_message(
            f"*Sprint runner closed a ticket*\n"
            f"{ticket.get('id')}: {ticket.get('title', '')}\n"
            f"Branch: `{branch}`\n"
            f"Solution: `{sol_id}`\n"
            f"Reply 'merge' to merge."
        )
    except Exception:
        pass


# ── Sprint log dir ───────────────────────────────────────────────────────────

def make_run_dir(ticket_id: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    d = SPRINT_LOG_DIR / f"{ts}-{ticket_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Orchestration ────────────────────────────────────────────────────────────

def run_one_ticket(args, ticket: Dict, cumulative_cost: float) -> Tuple[str, float]:
    """Process one ticket. Returns (status, cost_added). Status in:
       'closed', 'escalated', 'plan-only', 'verify-fail', 'budget'.
    """
    run_dir = make_run_dir(ticket.get("id", "TXX"))
    print(f"[sprint] picked {ticket.get('id')} — {ticket.get('title')}")
    try:
        print(f"[sprint] run dir: {run_dir.relative_to(ROOT)}")
    except ValueError:
        print(f"[sprint] run dir: {run_dir}")

    client = _claude_client()

    # 1. Plan
    plan, plan_cost = generate_plan(client, ticket)
    plan_path = run_dir / "plan.md"
    plan_path.write_text(plan, encoding="utf-8")
    print(f"[sprint] plan written ({plan_cost:.4f} USD)")

    # Risk gate
    if is_risk_flagged(ticket):
        escalate(ticket, "risk-flagged component — manual review required", plan_path)
        return ("escalated", plan_cost)

    if not args.auto_implement or args.dry_run:
        # plan-only mode
        msg = "auto-implement disabled" if not args.auto_implement else "dry-run"
        print(f"[sprint] {msg} — stopping after plan")
        return ("plan-only", plan_cost)

    if not is_safe_component(ticket):
        escalate(ticket,
                 f"component '{ticket.get('component')}' not in SAFE_COMPONENTS — "
                 "auto-implement refused. Add to SAFE list or run manually.",
                 plan_path)
        return ("escalated", plan_cost)

    # Budget check before expensive call
    if cumulative_cost + plan_cost >= args.max_cost:
        print(f"[sprint] budget hit before implement — stopping")
        return ("budget", plan_cost)

    # 2. Auto-implement
    deadline = time.time() + PER_TICKET_TIMEOUT_S
    impl = auto_implement(client, ticket, plan, deadline)
    impl_cost = impl.get("cost", 0.0)
    (run_dir / "implement.json").write_text(
        json.dumps({k: v for k, v in impl.items() if k != "messages"}, indent=2),
        encoding="utf-8",
    )
    print(f"[sprint] implement status={impl['status']} files={len(impl.get('files', []))} cost={impl_cost:.4f}")

    if impl["status"] != "done":
        escalate(ticket, f"auto-implement status={impl['status']}", plan_path)
        return ("escalated", plan_cost + impl_cost)

    # 3. Verify
    v = run_verify()
    (run_dir / "verify.log").write_text(
        v["stdout"] + "\n--- stderr ---\n" + v["stderr"], encoding="utf-8",
    )
    if not v["success"]:
        # 1 retry: feed verify output back to coder for a focused fix
        print("[sprint] verify FAIL — attempting one retry")
        retry_plan = (
            f"{plan}\n\n## Retry context\n\n"
            f"verify.py FAILED with this output:\n```\n{v['stdout'][-2000:]}\n```\n"
            f"Fix the failures only. Do not refactor."
        )
        impl2 = auto_implement(client, ticket, retry_plan, deadline)
        impl_cost += impl2.get("cost", 0.0)
        v = run_verify()
        (run_dir / "verify.log").write_text(
            v["stdout"] + "\n--- stderr ---\n" + v["stderr"], encoding="utf-8",
        )
        if not v["success"]:
            escalate(ticket, "verify failed after 1 retry", plan_path)
            return ("verify-fail", plan_cost + impl_cost)

    # 4. Refresh PI.md
    subprocess.run([sys.executable, str(ROOT / "scripts" / "refresh_pi.py")],
                   cwd=str(ROOT), check=False, capture_output=True, text=True)

    # 5. Close + branch + commit
    summary = f"Auto-implemented per plan at {plan_path.relative_to(ROOT)}"
    sol_id = close_ticket(ticket, summary, impl["files"])
    branch = commit_branch(ticket, impl["files"], sol_id)
    notify_success(ticket, branch, sol_id)
    return ("closed", plan_cost + impl_cost)


def main() -> int:
    ap = argparse.ArgumentParser(description="Pi autonomous sprint runner")
    ap.add_argument("--max-tickets", type=int, default=1)
    ap.add_argument("--max-cost", type=float, default=0.50)
    ap.add_argument("--auto-implement", action="store_true",
                    help="Allow Claude to make code edits (only for safe components)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan only, no commits — overrides --auto-implement")
    ap.add_argument("--ticket", type=str, default=None,
                    help="Force a specific ticket id (e.g. T-042)")
    args = ap.parse_args()

    SPRINT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    cumulative_cost = 0.0
    completed = 0
    statuses: List[str] = []

    while completed < args.max_tickets and cumulative_cost < args.max_cost:
        ticket = pick_ticket(forced_id=args.ticket if completed == 0 else None)
        if ticket is None:
            print("[sprint] no eligible tickets")
            break
        try:
            status, cost = run_one_ticket(args, ticket, cumulative_cost)
        except SprintError as e:
            print(f"[sprint] error: {e}")
            status, cost = ("error", 0.0)

        cumulative_cost += cost
        completed += 1
        statuses.append(status)

        if args.ticket:
            break  # never iterate when a specific ticket was forced

    print(f"\n[sprint] done — processed {completed} ticket(s), "
          f"cost ${cumulative_cost:.4f}, statuses: {statuses}")

    # T-169: one-liner run log so "did sprint run" is always answerable.
    run_log = SPRINT_LOG_DIR / "runs.jsonl"
    run_entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tickets_processed": completed,
        "cost_usd": round(cumulative_cost, 4),
        "statuses": statuses,
        "dry_run": args.dry_run,
        "auto_implement": args.auto_implement,
    }
    with run_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(run_entry) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
