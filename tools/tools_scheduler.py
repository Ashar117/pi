"""
tools/tools_scheduler.py — Scheduled jobs for Pi (F-008).

Uses the `schedule` library to run recurring jobs in a background thread.
Currently supports:
  - Daily briefing at a configurable time (default 08:00 local time)
  - Custom one-off and recurring jobs

The scheduler thread is started by pi_agent.py at startup.

Usage:
    from tools.tools_scheduler import PiScheduler
    sched = PiScheduler(agent)
    sched.start()           # starts background thread, non-blocking
    sched.stop()            # clean shutdown
    sched.list_jobs()       # {"jobs": [...]}
"""

import threading
import logging
import time
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class PiScheduler:
    """Background scheduler for Pi periodic tasks."""

    def __init__(self, agent, briefing_time: str = "08:00", tts=None, telegram=None):
        """
        Args:
            agent:         PiAgent instance.
            briefing_time: Daily briefing time in "HH:MM" (24h local time). Default 08:00.
            tts:           Optional TTSTools instance — reads briefing aloud if provided.
            telegram:      Optional TelegramTools instance — sends briefing via Telegram.
        """
        self._agent = agent
        self._briefing_time = briefing_time
        self._tts = tts
        self._telegram = telegram
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._jobs: List[Dict] = []

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduler in a daemon background thread."""
        if self._running:
            return

        import schedule

        # Daily briefing
        schedule.every().day.at(self._briefing_time).do(self._daily_briefing_job)
        self._jobs.append({
            "id": "daily_briefing",
            "description": f"Daily briefing at {self._briefing_time}",
            "type": "daily",
        })

        # T-085 R4: combined L3+L2 prune — once per day at 03:00. Replaces the
        # in-exit prune calls so prunes happen on a predictable schedule
        # independent of session boundaries. scripts/passive/memory_prune.py
        # is the manual / external invocation; this job is the in-daemon one.
        schedule.every().day.at("03:00").do(self._memory_prune_job)
        self._jobs.append({
            "id": "memory_prune",
            "description": "L3 expired + L2 stale pruning at 03:00 (T-085 R4)",
            "type": "daily",
        })

        # T-085 R4: weekly memory audit — Sunday 02:00. Replaces the in-exit
        # audit call. scripts/passive/weekly_memory_audit.py is the standalone
        # invocation; this job runs the same code in-daemon.
        schedule.every().sunday.at("02:00").do(self._weekly_audit_job)
        self._jobs.append({
            "id": "weekly_audit",
            "description": "Weekly memory audit Sunday 02:00 (T-085 R4)",
            "type": "weekly",
        })

        # T-083 R2.3: weekly tool usage audit — Friday 02:00.
        # Files P3 prune tickets for unused tools, P2 fix tickets for high-failure tools.
        schedule.every().friday.at("02:00").do(self._tool_usage_audit_job)
        self._jobs.append({
            "id": "tool_usage_audit",
            "description": "Tool usage audit Friday 02:00 (T-083 R2.3)",
            "type": "weekly",
        })

        # T-087 R6: daily replication log rotation — 04:00.
        schedule.every().day.at("04:00").do(self._replication_log_rotate_job)
        self._jobs.append({
            "id": "replication_log_rotate",
            "description": "Replication log rotation 04:00 (T-087 R6)",
            "type": "daily",
        })

        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="pi-scheduler",
        )
        self._thread.start()
        logger.info("PiScheduler started (briefing at %s)", self._briefing_time)

    def stop(self) -> None:
        """Signal the loop to stop."""
        self._running = False

    def add_daily(self, time_str: str, job_fn, job_id: str, description: str = "") -> Dict:
        """Register a new daily job.

        Args:
            time_str:    "HH:MM" in 24h local time.
            job_fn:      Callable with no arguments.
            job_id:      Unique identifier for this job.
            description: Human-readable label.

        Returns:
            {"success": bool, "id": str}
        """
        try:
            import schedule
            schedule.every().day.at(time_str).do(job_fn)
            entry = {"id": job_id, "description": description or job_id, "type": "daily", "time": time_str}
            self._jobs.append(entry)
            return {"success": True, "id": job_id}
        except Exception as e:
            return {"success": False, "id": job_id, "error": str(e)}

    def add_interval(self, minutes: int, job_fn, job_id: str, description: str = "") -> Dict:
        """Register a recurring interval job.

        Args:
            minutes:     How often to run (minutes).
            job_fn:      Callable with no arguments.
            job_id:      Unique identifier.
            description: Human-readable label.
        """
        try:
            import schedule
            schedule.every(minutes).minutes.do(job_fn)
            entry = {"id": job_id, "description": description or job_id, "type": "interval",
                     "interval_minutes": minutes}
            self._jobs.append(entry)
            return {"success": True, "id": job_id}
        except Exception as e:
            return {"success": False, "id": job_id, "error": str(e)}

    def list_jobs(self) -> Dict:
        """Return registered jobs."""
        return {"jobs": list(self._jobs), "count": len(self._jobs), "running": self._running}

    def run_briefing_now(self) -> Dict:
        """Trigger the daily briefing immediately (for testing)."""
        return self._daily_briefing_job()

    # ── internal ──────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        import schedule
        while self._running:
            schedule.run_pending()
            time.sleep(30)

    def _daily_briefing_job(self) -> Dict:
        """Generate and deliver the daily briefing."""
        logger.info("PiScheduler: running daily briefing")
        try:
            from tools.tools_briefing import BriefingTools
            from tools.tools_obsidian import ObsidianTools
            from tools.tools_calendar import CalendarTools

            obsidian = ObsidianTools()
            calendar = CalendarTools()

            briefing = BriefingTools(
                awareness=self._agent.awareness,
                memory=self._agent.memory,
                obsidian=obsidian,
                calendar=calendar,
            )
            text = briefing.generate(save_to_obsidian=True)

            # Speak it aloud if TTS is wired
            if self._tts is not None:
                summary = _summarise_for_speech(text)
                self._tts.speak_async(summary)

            # Send via Telegram if available
            if self._telegram is not None and self._telegram.is_available():
                # Trim to Telegram's 4096-char limit
                preview = text[:4000] + ("..." if len(text) > 4000 else "")
                self._telegram.send(f"*Daily Briefing*\n\n{preview}")

            logger.info("PiScheduler: briefing complete (%d chars)", len(text))
            return {"success": True, "chars": len(text)}

        except Exception as e:
            logger.exception("PiScheduler: briefing failed")
            return {"success": False, "error": str(e)}

    def _memory_prune_job(self) -> Dict:
        """T-085 R4: prune L3 expired + L2 stale rows. Replaces _l3_prune_job."""
        logger.info("PiScheduler: pruning L3 expired + L2 stale")
        out: Dict = {"l3": None, "l2": None, "errors": []}
        try:
            out["l3"] = self._agent.memory.prune_l3_expired()
        except Exception as e:
            out["errors"].append(f"l3: {e}")
            logger.exception("PiScheduler: L3 prune failed")
        try:
            out["l2"] = self._agent.memory.prune_l2_stale()
        except Exception as e:
            out["errors"].append(f"l2: {e}")
            logger.exception("PiScheduler: L2 prune failed")
        logger.info("PiScheduler: memory prune done: %s", out)
        return out

    def _weekly_audit_job(self) -> Dict:
        """T-085 R4: run the weekly memory audit + write digest + Telegram notify."""
        logger.info("PiScheduler: running weekly memory audit")
        try:
            from memory.audit import run_audit, should_run_weekly
            from tools.tools_obsidian import render_audit_digest, _default_vault_root
            if not should_run_weekly():
                logger.info("PiScheduler: weekly audit skipped (should_run_weekly=False)")
                return {"skipped": True, "reason": "should_run_weekly=False"}
            audit_run = run_audit(self._agent.memory)
            res = render_audit_digest(audit_run, _default_vault_root())
            total = audit_run.total_findings
            logger.info("PiScheduler: weekly audit done: %d findings", total)
            if total and self._telegram is not None and self._telegram.is_available():
                msg = (
                    f"Pi memory audit ({audit_run.week_iso}):\n"
                    f" {len(audit_run.flagged)} flagged\n"
                    f" {len(audit_run.archived)} archived\n"
                    f" {len(audit_run.deleted)} deleted\n"
                    f" {len(audit_run.merge_suggestions)} merge\n"
                    f"Review: vault/notes/memory/audit/{audit_run.week_iso}.md"
                )
                try:
                    self._telegram.send(msg)
                except Exception as e:
                    logger.warning("PiScheduler: audit Telegram notify failed: %s", e)
            return {"findings": total, "path": res.get("path"),
                    "errors": len(audit_run.errors)}
        except Exception as e:
            logger.exception("PiScheduler: weekly audit failed")
            return {"success": False, "error": str(e)}


    def _replication_log_rotate_job(self) -> Dict:
        """T-087 R6: daily rotation of data/memory_replication.log."""
        logger.info("PiScheduler: rotating replication log")
        try:
            from scripts.passive.replication_log_rotate import run_rotate
            status, _ = run_rotate(quiet=True)
            return {"success": True, "status": status.value}
        except Exception as e:
            logger.exception("PiScheduler: replication log rotate failed")
            return {"success": False, "error": str(e)}

    def _tool_usage_audit_job(self) -> Dict:
        """T-083 R2.3: weekly tool usage audit — files prune/fix tickets automatically."""
        logger.info("PiScheduler: running tool usage audit")
        try:
            from scripts.passive.tool_usage_audit import run_audit
            status, content = run_audit(quiet=True)
            logger.info("PiScheduler: tool audit done: %s", status.value)
            if self._telegram is not None and self._telegram.is_available():
                msg = f"*Tool usage audit (Friday):*\nStatus: {status.value}\n{content[:800]}"
                try:
                    self._telegram.send(msg)
                except Exception:
                    pass
            return {"success": True, "status": status.value}
        except Exception as e:
            logger.exception("PiScheduler: tool audit failed")
            return {"success": False, "error": str(e)}


def _summarise_for_speech(briefing_text: str, max_chars: int = 800) -> str:
    """Extract the first meaningful sentences from the briefing for TTS."""
    lines = [l.strip() for l in briefing_text.splitlines() if l.strip()]
    # Skip markdown headers and bullet prefixes, grab plain-text sentences
    spoken = []
    total = 0
    for line in lines:
        if line.startswith("#"):
            continue
        clean = line.lstrip("- *>").strip()
        if not clean:
            continue
        if total + len(clean) > max_chars:
            break
        spoken.append(clean)
        total += len(clean) + 1

    return ". ".join(spoken[:10]) if spoken else "Your daily briefing is ready."
