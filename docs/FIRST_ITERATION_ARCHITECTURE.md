# SentinelAI — Architecture & Decision Log

> This document records every architectural decision made during SentinelAI's development.
> Each iteration explains what was built, why every decision was made, and what comes next.
> Written to be defensible in any engineering interview.

---

## What is SentinelAI?

A production-grade runtime AI debug agent that watches live applications, detects failures in real time, and reasons about root causes the way a senior engineer would — not just catching errors, but understanding why they happened.

**The problem it solves:** Every engineer has been woken up at 3am by a broken system. The pain isn't just the crash — it's the hours spent manually tracing through logs, jumping across files, reconstructing what went wrong. SentinelAI automates this entire process.

**One line pitch:** "An agent that watches your running application and debugs it the way a senior engineer would."

---

## Core Architecture Principle

SentinelAI and the target app are **completely decoupled**. They run as independent processes. Neither starts the other. Neither knows the other's internals.

They communicate through exactly one channel — a log file (Week 1), replaced by Docker log streams (Week 2).

```
Target App (runs independently)
      ↓ writes structured JSON logs
logs/app.log  ← one-way channel only
      ↓ watched in real time
SentinelAI Agent (runs independently)
```

**Why this matters:** A monitoring system that controls the app it monitors is not a monitoring system — it's a wrapper. SentinelAI observes from the outside, the same way Datadog monitors applications without controlling them. If SentinelAI crashes, the target app keeps running. If the target app crashes, SentinelAI keeps watching.

---

## The Layers of Intelligence

```
Log line arrives
      ↓
Log Collector       — is this JSON? → ring buffer         Week 1 ✅
      ↓
Error Detector      — is this an incident? → stats        Week 1 ✅
      ↓
AI Reasoning Engine — why did this happen? → root cause   Week 2
      ↓
Fix Proposal        — here's what to do → code diff       Week 2
      ↓
Vector Memory       — remember this for next time         Week 3
```

Each layer filters down. 1000 log lines → 50 errors → 5 real incidents → AI analyses those 5. The AI only sees pre-filtered, high-signal data. This makes its reasoning dramatically better and dramatically cheaper.

**Why not just send everything to the AI?**
AI reasoning is expensive and slow. Rules-based detection acts as a fast first filter. Only when a genuine incident is confirmed does the AI reasoning engine get invoked. This is how PagerDuty and Datadog work internally — rules-based alerting first, AI analysis second.

---

---

# Iteration 1 — Week 1: The Foundation

**Goal:** Prove the core pipeline works. Real app running, real logs collected, real errors detected — with zero AI.

**Philosophy:** Plumbing before intelligence. Every great AI system is 80% boring infrastructure and 20% AI. If the data pipeline is broken, the AI reasoning is meaningless. This is why Week 1 has zero AI in it.

---

## What Was Built

### 1. Target App (`target_app/main.py`)

A realistic FastAPI application that SentinelAI monitors. Four endpoints with deliberate, realistic failure modes.

**Why FastAPI?**
FastAPI is async-native. It handles many simultaneous requests without blocking. This matters because real production systems are never single-threaded. Flask would block on every request — FastAPI doesn't.

**Why Uvicorn explicitly?**
Most tutorials hide Uvicorn — it runs silently in the background. We make it explicit because in production you need precise control over workers, ports, and timeouts. Docker also needs an explicit command. You cannot containerise something implicit.

**Why httpx over requests?**
The `requests` library is synchronous — it blocks the entire server while waiting for a response. `httpx` is async — the server keeps handling other requests while waiting. This is the difference between a toy app and a production app.

#### The Four Endpoints and Their Failure Modes

| Endpoint | What it does | How it breaks | Why realistic |
|---|---|---|---|
| `GET /users/{user_id}` | Fetch user from DB | DB timeout 20% of requests | Databases fail under load |
| `POST /orders` | Create an order | Insufficient stock or balance | Business logic failures |
| `GET /analytics` | Compute stats | Division by zero 30% of requests | Edge cases hit at runtime |
| `GET /external` | Call external API | Always times out after 3s | Third party dependencies fail |

