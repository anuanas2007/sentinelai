# agent/

This is SentinelAI's core — the process that watches `target_app`, detects
incidents, and (when an API key is present) investigates them with AI.

## Entry point

`log_collector.py` — run with `python log_collector.py`. It starts two
background threads (log watcher, AI worker) and then hands the main thread
to `server.py`'s uvicorn process.

## Data flow

```
target_app writes structured JSON logs
        │
        ▼
log_collector.py  ←── tails logs/app.log (shared Docker volume)
        │
        ├── every line ──► events.py (activity feed → UI)
        │
        └── if error ──► error_detector.py
                              │
                        immediate? ──► Incident (severity=immediate)
                        threshold? ──► count in sliding window
                                            │
                                     warning / critical
                              │
                        requires_ai?
                              │
                    yes ──► ai_queue ──► ai_engine.py
                                              │
                                    investigator agent (reads source code)
                                              │
                                      fix proposal agent
                                              │
                                        events.py (pipeline feed → UI)
```

## Files

| File | What it does |
|---|---|
| `log_collector.py` | Entry point. Tails the log file, fills the ring buffer, calls the detector, routes AI-worthy incidents to the queue |
| `error_detector.py` | Two-tier stateful classifier: immediate errors escalate instantly, threshold errors need a confirmed pattern within a sliding window |
| `log_parsing.py` | Two pure functions: `parse_log_line` (JSON → dict) and `is_error` (dict → bool). Separated so tests can import them without pulling in CrewAI |
| `ai_engine.py` | CrewAI pipeline: investigator agent reads actual source code and diagnoses root cause; fix agent proposes a diff for human review. Never applies anything |
| `server.py` | FastAPI + SSE HTTP server. Streams pipeline and activity events to the UI, handles scenario triggers, traffic control, and fix ratings |
| `events.py` | Thread-safe in-memory event bus. Two bounded buffers: pipeline events (detection → AI → fix) and activity events (every log line) |
| `redis_store.py` | Writes incidents to Redis for 24-hour history. Used for pattern queries across time |
| `vector_memory.py` | ChromaDB-backed semantic memory. Stores past diagnoses and fixes as embeddings; retrieved by the fix agent when proposing a fix |
| `metrics.py` | Prometheus metrics definitions. Scraped by Prometheus at `/metrics` every 15s |

## Running without AI (zero cost)

Leave `OPENAI_API_KEY` empty in `.env`. Detection, ring buffer, cascades,
cooldowns, SSE streams, and the UI all work normally — the AI worker thread
simply never starts.
