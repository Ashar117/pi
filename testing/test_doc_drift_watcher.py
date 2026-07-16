"""
testing/test_doc_drift_watcher.py — Tests for SKILL 4: doc_drift_watcher.

Coverage:

Happy path (in-sync -> PASS):
  - test_all_counts_match_passes
  - test_verify_status_matches_passes
  - test_no_archived_refs_passes

Open ticket drift:
  - test_open_tickets_mismatch_warns
  - test_missing_pi_md_warns
  - test_missing_auto_section_warns

Closed ticket drift:
  - test_closed_tickets_mismatch_warns
  - test_closed_tickets_match_passes

Solution count drift:
  - test_solution_count_mismatch_warns
  - test_solution_count_match_passes
  - test_missing_solutions_jsonl_reports_zero

Verify status drift:
  - test_verify_status_mismatch_warns
  - test_verify_status_match_passes
  - test_missing_status_md_warns

Archived refs:
  - test_archived_ref_in_readme_warns
  - test_archived_ref_in_pi_md_warns
  - test_no_archived_refs_passes

Strict mode:
  - test_strict_escalates_warn_to_fail

Integration:
  - test_run_check_all_clean_passes
  - test_run_check_writes_report
  - test_run_check_drift_detected_warns
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.passive import doc_drift_watcher as ddw
from scripts.passive.common import Status


# ── Helpers ───────────────────────────────────────────────────────────────────

AUTO_TMPL = """\
<!-- BEGIN AUTO §4 -->
- **Phase:** 7 — Autonomy (week 1 complete)
- **Last verify:** {verify} · 97/97 files clean · 32 tests · 0 failures
- **Open tickets:** {open_t}
- **Closed tickets:** {closed_t}
- **Solutions logged:** {solutions}
- **Turns today:** 9
- **Last session end:** 2026-05-09
<!-- END AUTO §4 -->"""


def _make_pi_md(tmp_path: Path, open_t: int = 0, closed_t: int = 5,
                solutions: int = 4, verify: str = "PASS") -> Path:
    p = tmp_path / "PI.md"
    p.write_text(
        "# PI.md\n\n## §4 State (auto-generated)\n\n"
        + AUTO_TMPL.format(open_t=open_t, closed_t=closed_t,
                           solutions=solutions, verify=verify),
        encoding="utf-8",
    )
    return p


def _make_status_md(tmp_path: Path, overall: str = "PASS") -> Path:
    d = tmp_path / "docs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "STATUS.md"
    p.write_text(f"# Status\n\n**Overall:** {overall}\n", encoding="utf-8")
    return p


def _make_tickets(tmp_path: Path, open_n: int = 0, closed_n: int = 5) -> None:
    for subdir, n in [("open", open_n), ("closed", closed_n)]:
        d = tmp_path / "tickets" / subdir
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (d / f"T-{i:03d}.json").write_text('{"id":"T-%03d"}' % i)


def _make_solutions(tmp_path: Path, count: int = 4) -> Path:
    d = tmp_path / "solutions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SOLUTIONS.jsonl"
    lines = [f'{{"id": {i}}}' for i in range(count)]
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return p


def _setup_clean(tmp_path: Path, open_t: int = 0, closed_t: int = 5,
                 solutions: int = 4, verify: str = "PASS") -> None:
    _make_pi_md(tmp_path, open_t=open_t, closed_t=closed_t,
                solutions=solutions, verify=verify)
    _make_status_md(tmp_path, overall=verify)
    _make_tickets(tmp_path, open_n=open_t, closed_n=closed_t)
    _make_solutions(tmp_path, count=solutions)
    # T-285: vault briefs must be current too, or "all clean" isn't all clean.
    if closed_t:
        briefs = tmp_path / "vault" / "notes" / "per-ticket"
        briefs.mkdir(parents=True, exist_ok=True)
        for i in range(closed_t):
            (briefs / f"T-{i:03d}-slug.md").write_text("brief", encoding="utf-8")


# ── check_open_tickets ────────────────────────────────────────────────────────

class TestCheckOpenTickets:
    def test_counts_match_passes(self, tmp_path):
        _make_tickets(tmp_path, open_n=2)
        _make_pi_md(tmp_path, open_t=2)
        status, _ = ddw.check_open_tickets(tmp_path)
        assert status == Status.PASS

    def test_mismatch_warns(self, tmp_path):
        _make_tickets(tmp_path, open_n=3)
        _make_pi_md(tmp_path, open_t=1)  # claims 1, actual 3
        status, lines = ddw.check_open_tickets(tmp_path)
        assert status == Status.WARN
        assert any("1" in l and "3" in l for l in lines)

    def test_missing_pi_md_warns(self, tmp_path):
        _make_tickets(tmp_path, open_n=0)
        # No PI.md
        status, lines = ddw.check_open_tickets(tmp_path)
        assert status == Status.WARN
        assert any("PI.md" in l for l in lines)

    def test_missing_auto_section_warns(self, tmp_path):
        _make_tickets(tmp_path, open_n=0)
        (tmp_path / "PI.md").write_text("# PI.md\nNo auto section here.\n",
                                         encoding="utf-8")
        status, lines = ddw.check_open_tickets(tmp_path)
        assert status == Status.WARN
        assert any("auto" in l.lower() or "refresh" in l.lower() for l in lines)

    def test_zero_open_tickets_matches(self, tmp_path):
        _make_tickets(tmp_path, open_n=0)
        _make_pi_md(tmp_path, open_t=0)
        status, _ = ddw.check_open_tickets(tmp_path)
        assert status == Status.PASS

    def test_missing_tickets_dir_counts_zero(self, tmp_path):
        _make_pi_md(tmp_path, open_t=0)
        # No tickets dir → actual=0, claimed=0
        status, _ = ddw.check_open_tickets(tmp_path)
        assert status == Status.PASS


# ── check_closed_tickets ──────────────────────────────────────────────────────

class TestCheckClosedTickets:
    def test_match_passes(self, tmp_path):
        _make_tickets(tmp_path, closed_n=10)
        _make_pi_md(tmp_path, closed_t=10)
        status, _ = ddw.check_closed_tickets(tmp_path)
        assert status == Status.PASS

    def test_mismatch_warns(self, tmp_path):
        _make_tickets(tmp_path, closed_n=53)
        _make_pi_md(tmp_path, closed_t=43)  # stale
        status, lines = ddw.check_closed_tickets(tmp_path)
        assert status == Status.WARN
        assert any("43" in l and "53" in l for l in lines)

    def test_missing_pi_md_warns(self, tmp_path):
        _make_tickets(tmp_path, closed_n=5)
        status, lines = ddw.check_closed_tickets(tmp_path)
        assert status == Status.WARN


# ── check_solution_count ──────────────────────────────────────────────────────

class TestCheckSolutionCount:
    def test_match_passes(self, tmp_path):
        _make_solutions(tmp_path, count=7)
        _make_pi_md(tmp_path, solutions=7)
        status, _ = ddw.check_solution_count(tmp_path)
        assert status == Status.PASS

    def test_mismatch_warns(self, tmp_path):
        _make_solutions(tmp_path, count=39)
        _make_pi_md(tmp_path, solutions=43)  # stale
        status, lines = ddw.check_solution_count(tmp_path)
        assert status == Status.WARN
        assert any("43" in l and "39" in l for l in lines)

    def test_missing_jsonl_counts_zero(self, tmp_path):
        _make_pi_md(tmp_path, solutions=0)
        # No SOLUTIONS.jsonl
        status, _ = ddw.check_solution_count(tmp_path)
        assert status == Status.PASS

    def test_blank_lines_not_counted(self, tmp_path):
        d = tmp_path / "solutions"
        d.mkdir(parents=True)
        (d / "SOLUTIONS.jsonl").write_text(
            '{"id":1}\n\n{"id":2}\n\n', encoding="utf-8"
        )
        _make_pi_md(tmp_path, solutions=2)
        status, _ = ddw.check_solution_count(tmp_path)
        assert status == Status.PASS


# ── check_verify_status ───────────────────────────────────────────────────────

class TestCheckVerifyStatus:
    def test_both_pass_passes(self, tmp_path):
        _make_status_md(tmp_path, overall="PASS")
        _make_pi_md(tmp_path, verify="PASS")
        status, _ = ddw.check_verify_status(tmp_path)
        assert status == Status.PASS

    def test_both_fail_passes(self, tmp_path):
        _make_status_md(tmp_path, overall="FAIL")
        _make_pi_md(tmp_path, verify="FAIL")
        status, _ = ddw.check_verify_status(tmp_path)
        assert status == Status.PASS

    def test_mismatch_warns(self, tmp_path):
        _make_status_md(tmp_path, overall="PASS")
        _make_pi_md(tmp_path, verify="FAIL")  # stale
        status, lines = ddw.check_verify_status(tmp_path)
        assert status == Status.WARN
        assert any("FAIL" in l and "PASS" in l for l in lines)

    def test_missing_status_md_warns(self, tmp_path):
        _make_pi_md(tmp_path, verify="PASS")
        # No docs/STATUS.md
        status, lines = ddw.check_verify_status(tmp_path)
        assert status == Status.WARN
        assert any("STATUS.md" in l for l in lines)

    def test_missing_pi_md_warns(self, tmp_path):
        _make_status_md(tmp_path, overall="PASS")
        status, lines = ddw.check_verify_status(tmp_path)
        assert status == Status.WARN


# ── check_archived_refs ───────────────────────────────────────────────────────

class TestCheckArchivedRefs:
    def test_no_refs_passes(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "# Pi\nNo archived links here.\n", encoding="utf-8"
        )
        status, _ = ddw.check_archived_refs(tmp_path)
        assert status == Status.PASS

    def test_archived_ref_in_readme_warns(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "See [old doc](docs/_archive/PI_MASTER_PROMPT.md) for history.\n",
            encoding="utf-8",
        )
        status, lines = ddw.check_archived_refs(tmp_path)
        assert status == Status.WARN
        assert any("_archive" in l for l in lines)

    def test_archived_ref_in_pi_md_warns(self, tmp_path):
        (tmp_path / "PI.md").write_text(
            "Refer to docs/_archive/old_spec.md.\n", encoding="utf-8"
        )
        status, lines = ddw.check_archived_refs(tmp_path)
        assert status == Status.WARN

    def test_missing_docs_skipped_gracefully(self, tmp_path):
        # No public docs at all
        status, _ = ddw.check_archived_refs(tmp_path)
        assert status == Status.PASS

    def test_multiple_archived_refs_all_listed(self, tmp_path):
        (tmp_path / "README.md").write_text(
            "See docs/_archive/foo.md and docs/_archive/bar.md.\n",
            encoding="utf-8",
        )
        status, lines = ddw.check_archived_refs(tmp_path)
        assert status == Status.WARN
        assert sum(1 for l in lines if "_archive" in l) >= 2


# ── strict mode ───────────────────────────────────────────────────────────────

class TestStrictMode:
    def test_strict_escalates_warn_to_fail(self, tmp_path):
        _make_tickets(tmp_path, open_n=5, closed_n=0)
        _make_pi_md(tmp_path, open_t=0)  # claimed 0, actual 5 → WARN
        _make_status_md(tmp_path, overall="PASS")
        _make_solutions(tmp_path, count=0)

        from unittest.mock import patch
        with patch("scripts.passive.doc_drift_watcher.write_report"):
            normal = ddw.run_check(strict=False, root=tmp_path)
            strict = ddw.run_check(strict=True, root=tmp_path)

        assert normal == Status.WARN
        assert strict == Status.FAIL


# ── integration: run_check ────────────────────────────────────────────────────

class TestRunCheck:
    def test_all_clean_passes(self, tmp_path):
        _setup_clean(tmp_path, open_t=0, closed_t=5, solutions=4, verify="PASS")

        from unittest.mock import patch
        with patch("scripts.passive.doc_drift_watcher.write_report"):
            status = ddw.run_check(root=tmp_path)

        assert status == Status.PASS

    def test_writes_report(self, tmp_path):
        reports = tmp_path / "reports"
        _setup_clean(tmp_path)

        from unittest.mock import patch
        with patch("scripts.passive.common.REPORTS", reports):
            ddw.run_check(root=tmp_path, reports=reports)

        assert (reports / "doc_drift_watcher.md").exists()

    def test_drift_detected_warns(self, tmp_path):
        _setup_clean(tmp_path, open_t=0, closed_t=5, solutions=4)
        # Introduce drift: claimed 99 solutions but actual is 4
        _make_pi_md(tmp_path, solutions=99, open_t=0, closed_t=5)

        from unittest.mock import patch
        with patch("scripts.passive.doc_drift_watcher.write_report"):
            status = ddw.run_check(root=tmp_path)

        assert status == Status.WARN

    def test_missing_pi_md_warns(self, tmp_path):
        _make_status_md(tmp_path, overall="PASS")
        _make_tickets(tmp_path, open_n=0, closed_n=0)
        _make_solutions(tmp_path, count=0)

        from unittest.mock import patch
        with patch("scripts.passive.doc_drift_watcher.write_report"):
            status = ddw.run_check(root=tmp_path)

        assert status == Status.WARN


# ── T-153: check_capability_drift ──────────────────────────────────────────────

def _make_about(tmp_path: Path, rows: list) -> Path:
    """rows: list of (capability, status, notes)."""
    lines = ["# About Pi", "", "## Capabilities — current state", "",
             "| Capability | Status | Notes |", "| --- | --- | --- |"]
    for cap, status, notes in rows:
        lines.append(f"| {cap} | {status} | {notes} |")
    p = tmp_path / "ABOUT.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _make_open_ticket(tmp_path: Path, tid: str, sev: str, title: str, component: str = "") -> None:
    import json
    d = tmp_path / "tickets" / "open"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps(
        {"id": tid, "severity": sev, "title": title, "component": component}), encoding="utf-8")


class TestCheckCapabilityDrift:
    def test_working_with_matching_open_p2_warns(self, tmp_path):
        _make_about(tmp_path, [("Conversation coherence", "✅ Working", "n/a")])
        _make_open_ticket(tmp_path, "T-999", "P2", "conversation coherence is broken")
        status, lines = ddw.check_capability_drift(tmp_path)
        assert status == Status.WARN
        assert any("T-999" in l for l in lines)

    def test_working_without_matching_ticket_passes(self, tmp_path):
        _make_about(tmp_path, [("Telegram integration", "✅ Working", "n/a")])
        _make_open_ticket(tmp_path, "T-999", "P2", "memory dedup is flaky")
        status, _ = ddw.check_capability_drift(tmp_path)
        assert status == Status.PASS

    def test_partial_row_not_flagged(self, tmp_path):
        _make_about(tmp_path, [("Session isolation", "◐ Partial", "n/a")])
        _make_open_ticket(tmp_path, "T-999", "P2", "session isolation incomplete")
        status, _ = ddw.check_capability_drift(tmp_path)
        assert status == Status.PASS

    def test_p3_ticket_does_not_trigger(self, tmp_path):
        _make_about(tmp_path, [("Conversation coherence", "✅ Working", "n/a")])
        _make_open_ticket(tmp_path, "T-999", "P3", "conversation coherence minor nit")
        status, _ = ddw.check_capability_drift(tmp_path)
        assert status == Status.PASS

    def test_no_about_md_passes(self, tmp_path):
        status, _ = ddw.check_capability_drift(tmp_path)
        assert status == Status.PASS


class TestCheckVaultBriefFreshness:
    """T-285: warn when vault/notes/per-ticket/ lags tickets/closed/."""

    def test_briefs_current_passes(self, tmp_path):
        _make_tickets(tmp_path, open_n=0, closed_n=5)  # T-000..T-004
        briefs = tmp_path / "vault" / "notes" / "per-ticket"
        briefs.mkdir(parents=True)
        for i in range(5):
            (briefs / f"T-{i:03d}-slug.md").write_text("brief", encoding="utf-8")
        status, lines = ddw.check_vault_brief_freshness(tmp_path)
        assert status == Status.PASS
        assert "T-4" in lines[0]

    def test_briefs_lagging_warns_with_gap(self, tmp_path):
        _make_tickets(tmp_path, open_n=0, closed_n=5)  # T-000..T-004
        briefs = tmp_path / "vault" / "notes" / "per-ticket"
        briefs.mkdir(parents=True)
        (briefs / "T-002-slug.md").write_text("brief", encoding="utf-8")
        status, lines = ddw.check_vault_brief_freshness(tmp_path)
        assert status == Status.WARN
        assert "T-2" in lines[0] and "T-4" in lines[0]

    def test_missing_briefs_dir_warns(self, tmp_path):
        _make_tickets(tmp_path, open_n=0, closed_n=3)
        status, lines = ddw.check_vault_brief_freshness(tmp_path)
        assert status == Status.WARN
        assert "T-2" in lines[0]

    def test_no_closed_tickets_passes(self, tmp_path):
        (tmp_path / "tickets" / "closed").mkdir(parents=True)
        status, _ = ddw.check_vault_brief_freshness(tmp_path)
        assert status == Status.PASS