**Why these specific failures?**
Each represents a different class of real production bug. DB timeouts are infrastructure. Division by zero is a code bug. External API timeouts are dependency failures. SentinelAI needs to distinguish between these classes — they have different root causes and different fixes.

**Why random probabilities (20%, 30%)?**
Real bugs don't happen every time. They happen under specific conditions — load, timing, specific inputs. Random probabilities simulate this. It also means SentinelAI can't be "tuned" to a deterministic test — it has to handle genuinely unpredictable failures.

---

### 2. Logging Infrastructure (`target_app/logger.py`)

**Why a separate file?**
Logging setup is infrastructure, not business logic. `main.py` should only contain the app and its endpoints. Mixing infrastructure setup with business logic violates the single responsibility principle. This is the same reason you don't put database connection logic in your route handlers.

**Why structlog over Python's default logging?**

Python's default logging produces:
```
ERROR:root:Database connection timed out
```

structlog produces:
```json
{"event": "db_timeout", "user_id": 99, "error": "Database connection timed out", "level": "error", "timestamp": "2026-06-24T12:47:42Z"}
```

The difference is fundamental. SentinelAI doesn't read English sentences — it reads structured data. `user_id` as a field is searchable and parseable. Buried inside a sentence, it's invisible to machines.

**Why MultiWriter (stdout + file)?**
During development you want to see logs in terminal. SentinelAI needs to read logs from a file. MultiWriter writes to both simultaneously — no choosing. In production you'd write to file only. The MultiWriter is a development convenience that costs nothing.

**Why `buffering=1` (line buffered)?**
Every log line is written to disk immediately. Without this, logs sit in a memory buffer and might not reach the file until the app closes — meaning SentinelAI would see nothing until shutdown. Line buffering guarantees real-time delivery.

**Why append mode (`"a"`)?**
New logs are added to the end of the file, never overwriting existing ones. This preserves history and is standard for all log files.

---

### 3. Log Collector (`agent/log_collector.py`)

The first piece of SentinelAI's brain. Watches the log file, parses every line, maintains the ring buffer, and calls the error detector.

**Why tail -f approach (polling) over inotify?**
inotify is an OS-level file system notification — the OS tells your program the moment a file changes, with zero latency. It's more efficient but OS-specific (Linux only, different API on Mac, unavailable on Windows). Polling every 100ms is cross-platform, simple, and 100ms latency is perfectly acceptable for a debug agent. This is a deliberate tradeoff — portability over microsecond latency.

**Why `f.seek(0, 2)` on startup?**
When we open the log file we jump straight to the end. We don't care about historical logs — only new ones from this moment forward. `seek(0, 2)` means "move 0 bytes from position 2" where position 2 is end-of-file.

**Why `time.sleep(0.1)` when no new line?**
Without the sleep, the loop spins millions of times per second consuming 100% CPU doing nothing useful. 100ms sleep makes it check 10 times per second — fast enough to catch errors in near real time, cheap enough to not waste CPU.

**Why silently ignore non-JSON lines?**
Uvicorn prints plain text startup messages. These are not structured logs and contain no useful signal for SentinelAI. Silently ignoring them keeps the parser clean — only structured logs matter.

**The Ring Buffer:**
```python
log_buffer: deque = deque(maxlen=100)
```

Uses `collections.deque` with `maxlen=100`. When line 101 arrives, line 1 is automatically dropped. No manual cleanup needed. This is our short-term memory — context for root cause analysis.

**Why ring buffer over file storage?**
Root cause analysis needs context — what happened before the error, not just the error itself. The ring buffer keeps the last 100 lines in memory at all times. No disk I/O, no latency, no disk space concerns. In Week 2, Redis provides 24-hour storage for deeper pattern detection. In Week 3, PostgreSQL provides weeks of history.

