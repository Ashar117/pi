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

        # L3 prune — once per day at 03:00
        schedule.every().day.at("03:00").do(self._l3_prune_job)
        self._jobs.append({
            "id": "l3_prune",
            "description": "L3 expired entry pruning at 03:00",
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

    def _l3_prune_job(self) -> Dict:
        """Prune expired L3 entries."""
        logger.info("PiScheduler: pruning L3 expired entries")
        try:
            result = self._agent.memory.prune_l3_expired()
            logger.info("PiScheduler: L3 prune done: %s", result)
            return result
        except Exception as e:
            logger.exception("PiScheduler: L3 prune failed")
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
