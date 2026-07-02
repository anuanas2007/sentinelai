import json
import time
import os
import queue
import threading
import uuid
from collections import deque
from typing import Optional
from error_detector import ErrorDetector, Incident, WINDOW_SECONDS
import ai_engine
import redis_store
import events

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
ai_queue: "queue.Queue[tuple[str, Incident]]" = queue.Queue()


BASE_APP_CONTEXT = (
    "The monitored app is a FastAPI service (target_app/main.py) backed by "
    "Postgres (target_app/db.py). It has users (id, name, email, balance) "
    "and items (name, stock); creating an order deducts both. Investigate "
    "by actually reading the relevant code -- don't assume an operation "
    "is safe just because each step looks correct in isolation, and don't "
    "default to a conclusion from a different incident you've seen "
    "before just because it's familiar."
)

# Category-level methodology hints, not one bespoke entry per event --
# four near-identical specific hints (one each for external_api_timeout/
# error, background_task_failed, email_service_unreachable) collapsed
# into one shared instruction, since they're all really the same
# question: "you're calling something you don't fully control, is OUR
# side of that adequate." Writing a new bespoke hint every time a new
# dependency-touching feature gets added doesn't scale -- this is
# meant to cover that whole category automatically instead.
#
# Still never the architecture or the answer. A hint like "create_order
# reads balance then separately calls db.apply_order, check for races"
# isn't investigation guidance, it's handing over the conclusion --
# that defeats the point of having an investigator agent.
# negative_balance_detected and analytics_failed deliberately have NO
# hint for that reason: they should be findable (or not) through the
# investigator's own code-reading, same as a real unknown bug would
# have to be -- already verified this holds (negative_balance_detected
# was found unaided, confidence 0.95, after its hint was removed).
_DEPENDENCY_HINT = (
    "This involves calling something OUR code doesn't fully control "
    "(a third party, a separate internal service, or a monitor tracking "
    "one). You have no visibility into why that dependency itself "
    "failed -- focus on whether OUR code's own handling (retries, "
    "timeouts, error handling, or coordination with other code that "
    "touches the same dependency) is adequate, rather than speculating "
    "about the dependency's internals."
)

EVENT_SPECIFIC_CONTEXT = {
    "unhandled_exception": (
        "There's no pre-existing knowledge of what this specific crash "
        "is. The event context below has a path field -- match it to "
        "the exact route decorator (e.g. @app.get(\"<path>\")) in the "
        "code, not just any function that happens to raise the same "
        "exception type elsewhere."
    ),
    "external_api_timeout": _DEPENDENCY_HINT,
    "external_api_error": _DEPENDENCY_HINT,
    "background_task_failed": _DEPENDENCY_HINT,
    "email_service_unreachable": _DEPENDENCY_HINT,
}


