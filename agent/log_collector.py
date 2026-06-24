import json
import time
import os
from collections import deque
from typing import Optional

# ============================================================
# RING BUFFER
# Keeps last 100 log lines in memory at all times.
# When line 101 arrives, line 1 is dropped automatically.
# This is our short-term memory — context for root cause analysis.
# ============================================================
LOG_BUFFER_SIZE = 100
log_buffer: deque = deque(maxlen=LOG_BUFFER_SIZE)


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
        return json.loads(line)
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


def get_buffer_context() -> list:
    """
    Returns current ring buffer contents.
    This is what the agent sees when reasoning about root cause.
    """
    return list(log_buffer)


def handle_error(error_entry: dict):
    """
    Called immediately when an error is detected in the log stream.

    Right now this just prints a clear alert with context.
    In Week 2 this is where the AI reasoning engine plugs in —
    it receives this error + the ring buffer context and reasons
    about root cause.
    """
    print("\n" + "=" * 60)
    print("🚨 [SentinelAI] ERROR DETECTED")
    print("=" * 60)
    print(f"  Event     : {error_entry.get('event', 'unknown')}")
    print(f"  Level     : {error_entry.get('level', 'unknown')}")
    print(f"  Timestamp : {error_entry.get('timestamp', 'unknown')}")

    # Print all extra context fields
    skip_fields = {"event", "level", "timestamp", "_raw"}
    for key, value in error_entry.items():
        if key not in skip_fields:
            print(f"  {key:<10}: {value}")

    # Show what happened just before the error
    context = get_buffer_context()
    recent = context[-10:]  # last 10 lines for readability
    print(f"\n📋 Context — last {len(recent)} log lines before error:")
    print("-" * 60)
    for entry in recent:
        level = entry.get("level", "info").upper()
        event = entry.get("event", "")
        timestamp = entry.get("timestamp", "")[:19]  # trim microseconds
        print(f"  [{level:<8}] {timestamp} — {event}")

    print("=" * 60 + "\n")
    print("[SentinelAI] Waiting for next event...\n")


def watch_log_file(log_path: str):
    """
    Watches a log file in real time — like 'tail -f' but in Python.

    Why tail -f approach?
    The target app runs completely independently and writes to this file.
    SentinelAI has zero control over the target app — it only observes.
    This is the correct monitoring architecture.

    In Week 2 this function is replaced by Docker log stream reader —
    same concept, no file needed, cleaner separation.
    """
    print(f"[SentinelAI] Watching log file: {log_path}")
    print(f"[SentinelAI] Ring buffer size : {LOG_BUFFER_SIZE} lines")
    print(f"[SentinelAI] Waiting for logs...")
    print("-" * 60)

    # Wait for file to exist — target app might not have started yet
    while not os.path.exists(log_path):
        print(f"[SentinelAI] Log file not found yet, retrying...")
        time.sleep(1)

    with open(log_path, "r") as f:
        # Jump to end of file — we only care about new lines
        f.seek(0, 2)

        while True:
            raw_line = f.readline()

            if not raw_line:
                # No new line yet — wait a bit and try again
                time.sleep(0.1)
                continue

            # Try to parse as structured JSON
            log_entry = parse_log_line(raw_line)

            if log_entry:
                # Add to ring buffer
                log_entry["_raw"] = raw_line.strip()
                log_buffer.append(log_entry)

                # If error — handle immediately
                if is_error(log_entry):
                    handle_error(log_entry)


if __name__ == "__main__":
    LOG_PATH = "logs/app.log"
    watch_log_file(LOG_PATH)