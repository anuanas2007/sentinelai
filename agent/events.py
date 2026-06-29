"""
Thread-safe in-memory live event log -- exists purely so a UI can show
what sentinel-agent is doing as it happens, instead of only ever
appearing in `docker compose logs`. Every push_event() call here is
added alongside the existing print() statements elsewhere in this
codebase, never replacing them -- nothing about current behavior
changes.

Bounded, not a permanent record -- this is a live activity feed for a
UI, not long-term storage (that's what redis_store.py and
vector_memory.py already are). Old events fall off once enough new
ones arrive.
"""
import time
import threading
from collections import deque

MAX_EVENTS = 500

_lock = threading.Lock()
_events: deque = deque(maxlen=MAX_EVENTS)
_next_id = 0


def push_event(event_type: str, **data) -> dict:
    global _next_id
    with _lock:
        _next_id += 1
        event = {
            "id": _next_id,
            "type": event_type,
            "timestamp": time.time(),
            **data,
        }
        _events.append(event)
    return event


def get_events_since(last_id: int) -> list:
    with _lock:
        return [e for e in _events if e["id"] > last_id]


def get_recent_events(limit: int = 100) -> list:
    with _lock:
        return list(_events)[-limit:]
