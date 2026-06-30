"""
Thin HTTP/SSE layer over sentinel-agent's existing pipeline. This is
deliberately NOT a separate service re-deriving anything independently
-- it's the new face for what log_collector.py and ai_engine.py
already do, reading from the events.py logs they already push into.
Read-only for now (live stream + history); trigger and rating
endpoints come in a later phase.

Two streams, mirroring events.py's split: "pipeline" (detection -> AI
investigation -> fix proposal) and "activity" (raw target_app log
lines, success and failure alike). Kept as separate endpoints rather
than one merged stream so a UI can render them in genuinely different
places (e.g. a 4-column pipeline view next to a raw activity feed)
without having to filter a merged firehose client-side.
"""
import asyncio
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import events
import vector_memory

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