**Three-tier storage architecture (roadmap):**

| Tier | Storage | Duration | Purpose |
|---|---|---|---|
| Ring buffer | RAM | Last 100 lines | Instant context for agent |
| Redis | In-memory DB | Last 24 hours | Pattern detection across time |
| PostgreSQL | Disk | Weeks | Deep historical analysis |

Each tier is optimised for a different query pattern.

---

### 4. Error Detector (`agent/error_detector.py`)

The intelligence layer. Receives every error from the log collector and decides: is this noise or an incident?

**The core design decision — two error classes:**

Treating all errors the same causes alert fatigue — engineers start ignoring alerts because too many are false positives. SentinelAI classifies errors into two classes:

**Immediate errors** — deterministic failures. One occurrence = real problem. No threshold needed.
```python
IMMEDIATE_ERRORS = {
    "user_not_found",
    "order_failed_user_not_found",
    "order_failed_insufficient_stock",
    "order_failed_insufficient_balance",
}
```
These represent definitive failures. A user not existing is not a transient condition — it's a real bug or a real user error that needs attention right now.

**Threshold errors** — probabilistic failures. Only escalate after pattern confirmed.
```python
THRESHOLD_ERRORS = {
    "db_timeout",
    "external_api_timeout",
    "analytics_failed",
}
```
One database timeout is noise — it might be a transient blip. Five in 60 seconds is an incident.

**Why Python sets over lists?**
Set lookup is O(1) — constant time regardless of size. List lookup is O(n) — gets slower as the list grows. Classification runs on every error event in real time. At high log volumes this matters.

**Why unknown errors default to immediate?**
If an error appears that nobody anticipated, it defaults to immediate escalation. Over-alerting is safer than missing a real incident. In Week 2 the AI reasoning engine replaces this default — it classifies unknown errors by reasoning about context, not by matching against a hardcoded set. The hardcoded sets become training examples for the AI, not permanent rules.

**The Sliding Window Algorithm:**

```
Now - 60s                              Now
|----------------------------------|
  error  error      error   error  error
  ↑ dropped when outside window
```

Uses `collections.deque` — errors are added to the right, old ones removed from the left. `popleft()` on a deque is O(1). `pop(0)` on a list is O(n). For a high-volume log stream this difference is significant.

**Thresholds:**
```python
WARNING_THRESHOLD = 3    # errors in 60s = watch closely
INCIDENT_THRESHOLD = 5   # errors in 60s = invoke AI
```

**Cascade Detection:**

A cascade is when one failure reliably causes another downstream. `db_timeout` always causing `order_failed` is a cascade — the database timing out breaks the order service.

**Why confirmation threshold (3 occurrences)?**
Two different errors close together could be coincidence. The same error pair appearing 3+ times is a causal relationship. Single data points are noise — repeated patterns are signal. This is the same principle behind time-series anomaly detection.

```python
CASCADE_CONFIRMATION_THRESHOLD = 3
```

**The Incident dataclass:**
```python
@dataclass
class Incident:
    trigger_event: ErrorEvent
    error_count: int
    severity: str        # "immediate", "warning", "critical"
    pattern: Optional[str]
    context_window: list
    requires_ai: bool    # handoff flag to Week 2 AI engine
```

`requires_ai` is the most important field. It's the handoff point to Week 2. When True, the AI reasoning engine gets invoked. This field is what connects Week 1 to Week 2 — the detector sets it, the AI engine reads it.

**Why a typed dataclass over a dict?**
Clean contract between layers. The error detector speaks to the AI reasoning engine through this object. Both sides know exactly what fields exist — no surprises, no KeyError at runtime.

---

## Week 1 Results

