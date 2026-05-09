"""
tools/tools_calendar.py — Google Calendar integration for Pi.

Reuses the same OAuth credentials as Gmail (data/gmail_credentials.json).
Token stored at data/calendar_token.json — gitignored.

Operations:
  calendar_today()                       — events today
  calendar_upcoming(days=7)              — events in next N days
  calendar_create(title, start, end, ..) — create an event
  calendar_search(query, days=30)        — search future events
"""

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

_ROOT       = Path(__file__).parent.parent
_CREDS_FILE = _ROOT / "data" / "gmail_credentials.json"   # shared with Gmail
_TOKEN_FILE = _ROOT / "data" / "calendar_token.json"

_SCOPES = ["https://www.googleapis.com/auth/calendar"]

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _GOOGLE_OK = True
except ImportError:
    _GOOGLE_OK = False


class CalendarTools:

    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service
        if not _GOOGLE_OK:
            raise RuntimeError("Google API libs not installed — pip install google-auth-oauthlib google-api-python-client")
        if not _CREDS_FILE.exists():
            raise RuntimeError(
                f"Google credentials not found at {_CREDS_FILE}.\n"
                "Download from console.cloud.google.com → APIs & Services → Credentials → OAuth 2.0 Client IDs"
            )
        creds = None
        if _TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS_FILE), _SCOPES)
                creds = flow.run_local_server(port=0)
            _TOKEN_FILE.parent.mkdir(exist_ok=True)
            _TOKEN_FILE.write_text(creds.to_json())
        self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def is_configured(self) -> bool:
        return _CREDS_FILE.exists()

    # ── Calendar operations ────────────────────────────────────────────────────

    def calendar_today(self) -> Dict:
        """Get today's events."""
        now   = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end   = start + timedelta(days=1)
        return self._get_events(start, end, label="Today")

    def calendar_upcoming(self, days: int = 7) -> Dict:
        """Get events in the next N days."""
        now   = datetime.now(timezone.utc)
        start = now
        end   = now + timedelta(days=days)
        return self._get_events(start, end, label=f"Next {days} days")

    def calendar_search(self, query: str, days: int = 30) -> Dict:
        """Search calendar events by keyword."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        try:
            svc    = self._get_service()
            result = svc.events().list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                q=query,
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            ).execute()
            events = result.get("items", [])
            items  = [self._format_event(e) for e in events]
            return {"success": True, "query": query, "count": len(items), "events": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def calendar_create(
        self,
        title:       str,
        start:       str,
        end:         str,
        description: str = "",
        location:    str = "",
        calendar_id: str = "primary",
    ) -> Dict:
        """
        Create a calendar event.

        Args:
            title:       Event title
            start:       ISO datetime string e.g. "2026-05-05T14:00:00"
            end:         ISO datetime string
            description: Optional description
            location:    Optional location string
        """
        try:
            svc   = self._get_service()
            event = {
                "summary":     title,
                "description": description,
                "location":    location,
                "start":       {"dateTime": start, "timeZone": "UTC"},
                "end":         {"dateTime": end,   "timeZone": "UTC"},
            }
            created = svc.events().insert(calendarId=calendar_id, body=event).execute()
            return {
                "success":    True,
                "event_id":   created.get("id"),
                "title":      title,
                "start":      start,
                "end":        end,
                "html_link":  created.get("htmlLink", ""),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def calendar_delete(self, event_id: str) -> Dict:
        """Delete a calendar event by ID."""
        try:
            svc = self._get_service()
            svc.events().delete(calendarId="primary", eventId=event_id).execute()
            return {"success": True, "deleted": event_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_events(self, start: datetime, end: datetime, label: str = "") -> Dict:
        try:
            svc    = self._get_service()
            result = svc.events().list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
            events = result.get("items", [])
            items  = [self._format_event(e) for e in events]

            if not items:
                summary = f"No events {label.lower() or 'in range'}."
            else:
                lines = [f"**{label}** — {len(items)} event(s)"]
                for ev in items:
                    time_str = ev.get("time_str", "")
                    lines.append(f"- {time_str} **{ev['title']}**" + (f" @ {ev['location']}" if ev.get('location') else ""))
                summary = "\n".join(lines)

            return {"success": True, "label": label, "count": len(items), "events": items, "summary": summary}
        except Exception as e:
            return {"success": False, "error": str(e), "summary": f"Calendar error: {e}"}

    def _format_event(self, event: Dict) -> Dict:
        title    = event.get("summary", "(no title)")
        location = event.get("location", "")
        desc     = event.get("description", "")

        start_raw = event.get("start", {})
        end_raw   = event.get("end", {})

        if "dateTime" in start_raw:
            try:
                dt = datetime.fromisoformat(start_raw["dateTime"].replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M")
            except Exception:
                time_str = start_raw["dateTime"][:16]
        else:
            time_str = "All day"

        return {
            "id":       event.get("id", ""),
            "title":    title,
            "time_str": time_str,
            "start":    start_raw.get("dateTime") or start_raw.get("date", ""),
            "end":      end_raw.get("dateTime")   or end_raw.get("date", ""),
            "location": location,
            "description": desc[:200],
        }
