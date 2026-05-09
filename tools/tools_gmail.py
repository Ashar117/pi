"""
tools/tools_gmail.py — Gmail integration for Pi.

Uses Gmail API v1 via Google OAuth2.  On first run it opens a browser to
authorize; the token is saved to data/gmail_token.json (gitignored).

Setup (one-time):
  1. Go to console.cloud.google.com → create / pick a project
  2. APIs & Services → Enable → Gmail API
  3. Credentials → Create → OAuth client ID → Desktop app → Download JSON
  4. Save the downloaded file as data/gmail_credentials.json
  5. First call auto-opens browser for consent

Available operations:
  gmail_search(query, max_results) → list of message summaries
  gmail_read(message_id)           → full message text
  gmail_inbox_summary(max_results) → unread count + top-N summaries
  gmail_send(to, subject, body)    → draft-only by default (Ash must confirm)
"""

import base64
import email as email_lib
import html
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

_ROOT = Path(__file__).parent.parent
_CREDS_FILE = _ROOT / "data" / "gmail_credentials.json"
_TOKEN_FILE = _ROOT / "data" / "gmail_token.json"

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _GOOGLE_OK = True
except ImportError:
    _GOOGLE_OK = False


def _strip_html(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


class GmailTools:

    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service
        if not _GOOGLE_OK:
            raise RuntimeError("Google API libs not installed — pip install google-auth-oauthlib google-api-python-client")
        if not _CREDS_FILE.exists():
            raise RuntimeError(
                f"Gmail credentials not found at {_CREDS_FILE}\n"
                "Download OAuth credentials from Google Cloud Console:\n"
                "  console.cloud.google.com → APIs & Services → Credentials → Create OAuth client ID\n"
                "  Save as data/gmail_credentials.json"
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

        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def is_configured(self) -> bool:
        return _CREDS_FILE.exists()

    # ── Gmail operations ───────────────────────────────────────────────────────

    def gmail_search(self, query: str = "is:unread", max_results: int = 10) -> Dict:
        """Search Gmail. Returns list of message summaries."""
        try:
            svc = self._get_service()
            resp = svc.users().messages().list(
                userId="me", q=query, maxResults=max_results
            ).execute()

            messages = resp.get("messages", [])
            if not messages:
                return {"success": True, "count": 0, "messages": [], "query": query}

            summaries = []
            for msg_ref in messages[:max_results]:
                summary = self._get_message_summary(svc, msg_ref["id"])
                if summary:
                    summaries.append(summary)

            return {"success": True, "count": len(summaries), "messages": summaries, "query": query}
        except Exception as e:
            return {"success": False, "error": str(e), "messages": []}

    def gmail_read(self, message_id: str) -> Dict:
        """Read full text of a specific message."""
        try:
            svc = self._get_service()
            msg = svc.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "(no subject)")
            sender  = headers.get("From", "unknown")
            date    = headers.get("Date", "")
            body    = self._extract_body(msg.get("payload", {}))

            return {
                "success":    True,
                "id":         message_id,
                "subject":    subject,
                "from":       sender,
                "date":       date,
                "body":       body[:4000],
                "body_chars": len(body),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def inbox_summary(self, max_results: int = 5) -> Dict:
        """Quick inbox snapshot: unread count + top-N unread summaries."""
        try:
            svc = self._get_service()

            # Unread count
            profile = svc.users().getProfile(userId="me").execute()
            messages_total = profile.get("messagesTotal", 0)

            # Top unread
            resp = svc.users().messages().list(
                userId="me", q="is:unread", maxResults=max_results
            ).execute()
            unread_msgs = resp.get("messages", [])
            unread_count = resp.get("resultSizeEstimate", len(unread_msgs))

            summaries = []
            for m in unread_msgs[:max_results]:
                s = self._get_message_summary(svc, m["id"])
                if s:
                    summaries.append(s)

            lines = [f"**{unread_count} unread** of {messages_total} total"]
            for s in summaries:
                lines.append(f"- **{s['from_short']}** — {s['subject']} _{s['date_short']}_")

            return {
                "success":      True,
                "unread_count": unread_count,
                "total":        messages_total,
                "summaries":    summaries,
                "summary":      "\n".join(lines),
            }
        except Exception as e:
            return {"success": False, "error": str(e), "summary": f"Gmail error: {e}"}

    def gmail_send(self, to: str, subject: str, body: str) -> Dict:
        """Send an email. Returns draft info — actual send requires Ash confirmation."""
        try:
            svc = self._get_service()

            # Build RFC 2822 message
            raw_msg = (
                f"To: {to}\n"
                f"Subject: {subject}\n"
                f"Content-Type: text/plain; charset=utf-8\n\n"
                f"{body}"
            )
            encoded = base64.urlsafe_b64encode(raw_msg.encode("utf-8")).decode("ascii")

            result = svc.users().messages().send(
                userId="me",
                body={"raw": encoded}
            ).execute()

            return {
                "success":    True,
                "message_id": result.get("id"),
                "to":         to,
                "subject":    subject,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get_message_summary(self, svc, message_id: str) -> Optional[Dict]:
        try:
            msg = svc.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "(no subject)")
            sender  = headers.get("From", "unknown")
            date    = headers.get("Date", "")

            # Shorten sender: extract name or email
            from_short = re.sub(r"\s*<[^>]+>", "", sender).strip() or sender.split("<")[0].strip() or sender

            # Shorten date
            date_short = re.sub(r"\s+\(.*?\)", "", date).strip()
            date_short = date_short[:16] if len(date_short) > 16 else date_short

            return {
                "id":         message_id,
                "subject":    subject[:80],
                "from":       sender,
                "from_short": from_short[:30],
                "date":       date,
                "date_short": date_short,
                "snippet":    msg.get("snippet", "")[:120],
            }
        except Exception:
            return None

    def _extract_body(self, payload: Dict) -> str:
        """Recursively extract plaintext body from MIME payload."""
        mime = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data", "")

        if mime == "text/plain" and body_data:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")

        if mime == "text/html" and body_data:
            raw = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
            return _strip_html(raw)

        for part in payload.get("parts", []):
            result = self._extract_body(part)
            if result:
                return result

        return ""


def check_gmail_setup() -> Dict:
    """Check if Gmail is configured and return status."""
    if not _GOOGLE_OK:
        return {
            "configured": False,
            "reason": "Google API libs missing — pip install google-auth-oauthlib google-api-python-client",
        }
    if not _CREDS_FILE.exists():
        return {
            "configured": False,
            "reason": (
                f"No credentials file at {_CREDS_FILE}.\n"
                "Download from Google Cloud Console:\n"
                "  1. console.cloud.google.com → create project\n"
                "  2. Enable Gmail API\n"
                "  3. Credentials → Create OAuth 2.0 Client ID (Desktop)\n"
                "  4. Download JSON → save as data/gmail_credentials.json\n"
                "Pi will open a browser for consent on first use."
            ),
        }
    return {"configured": True, "token_exists": _TOKEN_FILE.exists()}
