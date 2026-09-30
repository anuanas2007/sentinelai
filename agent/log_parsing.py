import json
from typing import Optional


def parse_log_line(line: str) -> Optional[dict]:
    """
    Parse a single log line as JSON.
    Returns a dict if valid JSON, None otherwise.

    Why: Not every line the app prints is structured JSON.
    Uvicorn prints plain text startup messages too.
    We silently ignore those — only structured logs matter to us.
    """
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            return None
        parsed["_raw"] = line
        return parsed
    except json.JSONDecodeError:
        return None


def is_error(log_entry: dict) -> bool:
    """
    Returns True if this log entry represents an error.

    Why only error and critical?
    Info and warning logs are noise for our purposes.
    We only want to wake up the agent when something actually broke.
    """
    level = log_entry.get("level", "").lower()
    return level in ("error", "critical")