def _build_incident_summary(incident: Incident) -> str:
    event_name = incident.trigger_event.event
    app_context = BASE_APP_CONTEXT
    if event_name in EVENT_SPECIFIC_CONTEXT:
        app_context += " " + EVENT_SPECIFIC_CONTEXT[event_name]

    lines = [
        app_context,
        "",
        f"Event: {event_name}",
        f"Severity: {incident.severity}",
        f"Errors in window: {incident.error_count}",
    ]

    if incident.pattern and incident.cascade_peer_event:
        # Include both events' context so the AI can reason about causality,
        # not just the downstream event in isolation.
        upstream = incident.cascade_peer_event
        lines.append(f"")
        lines.append(f"Cascade pattern confirmed: {incident.pattern}")
        lines.append(f"This pair has been observed co-occurring 3+ times within {WINDOW_SECONDS}s.")
        lines.append(f"")
        lines.append(f"Upstream event: {upstream.event}")
        if upstream.context:
            lines.append(f"  Context: {upstream.context}")
        lines.append(f"Downstream event: {event_name}")
        if incident.trigger_event.context:
            lines.append(f"  Context: {incident.trigger_event.context}")
    elif incident.pattern:
        lines.append(f"Cascade pattern: {incident.pattern}")
        if incident.trigger_event.context:
            lines.append(f"Event context: {incident.trigger_event.context}")
    else:
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
    place in the agent that makes a blocking LLM call. Strictly
    sequential (one incident fully finishes before the next starts),
    which is what makes ai_engine.py's incident_id correlation safe
    without any extra locking on that side.
    """
    while True:
        incident_id, incident = ai_queue.get()
        try:
            summary = _build_incident_summary(incident)
            events.push_pipeline_event(
                "ai_analysis_started", incident_id=incident_id,
                incident_event=incident.trigger_event.event,
            )
            print(f"\n🤖 [SentinelAI] Running AI analysis on '{incident.trigger_event.event}'...", flush=True)
            result = ai_engine.analyze_incident(summary, incident.trigger_event.event, incident_id)
            print("\n" + "=" * 60, flush=True)
            print("🤖 AI ANALYSIS RESULT")
            print("=" * 60)
            print(result)
            print("=" * 60 + "\n", flush=True)
            events.push_pipeline_event(
                "ai_analysis_result",
                incident_id=incident_id,
                incident_event=incident.trigger_event.event,
                result=result,
            )
        except Exception as e:
            # Full traceback, not just str(e) — some exceptions (and
            # CrewAI's own error wrapping) produce an unhelpful empty
            # or generic message otherwise.
            import traceback
            print("⚠️  [SentinelAI] AI analysis failed:", flush=True)
            traceback.print_exc()
            events.push_pipeline_event(
                "ai_analysis_failed",
                incident_id=incident_id,
                incident_event=incident.trigger_event.event,
                error=str(e),
            )
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

    # Short, human-legible ID -- generated here (not in error_detector.py,
    # which has no concept of a UI) and threaded through every event this
    # incident produces from here on, including into ai_engine.py's tool
    # calls, so a UI can correlate "this fix came from that trigger."
    incident_id = uuid.uuid4().hex[:8]

    try:
        redis_store.write_incident(incident)
    except Exception as e:
        # Redis is for long-horizon pattern queries, not core detection --
        # losing it shouldn't take down real-time alerting on top of it.
        print(f"⚠️  [SentinelAI] Failed to write incident to Redis: {e}")

    events.push_pipeline_event(
        "incident_detected",
        incident_id=incident_id,
        incident_event=incident.trigger_event.event,
        severity=incident.severity,
        error_count=incident.error_count,
        pattern=incident.pattern,
        requires_ai=incident.requires_ai,
        ai_worthy=incident.ai_worthy,
    )

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
            ai_queue.put((incident_id, incident))
            print(f"\n  🤖 Queued for AI analysis (running in background)")
        else:
            print(f"\n  🤖 Would queue for AI analysis, but OPENAI_API_KEY is not set — skipping")
    elif incident.ai_worthy:
        # ai_worthy + not requires_ai is genuinely two different reasons,
        # previously both printed as "(cooldown)" even when the real
        # reason was just "hasn't crossed the incident threshold yet" --
        # a warning-severity incident never even attempts AI dispatch in
        # the first place, so there's nothing to have been suppressed.
        if incident.severity == "warning":
            print(f"\n  🤖 AI-worthy, but not yet escalated — below the incident threshold")
        else:
            print(f"\n  🤖 AI-worthy, but skipped — already analyzed recently (cooldown)")

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
    print(f"  AI calls suppressed (cooldown): {stats['ai_calls_suppressed']}")

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
                # target_app now truncates app.log on every fresh startup
                # (see logger.py) instead of appending forever. If it
                # restarted while we kept running, our read position is
                # now past the truncated file's actual size -- without
                # this check we'd sit stuck forever, never seeing
                # anything the restarted app writes. Re-seek to the
                # start when that happens.
                try:
                    if os.path.getsize(log_path) < f.tell():
                        f.seek(0)
                except OSError:
                    pass
                time.sleep(0.1)
                continue

            # Try to parse as structured JSON
            log_entry = parse_log_line(raw_line)

            if log_entry:
                # Add to ring buffer
                log_entry["_raw"] = raw_line.strip()
                log_buffer.append(log_entry)

                # Every line, success or failure -- this is target_app's
                # own raw activity, separate from the detection/AI
                # pipeline above. A UI showing only errors would have no
                # sense of "is the app even doing anything right now."
                events.push_activity_event(
                    "target_app_log",
                    name=log_entry.get("event", ""),
                    level=log_entry.get("level", "info"),
                )

                # If error — handle immediately
                if is_error(log_entry):
                    handle_error(log_entry)


if __name__ == "__main__":
    # uvicorn now owns the main thread (see web.py) -- the watcher loop
    # that used to block here, and the AI worker that already ran in
    # its own thread, both move to background threads instead. Neither
    # changes behavior, only which thread runs them.
    import uvicorn
    import web

    if os.environ.get("OPENAI_API_KEY"):
        threading.Thread(target=ai_worker_loop, daemon=True).start()
    else:
        print("[SentinelAI] OPENAI_API_KEY not set — AI analysis disabled")

    # LOG_PATH env var lets docker-compose point this at the shared
    # volume mount without changing the local dev default.
    LOG_PATH = os.environ.get("LOG_PATH", "logs/app.log")
    threading.Thread(target=watch_log_file, args=(LOG_PATH,), daemon=True).start()

    uvicorn.run(web.app, host="0.0.0.0", port=9000)