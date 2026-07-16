"""Error redaction — single chokepoint for outward-facing exception strings.

Four audience modes:
  'public_log' — type name only (e.g. "ValueError"); strictest, for shared logs
  'telegram'   — redacted single-line message, capped at 200 chars
  'user'       — redacted message, no length cap
  'dev'        — full message + traceback, no redaction; for internal debug paths only

Redaction order: strip tracebacks first, then absolute paths, then key patterns.
"""
import re
import traceback

# Matches Windows (e:\pi\...) and Unix (/Users/...) absolute paths
_PATH_RE = re.compile(
    r"""
    (?:
        [A-Za-z]:\\(?:[^\s"',;|<>]+)   # Windows: C:\foo\bar
      | /(?:[^\s"',;|<>]+)              # Unix: /Users/ash/...
    )
    """,
    re.VERBOSE,
)

# Matches common API key patterns
_KEY_RE = re.compile(
    r"""
    (?:
        sk-[A-Za-z0-9\-_]{10,}         # OpenAI / Anthropic
      | eyJ[A-Za-z0-9\-_=.+/]{10,}     # JWT (base64 header eyJ...)
      | AKIA[A-Z0-9]{16}               # AWS access key
      | gsk_[A-Za-z0-9\-_]{10,}        # Groq
    )
    """,
    re.VERBOSE,
)

# Matches Python traceback blocks through to the end of the exception line
_TRACE_RE = re.compile(
    r"Traceback \(most recent call last\):.*?(?=\n\S|\Z)",
    re.DOTALL,
)


def _redact(text: str) -> str:
    text = _TRACE_RE.sub("", text)
    text = _PATH_RE.sub("<path>", text)
    text = _KEY_RE.sub("<key>", text)
    return text.strip()


def safe_error(e: Exception, *, audience: str = "user") -> str:
    """Return a safe string representation of exception *e* for *audience*.

    Never raises — falls back to type name on any internal error.
    """
    try:
        if audience == "public_log":
            return type(e).__name__

        if audience == "dev":
            parts = ["".join(traceback.format_exception(type(e), e, e.__traceback__))]
            cause = e.__cause__ or e.__context__
            if cause:
                parts.append(
                    "".join(traceback.format_exception(type(cause), cause, cause.__traceback__))
                )
            return "\n".join(parts).rstrip()

        # user / telegram — redact
        msg = str(e).strip() or type(e).__name__

        # Include redacted cause if present
        cause = e.__cause__ or e.__context__
        if cause:
            cause_msg = _redact(str(cause).strip() or type(cause).__name__)
            msg = f"{msg} (caused by {type(cause).__name__}: {cause_msg})"

        msg = _redact(msg)
        if not msg:
            msg = type(e).__name__

        if audience == "telegram":
            if len(msg) > 200:
                msg = msg[:197] + "..."

        return msg

    except Exception:
        return type(e).__name__
