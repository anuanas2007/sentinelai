"""
Thin HTTP/SSE layer over sentinel-agent's existing pipeline. This is
deliberately NOT a separate service re-deriving anything independently
-- it's the new face for what log_collector.py and ai_engine.py
already do, reading from the shared events.py log they already push
into. Read-only for now (live stream + history); trigger and rating
endpoints come in a later phase.
"""
import asyncio
import json
import os
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import events

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


@app.get("/api/events/history")
async def history(limit: int = 100):
    return events.get_recent_events(limit)


@app.get("/api/events/stream")
async def stream():
    """
    Server-Sent Events, not WebSocket -- this channel only ever pushes
    out, the browser never needs to send anything back over it, so the
    simpler one-directional protocol is the right fit, not the more
    complex bidirectional one.
    """
    async def event_generator():
        recent = events.get_recent_events(50)
        last_id = recent[-1]["id"] if recent else 0
        while True:
            new_events = events.get_events_since(last_id)
            for e in new_events:
                last_id = e["id"]
                yield f"data: {json.dumps(e)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
