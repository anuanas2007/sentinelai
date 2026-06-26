"""
24-hour incident history, for long-horizon pattern questions the
in-memory sliding window in error_detector.py can't answer (it forgets
everything after WINDOW_SECONDS=60). Stores only confirmed Incident
objects, not raw log lines -- same "filter down to high-signal data"
philosophy as the rest of this pipeline, and keeps this small even
under heavy traffic-simulator load (negative_balance_detected/
analytics_failed are inherently rare even when thousands of raw log
lines fly by).

Deliberately NOT used for short-window queries (e.g. "last 5 minutes")
-- that's already solved in-memory by error_detector.py's sliding
window. This module's job is the long horizon: "has this happened
before today," "how many times this week" -- nothing currently answers
that.

Not yet wired into ai_engine.py's context -- validated standalone
first, per the project's usual one-piece-at-a-time sequencing.
"""
import os
import json
import time
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
RETENTION_SECONDS = 24 * 60 * 60  # 24 hours

_client: "redis.Redis | None" = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def write_incident(incident) -> None:
    """
    Stores one confirmed incident with a native 24h TTL -- Redis expires
    it automatically, no cleanup job needed -- and indexes it in a
    per-event-type sorted set (timestamp as score) for efficient
    time-range queries.
    """
    client = get_client()
    now = time.time()
    event_name = incident.trigger_event.event
    incident_id = f"{event_name}:{now}"

    record = {
        "event": event_name,
        "severity": incident.severity,
        "error_count": incident.error_count,
        "pattern": incident.pattern,
        "requires_ai": incident.requires_ai,
        "timestamp": incident.trigger_event.timestamp,
        "wall_time": now,
    }

    client.setex(f"incident:{incident_id}", RETENTION_SECONDS, json.dumps(record))

    # Sorted-set members don't expire on their own the way SETEX keys do --
    # trim anything older than the retention window on every write so the
    # index stays in sync with what's actually still alive.
    zkey = f"incidents:{event_name}"
    client.zadd(zkey, {incident_id: now})
    client.zremrangebyscore(zkey, 0, now - RETENTION_SECONDS)


def count_in_window(event_name: str, hours: float = 24) -> int:
    """How many times this event was a confirmed incident in the last N hours."""
    client = get_client()
    now = time.time()
    zkey = f"incidents:{event_name}"
    client.zremrangebyscore(zkey, 0, now - RETENTION_SECONDS)
    return client.zcount(zkey, now - hours * 3600, now)
