"""
Thread-safe in-memory live event logs -- exists purely so a UI can show
what sentinel-agent is doing as it happens, instead of only ever
appearing in `docker compose logs`. Every push call here is added
alongside the existing print() statements elsewhere in this codebase,
never replacing them -- nothing about current behavior changes.

Two separate, independently-bounded buffers, not one shared one --
target_app activity (every log line, success and error) is much
higher volume than the detection/AI pipeline (incidents, tool calls,
diagnoses, fixes). Sharing one buffer would mean a burst of ordinary
traffic evicts the pipeline events a UI actually cares about most.
Each stream's "since" queries only need ordering within their own
buffer, not a shared contiguous counter, but a single shared counter
is simplest and still correct -- gaps within either buffer are fine.

Bounded, not a permanent record -- this is a live activity feed for a
UI, not long-term storage (that's what redis_store.py and
vector_memory.py already are). Old events fall off once enough new
ones arrive.
"""
import time
import threading
from collections import deque

MAX_PIPELINE_EVENTS = 500
MAX_ACTIVITY_EVENTS = 300

_lock = threading.Lock()
_pipeline_events: deque = deque(maxlen=MAX_PIPELINE_EVENTS)
_activity_events: deque = deque(maxlen=MAX_ACTIVITY_EVENTS)
_next_id = 0


def _push(buffer: deque, event_type: str, **data) -> dict:
    global _next_id
    with _lock:
        _next_id += 1
        event = {
            "id": _next_id,
            "type": event_type,
            "timestamp": time.time(),
            **data,
        }
        buffer.append(event)
    return event


def push_pipeline_event(event_type: str, **data) -> dict:
    """Detection -> AI investigation -> fix proposal. Lower volume, higher signal."""
    return _push(_pipeline_events, event_type, **data)


def push_activity_event(event_type: str, **data) -> dict:
    """Raw target_app log lines, success and error alike. Higher volume."""
    return _push(_activity_events, event_type, **data)


def get_pipeline_events_since(last_id: int) -> list:
    with _lock:
        return [e for e in _pipeline_events if e["id"] > last_id]


def get_activity_events_since(last_id: int) -> list:
    with _lock:
        return [e for e in _activity_events if e["id"] > last_id]


def get_recent_pipeline_events(limit: int = 100) -> list:
    with _lock:
        return list(_pipeline_events)[-limit:]


def get_recent_activity_events(limit: int = 100) -> list:
    with _lock:
        return list(_activity_events)[-limit:]