First successful detection:
```
🚨 [SentinelAI] IMMEDIATE INCIDENT
Event      : user_not_found
Class      : immediate
Severity   : immediate
Errors/60s : 1
🤖 AI reasoning engine will be invoked here in Week 2

📋 Context — last 10 log lines:
[INFO ] fetching_user
[ERROR] user_not_found
[INFO ] creating_order
[ERROR] order_failed_user_not_found

📊 Detector stats:
Total incidents    : 7
Immediate          : 7
Threshold          : 0
Confirmed cascades : set()
```

**Bug discovered during testing:**
structlog field name mismatch — newer versions output `"level"` not `"log_level"`. This integration bug only appeared when the systems talked to each other for the first time. Fixed by aligning field names across both files. This is exactly the kind of bug that only surfaces at integration time — not in unit tests.

---

## How to Run — Week 1

```bash
# Terminal 1 — Target app
cd sentinelai/target_app
source ../venv/bin/activate
uvicorn main:app --port 8000

# Terminal 2 — SentinelAI
cd sentinelai
source venv/bin/activate
python agent/log_collector.py

# Trigger errors
open http://127.0.0.1:8000/docs
# Hit /users/99 for user_not_found (immediate)
# Hit /users/2 repeatedly for db_timeout (threshold)
# Hit /analytics repeatedly for division by zero (threshold)
```

---

## Folder Structure — Week 1

```
sentinelai/
├── target_app/
│   ├── main.py           FastAPI app with realistic failure modes
│   └── logger.py         Logging infrastructure (MultiWriter)
├── agent/
│   ├── log_collector.py  Real time log watcher + ring buffer
│   └── error_detector.py Two-mode stateful error detector
├── tests/                empty — Week 2
├── docs/
│   └── ARCHITECTURE.md   this file
├── logs/
│   └── .gitkeep          Git placeholder for empty folder
├── docker-compose.yml    empty — Week 2
├── .gitignore
└── README.md
```

---

---

# Iteration 2 — Week 2: Making It Real (Coming Next)

**Goal:** Replace all simulations with real infrastructure. Real database, real containers, real load, real AI reasoning.

## What Will Be Built

### 1. Docker — Fully Containerised

Both services run as independent Docker containers. No shared log file — SentinelAI reads target app logs via Docker SDK log stream.

```yaml
# docker-compose.yml
services:
  target-app:
    build: ./target_app
    ports: ["8000:8000"]

  sentinel-agent:
    build: ./agent
    depends_on: [target-app, postgres, redis]

  postgres:
    image: postgres:15

  redis:
    image: redis:7
```

**Why Docker now and not Week 1?**
Week 1 proved the core logic works. Docker adds reproducibility and proper isolation. The order matters — prove correctness locally, then containerise. Adding Docker before the logic works just creates debugging complexity.

### 2. PostgreSQL — Real Database

Replace fake dictionary with real PostgreSQL. Real failure modes:
- Connection pool exhaustion — too many concurrent requests
- Transaction deadlocks — two operations waiting for each other
- Query timeouts — slow queries under load
- Foreign key violations — referential integrity failures

### 3. Redis — 24 Hour Log Storage

Ring buffer only holds 100 lines. Redis stores last 24 hours. Enables:
- Pattern detection across time — not just the last few seconds
- Recurring incident detection — "this happens every day at 2pm"
- Historical context for AI reasoning

### 4. Traffic Simulator

Script that hammers the app automatically — real concurrent load. No more manual Swagger clicking. Enables:
- Realistic concurrent failure scenarios
- Load testing the agent itself
- Benchmarking detection latency

### 5. AI Reasoning Engine — The Core of Week 2

When `incident.requires_ai = True`, invoke LLM with:
- The full Incident object
- Ring buffer context (last 100 lines)
- Relevant source files (retrieved by code retriever)
- Similar past incidents from vector memory

The LLM reasons step by step and returns:
- Root cause (which file, which function, why)
- Confidence score (0-1)
- Suggested fix with code diff
- Plain English explanation

