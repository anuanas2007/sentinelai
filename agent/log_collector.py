import json
import time
import os
from collections import deque
from typing import Optional
from error_detector import ErrorDetector, Incident

# ============================================================
# RING BUFFER
# Keeps last 100 log lines in memory at all times.
# When line 101 arrives, line 1 is dropped automatically.
# This is our short-term memory — context for root cause analysis.
# ============================================================
LOG_BUFFER_SIZE = 100
log_buffer: deque = deque(maxlen=LOG_BUFFER_SIZE)

# Single detector instance — stateful, lives for the lifetime of the collector
detector = ErrorDetector()


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
    Called by log collector when is_error() = True.
    Passes error to detector — detector decides if it's an incident.
    """
    context = get_buffer_context()
    incident = detector.process_error(error_entry, context)

    if incident is None:
        # Below threshold — noise, ignore
        return

    # Print incident alert
    severity_emoji = "🚨" if incident.severity in ("immediate", "critical") else "⚠️"

    print("\n" + "=" * 60)
    print(f"{severity_emoji} [SentinelAI] {incident.severity.upper()} INCIDENT")
    print("=" * 60)
    print(f"  Event      : {incident.trigger_event.event}")
    print(f"  Class      : {incident.trigger_event.error_class}")
    print(f"  Severity   : {incident.severity}")
    print(f"  Errors/60s : {incident.error_count}")

    if incident.pattern:
        print(f"  Cascade    : {incident.pattern}")

    if incident.requires_ai:
        print(f"\n  🤖 AI reasoning engine will be invoked here in Week 2")

    # Show context
    recent = incident.context_window[-10:]
    print(f"\n📋 Context — last {len(recent)} log lines:")
    print("-" * 60)
    for entry in recent:
        level = entry.get("level", "info").upper()
        event = entry.get("event", "")
        timestamp = entry.get("timestamp", "")[:19]
        print(f"  [{level:<8}] {timestamp} — {event}")

    # Show detector stats
    stats = detector.get_stats()
    print(f"\n📊 Detector stats:")
    print(f"  Total incidents : {stats['total_incidents']}")
    print(f"  Immediate       : {stats['immediate_incidents']}")
    print(f"  Threshold       : {stats['threshold_incidents']}")
    print(f"  Errors in window: {stats['errors_in_window']}")

    if stats['confirmed_cascades']:
        print(f"  Confirmed cascades: {stats['confirmed_cascades']}")

    print("=" * 60 + "\n")


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
    # LOG_PATH env var lets docker-compose point this at the shared
    # volume mount without changing the local dev default.
    LOG_PATH = os.environ.get("LOG_PATH", "logs/app.log")
    watch_log_file(LOG_PATH)