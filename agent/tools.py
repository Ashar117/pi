"""Tool schemas and dispatch — what Claude is allowed to call and how to execute it."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

from tools.tools_web import WebTools
from tools.tools_project import ProjectTools
from tools.tools_obsidian import ObsidianTools
from tools.tools_image import generate_image
from tools.tools_gmail import GmailTools, check_gmail_setup
from tools.tools_calendar import CalendarTools
from tools.tools_media import MediaTools
from tools.tools_tts import TTSTools
from tools.tools_telegram import send_message as _telegram_send

_web      = WebTools()
_project  = ProjectTools()
_obsidian = ObsidianTools()
_gmail    = GmailTools()
_calendar = CalendarTools()
_tts      = TTSTools()

_ROOT = Path(__file__).parent.parent


def _system_introspect(agent) -> Dict:
    """Read live system state and return a structured dict.

    Never raises — individual failures are captured as None values so the caller
    always gets a complete (if partial) result.
    """
    result: Dict = {}

    # evolution.jsonl — total interactions
    lines: list = []
    try:
        evo_path = _ROOT / "logs" / "evolution.jsonl"
        lines = [l for l in evo_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        result["total_interactions"] = len(lines)
    except Exception:
        result["total_interactions"] = None

    try:
        now = datetime.now(timezone.utc)
        last_7_ok = 0
        for l in lines:
            rec = json.loads(l)
            if rec.get("success") is not True:
                continue
            ts_str = rec.get("timestamp", "2000-01-01T00:00:00+00:00")
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts).days <= 7:
                last_7_ok += 1
        result["last_7d_successes"] = last_7_ok
    except Exception:
        result["last_7d_successes"] = None

    # tickets
    try:
        open_dir = _ROOT / "tickets" / "open"
        result["open_ticket_count"] = len(list(open_dir.glob("*.json")))
    except Exception:
        result["open_ticket_count"] = None

    try:
        closed_dir = _ROOT / "tickets" / "closed"
        result["closed_ticket_count"] = len(list(closed_dir.glob("*.json")))
    except Exception:
        result["closed_ticket_count"] = None

    # solutions
    try:
        sol_path = _ROOT / "solutions" / "SOLUTIONS.jsonl"
        sol_lines = [l for l in sol_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        result["solution_count"] = len(sol_lines)
        result["last_solution_id"] = json.loads(sol_lines[-1]).get("id") if sol_lines else None
    except Exception:
        result["solution_count"] = None
        result["last_solution_id"] = None

    # SQLite — L3 entry count
    try:
        conn = sqlite3.connect(str(agent.memory.sqlite_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM l3_cache")
        result["l3_entry_count"] = cursor.fetchone()[0]
        conn.close()
    except Exception:
        result["l3_entry_count"] = None

    # session / process info
    result["session_id"] = agent.session_id
    result["mode"] = agent.mode
    try:
        result["uptime_seconds"] = round(
            (datetime.now(timezone.utc) - agent.session_start).total_seconds()
        )
    except Exception:
        result["uptime_seconds"] = None

    return result


def get_tool_definitions() -> List[Dict]:
    """Return the static list of tool schemas Claude sees in root mode."""
    return [
        {
            "name": "memory_read",
            "description": "Search memory. Returns matching entries.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                    "tier": {"type": "string", "enum": ["l1", "l2", "l3"], "description": "Optional tier filter"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "memory_write",
            "description": "Write to memory. Auto-verifies.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "tier": {"type": "string", "enum": ["l1", "l2", "l3"], "default": "l3"},
                    "importance": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    "category": {"type": "string", "default": "note"},
                    "expiry": {"type": "string", "description": "ISO datetime"},
                    "source": {
                        "type": "string",
                        "enum": ["stated", "inferred_confirmed", "inferred_unconfirmed"],
                        "default": "stated",
                        "description": "How the fact was obtained. Use 'stated' when user said it directly, 'inferred_confirmed' when you inferred it and user confirmed, 'inferred_unconfirmed' to block accidental L3 writes of unverified guesses."
                    }
                },
                "required": ["content"]
            }
        },
        {
            "name": "memory_delete",
            "description": "Delete from memory. Soft delete = archive to L2.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "soft": {"type": "boolean", "default": True}
                },
                "required": ["target"]
            }
        },
        {
            "name": "execute_python",
            "description": "Execute Python code. Returns output/errors.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"}
                },
                "required": ["code"]
            }
        },
        {
            "name": "execute_bash",
            "description": "Execute bash command.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"]
            }
        },
        {
            "name": "read_file",
            "description": "Read file contents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "modify_file",
            "description": "Modify file (including self). String must be unique.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"}
                },
                "required": ["path", "old_str", "new_str"]
            }
        },
        {
            "name": "create_file",
            "description": "Create a new file with given content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "web_search",
            "description": (
                "Search the web via DuckDuckGo for current information. "
                "Use when you need facts beyond your training cutoff (Aug 2025), "
                "live prices, recent events, or anything that may have changed."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (1-10, default 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "search_codebase",
            "description": (
                "Search Pi's own source files for a regex pattern. "
                "Use to find function definitions, understand how a subsystem works, "
                "or locate where a variable is used before modifying it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Python regex pattern to search for"
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Glob filter, e.g. '*.py' or 'agent/*.py' (default: *.py)",
                        "default": "*.py"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max matching lines to return (default 20)",
                        "default": 20
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "create_ticket",
            "description": (
                "File a self-improvement ticket to tickets/open/. "
                "Use when you discover a bug, gap, or improvement opportunity "
                "during a session that should be tracked for future work."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title describing the issue"
                    },
                    "what_failed": {
                        "type": "string",
                        "description": "What went wrong or what the gap is"
                    },
                    "component": {
                        "type": "string",
                        "description": "File(s) responsible, e.g. 'tools/tools_memory.py'"
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["P1", "P2", "P3", "P4"],
                        "description": "P1=critical, P2=high, P3=medium, P4=low",
                        "default": "P3"
                    },
                    "where_failed": {
                        "type": "string",
                        "description": "Specific function or location (optional)"
                    },
                    "suggested_fix": {
                        "type": "string",
                        "description": "Implementation hint (optional)"
                    }
                },
                "required": ["title", "what_failed", "component"]
            }
        },
        {
            "name": "get_session_stats",
            "description": (
                "Return live stats for the current session: turns, cost, tokens, uptime. "
                "Use to answer 'how much have we spent?' or 'what mode are we in?'"
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "obsidian_read",
            "description": "Read a note from Ash's Obsidian vault by path (relative to vault root).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "e.g. 'Projects/Pi.md'"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "obsidian_write",
            "description": "Create or overwrite a note in Ash's Obsidian vault.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Note path relative to vault root"},
                    "content": {"type": "string", "description": "Full markdown content"}
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "obsidian_append",
            "description": "Append markdown text to an existing Obsidian note (creates it if absent).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "obsidian_search",
            "description": "Full-text search across Ash's Obsidian vault. Returns matching note paths and excerpts.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 10}
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_weather",
            "description": "Get current weather for any location. Empty location = auto-detect from IP.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name or 'city,country'. Leave empty to use current location."
                    }
                },
                "required": []
            }
        },
        {
            "name": "get_news",
            "description": "Get recent news headlines. Categories: global | tech | business | science | ai",
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["global", "tech", "business", "science", "ai"],
                        "description": "News category (default: global)"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of headlines to return (default 6, max 10)",
                        "default": 6
                    }
                },
                "required": []
            }
        },
        {
            "name": "get_stocks",
            "description": "Get live stock/crypto prices from Yahoo Finance. Returns price and % change.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ticker symbols e.g. ['AAPL','NVDA','BTC-USD']. Omit for default watchlist."
                    }
                },
                "required": []
            }
        },
        {
            "name": "get_tech_updates",
            "description": "Get latest HN front-page stories and ArXiv AI/ML/NLP research papers.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of items per source (default 5)",
                        "default": 5
                    }
                },
                "required": []
            }
        },
        {
            "name": "refresh_awareness",
            "description": "Force-refresh the full live awareness snapshot (weather, news, stocks, research). Use when Pi needs the absolute latest data.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "image_gen",
            "description": (
                "Generate an image from a text prompt and save it to disk. "
                "Returns the absolute file path. "
                "Backend 'pollinations' is free with no API key. "
                "Backend 'huggingface' is higher quality but needs HF_TOKEN env var."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text description of the image to generate"
                    },
                    "backend": {
                        "type": "string",
                        "enum": ["pollinations", "huggingface"],
                        "default": "pollinations",
                        "description": "Which backend to use (default: pollinations)"
                    },
                    "width": {
                        "type": "integer",
                        "default": 1024,
                        "description": "Image width in pixels (pollinations only)"
                    },
                    "height": {
                        "type": "integer",
                        "default": 1024,
                        "description": "Image height in pixels (pollinations only)"
                    },
                    "save_path": {
                        "type": "string",
                        "description": "Where to save the image. Omit for auto temp path."
                    }
                },
                "required": ["prompt"]
            }
        },
        {
            "name": "system_introspect",
            "description": (
                "Return live system state: total interactions logged, open/closed ticket counts, "
                "solution count, last solution ID, L3 cache size, session ID, mode, and uptime. "
                "Use this — not memory — when asked about Pi's own stats or history."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "daily_briefing",
            "description": (
                "Generate Ash's daily briefing: weather, top news, markets, HN, research, and active L3 context. "
                "Saves to Obsidian daily note automatically. Use on 'briefing' / 'morning' / 'what's today'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "save_to_obsidian": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to save to Obsidian daily note"
                    }
                },
                "required": []
            }
        },
        {
            "name": "gmail_inbox",
            "description": "Get Gmail inbox summary: unread count and top unread messages.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "default": 5}
                },
                "required": []
            }
        },
        {
            "name": "gmail_search",
            "description": "Search Gmail messages. Use Gmail search syntax e.g. 'is:unread from:boss' or 'subject:invoice'.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query"},
                    "max_results": {"type": "integer", "default": 10}
                },
                "required": ["query"]
            }
        },
        {
            "name": "gmail_read",
            "description": "Read the full body of a specific Gmail message by ID.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"}
                },
                "required": ["message_id"]
            }
        },
        {
            "name": "gmail_send",
            "description": "Send an email. Ash must explicitly confirm before calling this.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to":      {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string"},
                    "body":    {"type": "string"}
                },
                "required": ["to", "subject", "body"]
            }
        },
        {
            "name": "web_browse",
            "description": "Fetch and read any URL — articles, docs, GitHub, Reddit threads, etc. Returns stripped text.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url":       {"type": "string"},
                    "max_chars": {"type": "integer", "default": 8000}
                },
                "required": ["url"]
            }
        },
        {
            "name": "reddit_browse",
            "description": "Browse a subreddit's top/hot/new posts. Returns titles, scores, and links.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "subreddit": {"type": "string", "description": "Subreddit name without r/"},
                    "sort":      {"type": "string", "enum": ["hot","new","top","rising"], "default": "hot"},
                    "count":     {"type": "integer", "default": 10}
                },
                "required": ["subreddit"]
            }
        },
        {
            "name": "reddit_search",
            "description": "Search Reddit posts across all subreddits.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query":     {"type": "string"},
                    "subreddit": {"type": "string", "description": "Limit to subreddit (optional)"},
                    "count":     {"type": "integer", "default": 10}
                },
                "required": ["query"]
            }
        },
        {
            "name": "scholar_search",
            "description": "Search academic papers via Semantic Scholar (free). Returns titles, authors, year, abstract, citation count.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        },
        {
            "name": "discord_read",
            "description": "Read recent messages from a Discord channel. Requires DISCORD_BOT_TOKEN in .env.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string", "description": "Discord channel ID (numeric)"},
                    "count":      {"type": "integer", "default": 20}
                },
                "required": ["channel_id"]
            }
        },
        {
            "name": "reddit_thread",
            "description": "Read a full Reddit thread — post body + top comments. Pass the permalink URL.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "permalink":    {"type": "string", "description": "Reddit permalink URL"},
                    "max_comments": {"type": "integer", "default": 20}
                },
                "required": ["permalink"]
            }
        },
        {
            "name": "calendar_today",
            "description": "Get today's Google Calendar events.",
            "input_schema": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "calendar_upcoming",
            "description": "Get upcoming calendar events for the next N days (default 7).",
            "input_schema": {
                "type": "object",
                "properties": {"days": {"type": "integer", "default": 7}},
                "required": []
            }
        },
        {
            "name": "calendar_search",
            "description": "Search Google Calendar events by keyword.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "days":  {"type": "integer", "default": 30, "description": "Search window in days"}
                },
                "required": ["query"]
            }
        },
        {
            "name": "calendar_create",
            "description": "Create a Google Calendar event. Confirm with Ash before calling.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string"},
                    "start":       {"type": "string", "description": "ISO datetime e.g. 2026-05-06T14:00:00"},
                    "end":         {"type": "string", "description": "ISO datetime"},
                    "description": {"type": "string"},
                    "location":    {"type": "string"}
                },
                "required": ["title", "start", "end"]
            }
        },
        {
            "name": "calendar_delete",
            "description": "Delete a calendar event by event ID. Confirm with Ash first.",
            "input_schema": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"]
            }
        },
        {
            "name": "read_document",
            "description": "Extract text and tables from PDF, DOCX, PPTX, or plain text files. Returns full text content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path":      {"type": "string", "description": "Absolute or relative file path"},
                    "max_chars": {"type": "integer", "default": 50000}
                },
                "required": ["path"]
            }
        },
        {
            "name": "analyze_image",
            "description": "Analyze an image with Claude vision. Supports JPG, PNG, GIF, WebP. Ask any question about the image.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path":     {"type": "string", "description": "Path to image file"},
                    "question": {"type": "string", "description": "What to analyze or ask about the image"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "analyze_images",
            "description": "Analyze multiple images together with Claude vision — compare, find differences, etc.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "paths":    {"type": "array", "items": {"type": "string"}, "description": "List of image paths"},
                    "question": {"type": "string"}
                },
                "required": ["paths"]
            }
        },
        {
            "name": "analyze_video",
            "description": "Sample frames from a video and analyze with Claude vision.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path":       {"type": "string"},
                    "question":   {"type": "string"},
                    "max_frames": {"type": "integer", "default": 8}
                },
                "required": ["path"]
            }
        },
        {
            "name": "ocr_image",
            "description": "Extract text from an image using OCR (Tesseract first, Claude vision fallback).",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        },
        {
            "name": "analyze_document_smart",
            "description": "Smart document analysis combining text extraction + Claude vision. Best for scanned PDFs, charts, mixed content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path":     {"type": "string"},
                    "question": {"type": "string", "description": "What to focus on or ask about the document"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "detect_faces",
            "description": "Detect faces in an image and return age, gender, emotion, and race attributes using DeepFace.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        },
        {
            "name": "recognize_face",
            "description": "Identify a face in an image against the registered face database. Returns name and confidence.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path":      {"type": "string"},
                    "threshold": {"type": "number", "default": 0.4, "description": "Distance threshold — lower = stricter match"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "register_face",
            "description": "Register a person's face in the local database for future recognition. Saves to data/faces/<name>/.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to image containing the person's face"},
                    "name": {"type": "string", "description": "Person's name"}
                },
                "required": ["path", "name"]
            }
        },
        {
            "name": "list_registered_faces",
            "description": "List all people registered in the face database.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "speak",
            "description": (
                "Speak text aloud on Ash's computer using text-to-speech. "
                "Use for reading out important updates, reminders, or results. "
                "Non-blocking — Pi continues while speech plays."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to speak aloud"
                    },
                    "rate": {
                        "type": "integer",
                        "description": "Words per minute (default 175)",
                        "default": 175
                    }
                },
                "required": ["text"]
            }
        },
        {
            "name": "telegram_send",
            "description": (
                "Send a message to Ash's Telegram. Use to push important updates, "
                "completed task notifications, or alerts when Ash is away from the computer. "
                "Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Message text (Markdown supported)"
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "Override chat ID (uses TELEGRAM_CHAT_ID env var by default)"
                    }
                },
                "required": ["text"]
            }
        }
    ]


def execute_tool(agent, tool_name: str, tool_input: Dict) -> Any:
    """Execute a tool by name and track per-tool pattern stats.

    Operates on the PiAgent instance to access memory/execution/evolution
    subsystems. Mechanical lift from PiAgent._execute_tool — same dispatch
    table, same success-flag logic, same evolution.track_pattern call.
    """
    start_time = datetime.now(timezone.utc)
    success = False

    try:
        if tool_name == "memory_read":
            result = agent.memory.memory_read(
                query=tool_input["query"],
                tier=tool_input.get("tier")
            )
            success = True

        elif tool_name == "memory_write":
            expiry = None
            if "expiry" in tool_input and tool_input["expiry"]:
                expiry = datetime.fromisoformat(tool_input["expiry"])

            result = agent.memory.memory_write(
                content=tool_input["content"],
                tier=tool_input.get("tier", "l3"),
                importance=tool_input.get("importance", 5),
                category=tool_input.get("category", "note"),
                expiry=expiry,
                session_id=agent.session_id,
                source=tool_input.get("source", "stated"),
            )
            success = result.get("verified", False)

        elif tool_name == "memory_delete":
            result = agent.memory.memory_delete(
                target=tool_input["target"],
                soft=tool_input.get("soft", True)
            )
            success = result.get("deleted", 0) > 0

        elif tool_name == "execute_python":
            result = agent.execution.execute_python(code=tool_input["code"])
            success = result.get("success", False)

        elif tool_name == "execute_bash":
            result = agent.execution.execute_bash(command=tool_input["command"])
            success = result.get("success", False)

        elif tool_name == "read_file":
            result = agent.execution.read_file(path=tool_input["path"])
            success = result.get("success", False)

        elif tool_name == "modify_file":
            result = agent.execution.modify_file(
                path=tool_input["path"],
                old_str=tool_input["old_str"],
                new_str=tool_input["new_str"]
            )
            success = result.get("success", False)
            if success:
                agent.memory.memory_write(
                    content=f"Modified file: {tool_input['path']}",
                    tier="l3", importance=3, category="file_operations",
                    session_id=agent.session_id
                )

        elif tool_name == "create_file":
            result = agent.execution.create_file(
                path=tool_input["path"],
                content=tool_input["content"]
            )
            success = result.get("success", False)
            if success:
                agent.memory.memory_write(
                    content=f"Created file: {tool_input['path']}",
                    tier="l3", importance=3, category="file_operations",
                    session_id=agent.session_id
                )

        elif tool_name == "web_search":
            result = _web.web_search(
                query=tool_input["query"],
                max_results=tool_input.get("max_results", 5),
            )
            success = result.get("count", 0) > 0 or "error" not in result

        elif tool_name == "search_codebase":
            result = _project.search_codebase(
                query=tool_input["query"],
                file_pattern=tool_input.get("file_pattern", "*.py"),
                max_results=tool_input.get("max_results", 20),
            )
            success = "error" not in result

        elif tool_name == "create_ticket":
            result = _project.create_ticket(
                title=tool_input["title"],
                what_failed=tool_input["what_failed"],
                component=tool_input["component"],
                severity=tool_input.get("severity", "P3"),
                where_failed=tool_input.get("where_failed", ""),
                suggested_fix=tool_input.get("suggested_fix", ""),
            )
            success = result.get("success", False)

        elif tool_name == "get_session_stats":
            result = _project.get_session_stats(agent)
            success = True

        elif tool_name == "obsidian_read":
            result = _obsidian.obsidian_read(path=tool_input["path"])
            success = result.get("success", False)

        elif tool_name == "obsidian_write":
            result = _obsidian.obsidian_write(
                path=tool_input["path"],
                content=tool_input["content"],
            )
            success = result.get("success", False)

        elif tool_name == "obsidian_append":
            result = _obsidian.obsidian_append(
                path=tool_input["path"],
                content=tool_input["content"],
            )
            success = result.get("success", False)

        elif tool_name == "obsidian_search":
            result = _obsidian.obsidian_search(
                query=tool_input["query"],
                max_results=tool_input.get("max_results", 10),
            )
            success = result.get("success", False)

        elif tool_name == "get_weather":
            result = agent.awareness.get_weather(
                location=tool_input.get("location", ""),
                force=True,
            )
            success = result.get("success", False)

        elif tool_name == "get_news":
            result = agent.awareness.get_news(
                category=tool_input.get("category", "global"),
                count=min(tool_input.get("count", 6), 10),
                force=True,
            )
            success = result.get("success", False)

        elif tool_name == "get_stocks":
            result = agent.awareness.get_stocks(
                symbols=tool_input.get("symbols") or None,
                force=True,
            )
            success = result.get("success", False)

        elif tool_name == "get_tech_updates":
            result = agent.awareness.get_tech_updates(
                count=tool_input.get("count", 5),
                force=True,
            )
            success = result.get("success", False)

        elif tool_name == "refresh_awareness":
            agent.awareness_snapshot = agent.awareness.get_awareness_snapshot(force=True)
            result = {"success": True, "preview": agent.awareness_snapshot[:300]}
            success = True

        elif tool_name == "system_introspect":
            result = _system_introspect(agent)
            success = True

        elif tool_name == "image_gen":
            result = generate_image(
                prompt=tool_input["prompt"],
                backend=tool_input.get("backend", "pollinations"),
                width=tool_input.get("width", 1024),
                height=tool_input.get("height", 1024),
                save_path=tool_input.get("save_path"),
            )
            success = result.get("success", False)
            if success:
                result["message"] = f"Image saved to: {result['path']}"

        elif tool_name == "daily_briefing":
            from tools.tools_briefing import BriefingTools
            briefing = BriefingTools(
                awareness=agent.awareness,
                memory=agent.memory,
                obsidian=_obsidian,
                calendar=_calendar,
            )
            text = briefing.generate(save_to_obsidian=tool_input.get("save_to_obsidian", True))
            result = {"success": True, "briefing": text}
            success = True

        elif tool_name == "gmail_inbox":
            result = _gmail.inbox_summary(max_results=tool_input.get("max_results", 5))
            success = result.get("success", False)

        elif tool_name == "gmail_search":
            result = _gmail.gmail_search(
                query=tool_input["query"],
                max_results=tool_input.get("max_results", 10),
            )
            success = result.get("success", False)

        elif tool_name == "gmail_read":
            result = _gmail.gmail_read(message_id=tool_input["message_id"])
            success = result.get("success", False)

        elif tool_name == "gmail_send":
            result = _gmail.gmail_send(
                to=tool_input["to"],
                subject=tool_input["subject"],
                body=tool_input["body"],
            )
            success = result.get("success", False)

        elif tool_name == "web_browse":
            from tools.tools_browse import BrowseTools
            result = BrowseTools.fetch(
                url=tool_input["url"],
                max_chars=tool_input.get("max_chars", 8000),
            )
            success = result.get("success", False)

        elif tool_name == "reddit_browse":
            from tools.tools_browse import BrowseTools
            result = BrowseTools.reddit_browse(
                subreddit=tool_input["subreddit"],
                sort=tool_input.get("sort", "hot"),
                count=tool_input.get("count", 10),
            )
            success = result.get("success", False)

        elif tool_name == "reddit_search":
            from tools.tools_browse import BrowseTools
            result = BrowseTools.reddit_search(
                query=tool_input["query"],
                subreddit=tool_input.get("subreddit", ""),
                count=tool_input.get("count", 10),
            )
            success = result.get("success", False)

        elif tool_name == "scholar_search":
            from tools.tools_browse import BrowseTools
            result = BrowseTools.scholar_search(
                query=tool_input["query"],
                count=tool_input.get("count", 5),
            )
            success = result.get("success", False)

        elif tool_name == "discord_read":
            from tools.tools_browse import BrowseTools
            result = BrowseTools.discord_read(
                channel_id=tool_input["channel_id"],
                count=tool_input.get("count", 20),
            )
            success = result.get("success", False)

        elif tool_name == "reddit_thread":
            from tools.tools_browse import BrowseTools
            result = BrowseTools.reddit_thread(
                permalink=tool_input["permalink"],
                max_comments=tool_input.get("max_comments", 20),
            )
            success = result.get("success", False)

        elif tool_name == "calendar_today":
            result  = _calendar.calendar_today()
            success = result.get("success", False)

        elif tool_name == "calendar_upcoming":
            result  = _calendar.calendar_upcoming(days=tool_input.get("days", 7))
            success = result.get("success", False)

        elif tool_name == "calendar_search":
            result  = _calendar.calendar_search(
                query=tool_input["query"],
                days=tool_input.get("days", 30),
            )
            success = result.get("success", False)

        elif tool_name == "calendar_create":
            result  = _calendar.calendar_create(
                title=tool_input["title"],
                start=tool_input["start"],
                end=tool_input["end"],
                description=tool_input.get("description", ""),
                location=tool_input.get("location", ""),
            )
            success = result.get("success", False)

        elif tool_name == "calendar_delete":
            result  = _calendar.calendar_delete(event_id=tool_input["event_id"])
            success = result.get("success", False)

        elif tool_name == "read_document":
            result  = MediaTools.read_document(
                path=tool_input["path"],
                max_chars=tool_input.get("max_chars", 50_000),
            )
            success = result.get("success", False)

        elif tool_name == "analyze_image":
            result  = MediaTools.analyze_image(
                path=tool_input["path"],
                question=tool_input.get("question", ""),
            )
            success = result.get("success", False)

        elif tool_name == "analyze_images":
            result  = MediaTools.analyze_images(
                paths=tool_input["paths"],
                question=tool_input.get("question", ""),
            )
            success = result.get("success", False)

        elif tool_name == "analyze_video":
            result  = MediaTools.analyze_video(
                path=tool_input["path"],
                question=tool_input.get("question", ""),
                max_frames=tool_input.get("max_frames", 8),
            )
            success = result.get("success", False)

        elif tool_name == "ocr_image":
            result  = MediaTools.ocr_image(path=tool_input["path"])
            success = result.get("success", False)

        elif tool_name == "analyze_document_smart":
            result  = MediaTools.analyze_document_with_vision(
                path=tool_input["path"],
                question=tool_input.get("question", ""),
            )
            success = result.get("success", False)

        elif tool_name == "detect_faces":
            result  = MediaTools.detect_faces(path=tool_input["path"])
            success = result.get("success", False)

        elif tool_name == "recognize_face":
            result  = MediaTools.recognize_face(
                path=tool_input["path"],
                threshold=tool_input.get("threshold", 0.4),
            )
            success = result.get("success", False)

        elif tool_name == "register_face":
            result  = MediaTools.register_face(
                path=tool_input["path"],
                name=tool_input["name"],
            )
            success = result.get("success", False)

        elif tool_name == "list_registered_faces":
            result  = MediaTools.list_registered_faces()
            success = result.get("success", False)

        elif tool_name == "speak":
            result  = _tts.speak_async(text=tool_input["text"])
            success = result.get("success", False)

        elif tool_name == "telegram_send":
            result  = _telegram_send(
                text=tool_input["text"],
                chat_id=tool_input.get("chat_id"),
            )
            success = result.get("success", False)

        else:
            result = {"error": f"Unknown tool: {tool_name}"}
            success = False

        # Track pattern
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        agent.evolution.track_pattern(
            pattern_name=f"tool_{tool_name}",
            success=success,
            metadata={"duration_seconds": duration}
        )

        return result

    except Exception as e:
        agent.evolution.track_pattern(
            pattern_name=f"tool_{tool_name}",
            success=False,
            metadata={"error": str(e)}
        )
        return {"error": str(e), "success": False}
