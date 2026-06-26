import json
import time
import os
import queue
import threading
from collections import deque
from typing import Optional
from error_detector import ErrorDetector, Incident
import ai_engine

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

# ============================================================
# AI DISPATCH QUEUE
# requires_ai incidents go here instead of being analyzed inline.
# A background thread (started in __main__) consumes this queue, so
# the log-watching loop below never blocks waiting on an LLM call.
# ============================================================
ai_queue: "queue.Queue[Incident]" = queue.Queue()


APP_CONTEXT = (
    "The monitored app is a FastAPI service (target_app/main.py) backed by "
    "Postgres (target_app/db.py). It has users (id, name, email, balance) "
    "and items (name, stock); creating an order deducts both. main.py's "
    "create_order reads current balance/stock (via db.get_user/db.get_item), "
    "checks them in Python, then separately calls db.apply_order to write "
    "the deduction. If you're investigating a balance/stock anomaly, check "
    "whether that read-then-write sequence holds up under concurrent "
    "requests -- don't assume it's safe just because each step looks "
    "correct in isolation. main.py also has a call_external() function "
    "that calls a third-party HTTP endpoint with a fixed client timeout "
    "and parses the response as JSON. If you're investigating an "
    "external_api_timeout/external_api_error incident, you have no "
    "visibility into the third party itself -- focus on whether OUR "
    "code's timeout value, status-code handling, and response parsing "
    "are appropriate for what it's actually calling, rather than "
    "speculating about the third party's internals. main.py also has a "
    "catch_all_exceptions handler registered for any exception no more "
    "specific handler caught. If you're investigating an "
    "unhandled_exception incident, there's no pre-existing knowledge of "
    "what this is -- use the event's error_type/error/path fields (in "
    "the event context below) to find the actual endpoint function "
    "handling that path, read it, and trace what could realistically "
    "raise that specific exception type there. Don't guess generically; "
    "find the actual line."
)


def _build_incident_summary(incident: Incident) -> str:
    lines = [
        APP_CONTEXT,
        "",
        f"Event: {incident.trigger_event.event}",
        f"Severity: {incident.severity}",
        f"Errors in window: {incident.error_count}",
    ]
    if incident.pattern:
        lines.append(f"Cascade pattern: {incident.pattern}")
    if incident.trigger_event.context:
        lines.append(f"Event context: {incident.trigger_event.context}")
    lines.append("Recent log lines:")
    for entry in incident.context_window[-10:]:
        lines.append(f"  [{entry.get('level', 'info').upper()}] {entry.get('event', '')}")
    return "\n".join(lines)


def ai_worker_loop():
    """
    Runs forever in a background thread. Pulls one incident at a time
    off ai_queue and runs the CrewAI pipeline on it — this is the only
    place in the agent that makes a blocking LLM call.
    """
    while True:
        incident = ai_queue.get()
        try:
            summary = _build_incident_summary(incident)
            print(f"\n🤖 [SentinelAI] Running AI analysis on '{incident.trigger_event.event}'...", flush=True)
            result = ai_engine.analyze_incident(summary)
            print("\n" + "=" * 60, flush=True)
            print("🤖 AI ANALYSIS RESULT")
            print("=" * 60)
            print(result)
            print("=" * 60 + "\n", flush=True)
        except Exception:
            # Full traceback, not just str(e) — some exceptions (and
            # CrewAI's own error wrapping) produce an unhelpful empty
            # or generic message otherwise.
            import traceback
            print("⚠️  [SentinelAI] AI analysis failed:", flush=True)
            traceback.print_exc()
        finally:
            ai_queue.task_done()


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
        if os.environ.get("OPENAI_API_KEY"):
            ai_queue.put(incident)
            print(f"\n  🤖 Queued for AI analysis (running in background)")
        else:
            print(f"\n  🤖 Would queue for AI analysis, but OPENAI_API_KEY is not set — skipping")

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
    # Daemon thread so it doesn't block process exit; started once,
    # before the log-watching loop, since the loop runs forever.
    if os.environ.get("OPENAI_API_KEY"):
        threading.Thread(target=ai_worker_loop, daemon=True).start()
    else:
        print("[SentinelAI] OPENAI_API_KEY not set — AI analysis disabled")

    # LOG_PATH env var lets docker-compose point this at the shared
    # volume mount without changing the local dev default.
    LOG_PATH = os.environ.get("LOG_PATH", "logs/app.log")
    watch_log_file(LOG_PATH)