"""
Thin HTTP/SSE layer over sentinel-agent's existing pipeline. This is
deliberately NOT a separate service re-deriving anything independently
-- it's the new face for what log_collector.py and ai_engine.py
already do, reading from the events.py logs they already push into.

Two streams, mirroring events.py's split: "pipeline" (detection -> AI
investigation -> fix proposal) and "activity" (raw target_app log
lines, success and failure alike). Kept as separate endpoints rather
than one merged stream so a UI can render them in genuinely different
places (e.g. a 4-column pipeline view next to a raw activity feed)
without having to filter a merged firehose client-side.
"""
import asyncio
import json
import os
import random
from fastapi import Body, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import events
import vector_memory

TARGET_BASE_URL = os.environ.get("TARGET_BASE_URL", "http://localhost:8000")

VALID_SCENARIOS = {"analytics_crash", "external_timeout", "user_not_found", "negative_balance", "payment_cascade"}

# Module-level task set prevents GC of fire-and-forget scenario tasks.
_scenario_tasks: set = set()
_traffic_stop_event: asyncio.Event | None = None
_traffic_task: asyncio.Task | None = None


async def _fire_scenario(
    scenario_id: str,
    calls: int | None = None,
    user_id: int | None = None,
    concurrent: int | None = None,
) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        if scenario_id == "analytics_crash":
            n = calls or 10
            await asyncio.gather(
                *[client.get(f"{TARGET_BASE_URL}/analytics") for _ in range(n)],
                return_exceptions=True,
            )
        elif scenario_id == "external_timeout":
            n = calls or 5
            await asyncio.gather(
                *[client.get(f"{TARGET_BASE_URL}/external") for _ in range(n)],
                return_exceptions=True,
            )
        elif scenario_id == "user_not_found":
            uid = user_id or 9999
            n = calls or 1
            await asyncio.gather(
                *[client.get(f"{TARGET_BASE_URL}/users/{uid}") for _ in range(n)],
                return_exceptions=True,
            )
        elif scenario_id == "negative_balance":
            uid = user_id or 1
            n = concurrent or 30
            await asyncio.gather(
                *[
                    client.post(
                        f"{TARGET_BASE_URL}/orders",
                        json={"user_id": uid, "item": "item_a", "quantity": 1},
                    )
                    for _ in range(n)
                ],
                return_exceptions=True,
            )
        elif scenario_id == "payment_cascade":
            n = concurrent or 80
            await asyncio.gather(
                *[
                    client.post(
                        f"{TARGET_BASE_URL}/orders",
                        json={
                            "user_id": random.choice([1, 2, 3]),
                            "item": random.choice(["item_a", "item_b"]),
                            "quantity": 1,
                        },
                    )
                    for _ in range(n)
                ],
                return_exceptions=True,
            )


async def _traffic_worker(stop_event: asyncio.Event) -> None:
    VALID = [1, 2, 3]
    INVALID = [99, 100, 404]
    ITEMS = ["item_a", "item_b", "item_c"]
    ACTIONS = ["get_user", "get_user", "create_order", "create_order", "analytics"]
    async with httpx.AsyncClient(timeout=10.0) as client:
        while not stop_event.is_set():
            action = random.choice(ACTIONS)
            try:
                if action == "get_user":
                    await client.get(f"{TARGET_BASE_URL}/users/{random.choice(VALID + INVALID)}")
                elif action == "create_order":
                    await client.post(
                        f"{TARGET_BASE_URL}/orders",
                        json={
                            "user_id": random.choice(VALID),
                            "item": random.choice(ITEMS),
                            "quantity": random.randint(1, 3),
                        },
                    )
                else:
                    await client.get(f"{TARGET_BASE_URL}/analytics")
            except Exception:
                pass
            await asyncio.sleep(0.2)


async def _run_traffic(stop_event: asyncio.Event) -> None:
    await asyncio.gather(*[_traffic_worker(stop_event) for _ in range(15)])

app = FastAPI(title="SentinelAI Live UI")

# Single-user local demo, not a multi-tenant deployment -- wide open
# CORS is a deliberate toy-scale simplification, not an oversight.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def _make_stream(get_since, get_recent):
    """
    Server-Sent Events, not WebSocket -- this channel only ever pushes
    out, the browser never needs to send anything back over it, so the
    simpler one-directional protocol is the right fit, not the more
    complex bidirectional one. Shared between both streams below --
    they differ only in which events.py functions they read from.
    """
    async def event_generator():
        recent = get_recent(50)
        last_id = recent[-1]["id"] if recent else 0
        while True:
            new_events = get_since(last_id)
            for e in new_events:
                last_id = e["id"]
                yield f"data: {json.dumps(e)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/events/pipeline/history")
async def pipeline_history(limit: int = 200):
    return events.get_recent_pipeline_events(limit)


@app.get("/api/events/pipeline/stream")
async def pipeline_stream():
    return _make_stream(events.get_pipeline_events_since, events.get_recent_pipeline_events)


@app.get("/api/events/activity/history")
async def activity_history(limit: int = 100):
    return events.get_recent_activity_events(limit)


@app.get("/api/events/activity/stream")
async def activity_stream():
    return _make_stream(events.get_activity_events_since, events.get_recent_activity_events)


class RatingRequest(BaseModel):
    rating: str  # "correct" | "incorrect" | "partial"
    note: str = ""


@app.post("/api/incidents/{incident_id}/rate")
async def rate_incident(incident_id: str, body: RatingRequest):
    """
    The actual point of this endpoint isn't UI polish -- a correctness
    judgment recorded here is the outcome-tracking data the learned
    classifier and fix-accuracy benchmarking have both been blocked on.
    """
    try:
        found = vector_memory.rate_incident(incident_id, body.rating, body.note)
    except Exception as e:
        return {"status": "error", "error": str(e)}
    if not found:
        return {"status": "not_found"}
    return {"status": "ok"}


class TriggerRequest(BaseModel):
    calls: int | None = None
    user_id: int | None = None
    concurrent: int | None = None


@app.post("/api/trigger/{scenario_id}")
async def trigger_scenario(scenario_id: str, body: TriggerRequest = Body(default=None)):
    if scenario_id not in VALID_SCENARIOS:
        return {"status": "unknown_scenario"}
    b = body or TriggerRequest()
    task = asyncio.create_task(_fire_scenario(scenario_id, b.calls, b.user_id, b.concurrent))
    _scenario_tasks.add(task)
    task.add_done_callback(_scenario_tasks.discard)
    return {"status": "triggered", "scenario": scenario_id}


@app.get("/api/traffic/status")
async def traffic_status():
    running = _traffic_task is not None and not _traffic_task.done()
    return {"running": running}


@app.post("/api/traffic/start")
async def traffic_start():
    global _traffic_stop_event, _traffic_task
    if _traffic_task is not None and not _traffic_task.done():
        return {"running": True, "status": "already_running"}
    _traffic_stop_event = asyncio.Event()
    _traffic_task = asyncio.create_task(_run_traffic(_traffic_stop_event))
    return {"running": True, "status": "started"}


@app.post("/api/traffic/stop")
async def traffic_stop():
    global _traffic_stop_event
    if _traffic_stop_event is not None:
        _traffic_stop_event.set()
    return {"running": False, "status": "stopped"}
