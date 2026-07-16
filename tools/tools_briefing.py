"""
tools/tools_briefing.py — Daily briefing generator for Pi.

Aggregates awareness (weather, news, markets, research) + active L3 context
into a personalized morning report.  Optionally saves to Obsidian daily note.

Usage (from within Pi session):
    briefing = BriefingTools(awareness, memory, obsidian)
    text = briefing.generate()
"""

from datetime import datetime, timezone
from typing import Optional


class BriefingTools:

    def __init__(self, awareness, memory, obsidian=None, calendar=None):
        self.awareness = awareness
        self.memory    = memory
        self.obsidian  = obsidian   # Optional[ObsidianTools]
        self.calendar  = calendar   # Optional[CalendarTools]

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate(
        self,
        save_to_obsidian: bool = True,
        gmail=None,              # Optional[GmailTools] — injected by pi_agent
    ) -> str:
        """Generate and return the full daily briefing as a markdown string."""
        now   = datetime.now(timezone.utc)
        # T-159: %-d is Linux-only and raises ValueError on Windows. Build the
        # no-leading-zero day portably instead.
        today = f"{now.strftime('%A, %B')} {now.day}, {now.year}"  # "Monday, May 4, 2026"

        sections: list[str] = [f"# Daily Briefing — {today}\n"]

        # --- Time & Location ---
        loc = self.awareness.get_location()
        if loc.get("city"):
            city = loc["city"]
            country = loc.get("country", "")
            sections.append(f"**Location:** {city}, {country}")

        # --- Calendar: Today's Events ---
        if self.calendar and self.calendar.is_configured():
            try:
                cal = self.calendar.calendar_today()
                if cal.get("success") and cal.get("summary"):
                    sections.append(f"\n## Calendar\n{cal['summary']}")
            except Exception:
                pass

        # --- Weather ---
        wx = self.awareness.get_weather(force=True)
        if wx.get("success") and wx.get("summary"):
            sections.append(f"\n## Weather\n{wx['summary']}")

        # --- Gmail Inbox Summary ---
        if gmail:
            try:
                inbox = gmail.inbox_summary(max_results=5)
                if inbox.get("success") and inbox.get("summary"):
                    sections.append(f"\n## Inbox\n{inbox['summary']}")
            except Exception:
                pass

        # --- Top News ---
        news = self.awareness.get_news(category="global", count=5, force=True)
        if news.get("success") and news.get("items"):
            items_md = "\n".join(
                f"- [{it['title']}]({it['url']})" if it.get("url") else f"- {it['title']}"
                for it in news["items"][:5]
            )
            sections.append(f"\n## World\n{items_md}")

        # --- Tech / AI ---
        tech = self.awareness.get_news(category="tech", count=4, force=True)
        if tech.get("success") and tech.get("items"):
            items_md = "\n".join(
                f"- {it['title']}" for it in tech["items"][:4]
            )
            sections.append(f"\n## Tech\n{items_md}")

        # --- Markets ---
        stocks = self.awareness.get_stocks(force=True)
        if stocks.get("success") and stocks.get("prices"):
            lines = []
            for sym, data in list(stocks["prices"].items())[:6]:
                price  = data.get("price", "?")
                change = data.get("change_pct", 0)
                arrow  = "▲" if change >= 0 else "▼"
                lines.append(f"- **{sym}** ${price:.2f} {arrow}{abs(change):.2f}%")
            sections.append(f"\n## Markets\n" + "\n".join(lines))

        # --- Business / Markets news (CNBC-led, T-160) ---
        biz = self.awareness.get_news(category="business", count=4, force=True)
        if biz.get("success") and biz.get("items"):
            items_md = "\n".join(
                f"- [{it['title']}]({it['url']})" if it.get("url") else f"- {it['title']}"
                for it in biz["items"][:4]
            )
            sections.append(f"\n## Business\n{items_md}")

        # --- Research (HN + ArXiv) ---
        tech_upd = self.awareness.get_tech_updates(count=4, force=True)
        if tech_upd.get("success"):
            hn = tech_upd.get("hn_stories", [])[:3]
            papers = tech_upd.get("arxiv_papers", [])[:2]
            if hn:
                hn_md = "\n".join(f"- {s['title']}" for s in hn)
                sections.append(f"\n## Hacker News\n{hn_md}")
            if papers:
                p_md = "\n".join(f"- {p['title']}" for p in papers)
                sections.append(f"\n## Research\n{p_md}")

        # --- Active L3 Context (what's happening in Ash's world) ---
        l3 = self.memory.get_l3_context(max_tokens=300)
        if l3:
            # Strip the header and inject a condensed version
            body = l3.replace("=== ACTIVE CONTEXT ===", "").strip()
            if body:
                sections.append(f"\n## Active Context\n{body}")

        briefing = "\n".join(sections)

        # Optionally save to Obsidian
        if save_to_obsidian and self.obsidian:
            note_path = f"Daily Notes/{now.strftime('%Y-%m-%d')}.md"
            try:
                self.obsidian.obsidian_write(path=note_path, content=briefing)
            except Exception:
                pass

        return briefing


# ── T-083 R2.1: tool registry export ─────────────────────────────────────────

from agent.tool_spec import ToolSpec  # noqa: E402


def _handle_daily_briefing(agent, tool_input, *, memory_override=None):
    mem = memory_override or agent.memory
    from tools.tools_obsidian import ObsidianTools
    from tools.tools_calendar import CalendarTools
    briefing = BriefingTools(
        awareness=agent.awareness,
        memory=mem,
        obsidian=ObsidianTools(),
        calendar=CalendarTools(),
    )
    text = briefing.generate(save_to_obsidian=tool_input.get("save_to_obsidian", True))
    return {"success": True, "briefing": text}


TOOLS = [
    ToolSpec(
        name="daily_briefing",
        description=(
            "Generate Ash's daily briefing: weather, top news, markets, HN, research, "
            "and active L3 context. Saves to Obsidian daily note automatically. Use on "
            "'briefing' / 'morning' / 'what's today'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "save_to_obsidian": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to save to Obsidian daily note",
                },
            },
            "required": [],
        },
        handler=_handle_daily_briefing,
    ),
]