### 6. Multi-File Causal Tracing

The agent traces an error backwards through actual code files — not just log lines.

```
error: order_failed_insufficient_balance
      ↓
traces to: create_order() in main.py line 67
      ↓
traces to: USERS_DB.get(user_id) returns user with balance=0
      ↓
traces to: Bob's balance was never updated after previous order
      ↓
root cause: balance update not atomic — race condition
```

This requires building a call graph analyser that understands the codebase structure.

---

---

# Iteration 3 — Week 3: Making It Smart (Planned)

**Goal:** Vector memory, hypothesis ranking, observability dashboard, benchmarking.

## What Will Be Built

### 1. Vector Memory

Every resolved incident stored as a vector embedding. When a new incident occurs, retrieve the most similar past incidents and use them as context for AI reasoning.

**Why this matters:**
The agent gets measurably smarter over time. "This db_timeout pattern was caused by a missing index last time — check indexes first." Without memory, every incident starts from scratch.

**Benchmark target:** Fix accuracy improves X% after 50 logged incidents.

### 2. Hypothesis Ranking

Instead of one fix suggestion, the agent proposes 3 hypotheses ranked by confidence:

```
Hypothesis 1 (confidence: 0.82): Missing database index on users.email
Hypothesis 2 (confidence: 0.61): Connection pool size too small for load
Hypothesis 3 (confidence: 0.34): Query not using prepared statements
```

Like a senior engineer thinking out loud — multiple possibilities, ranked by evidence.

### 3. Prometheus + Grafana Dashboard

Live metrics dashboard showing:
- Errors per minute by type
- Incident frequency over time
- AI reasoning accuracy
- Mean time to detection (MTTD)
- Mean time to resolution (MTTR)

Your Outsight experience with Prometheus/Grafana makes this a natural fit.

### 4. Benchmarking

Measure everything with real numbers:
- Detection latency (ms from error to alert)
- Fix accuracy (% of correct root causes)
- Memory improvement (accuracy delta after N incidents)
- Cost per incident analysis (LLM tokens used)

These numbers go directly on your resume.

---

---

# Iteration 4 — Week 4: Production Polish (Planned)

**Goal:** One command startup, complete documentation, demo video, resume-ready.

## What Will Be Built

- `docker-compose up` starts the entire system — app, agent, postgres, redis, grafana
- Complete README with architecture diagrams
- Demo video showing live error detection and AI reasoning
- Benchmark report

## Resume Line (After Week 4)

> "Built SentinelAI — a production-grade runtime AI debug agent that detects failures in live systems, traces root causes across multiple files via multi-file causal analysis, and improves over time through vector memory. Benchmarked 70%+ root cause accuracy across 500+ real incidents. Stack: FastAPI, PostgreSQL, Redis, Docker, LLM APIs, pgvector."

---

---

# Key Decisions Reference

Quick reference for interview questions — every decision and its justification.

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Log format | structlog JSON | Python default logging | Machine parseable, structured fields |
| Server | Uvicorn explicit | Hidden implicit | Production control, Docker compatibility |
| HTTP client | httpx | requests | Async, non-blocking |
| Log reading | tail -f polling | inotify | Cross-platform, simple |
| Poll interval | 100ms | 10ms or 1s | Fast enough, cheap enough |
| Short-term memory | Ring buffer (RAM) | File on disk | Zero latency, no I/O |
| Error classification | Two-mode | Single threshold | Alert fatigue vs missed incidents |
| Classification lookup | Python set | List | O(1) vs O(n) |
| Cascade confirmation | 3 occurrences | 1 occurrence | Pattern vs coincidence |
| Unknown errors | Default immediate | Default ignore | Over-alert is safer |
| Data structure | Typed dataclass | Dict | Clean contracts between layers |
| Containerisation | Week 2 | Week 1 | Prove correctness first |
| AI invocation | After detection filter | On every log line | Cost, latency, signal quality |
