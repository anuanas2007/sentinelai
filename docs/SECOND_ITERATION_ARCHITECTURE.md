# SentinelAI — Iteration 2 (Week 2): Making It Real

> Continues the decision log started in [FIRST_ITERATION_ARCHITECTURE.md](FIRST_ITERATION_ARCHITECTURE.md).
> That file documents Week 1 as it was actually built, plus the original Week 2 plan.
> This file documents Week 2 as it is actually built — some decisions diverge from that original plan, with the reasoning for the divergence recorded here.

---

## 1. Docker — Fully Containerised

**Goal:** Both services run as independent Docker containers instead of as two processes on the host.

**What changed from the original plan:** the original plan said "no shared log file — SentinelAI reads target app logs via Docker SDK log stream." Once it came time to build it, that turned out to be the wrong call. The actual implementation keeps the log file, just moved into a shared Docker volume.

```yaml
# docker-compose.yml (current — Postgres/Redis come in their own steps below)
services:
  target-app:
    build: ./target_app
    ports: ["8000:8000"]
    environment:
      - LOG_PATH=/app/logs/app.log
    volumes:
      - log-data:/app/logs

  sentinel-agent:
    build: ./agent
    depends_on: [target-app]
    environment:
      - LOG_PATH=/app/logs/app.log
    volumes:
      - log-data:/app/logs:ro

volumes:
  log-data:
```

**Why a shared volume instead of Docker SDK log streaming?**

Two options were on the table for how the agent reads target-app logs once both run as containers:

1. **Shared named volume + file tailing (chosen).** Both containers mount the same `log-data` volume. `agent/log_collector.py` keeps tailing the file exactly as in Week 1 — only the path changed, via a `LOG_PATH` env var that defaults back to the Week 1 local path when unset (so local dev without Docker still works unmodified). The agent still only ever reads a file; it has no idea the target app is even running in a container.
2. **Docker SDK log streaming (`docker-py`).** The agent attaches directly to the target-app container's stdout via the Docker Engine API. This requires mounting `/var/run/docker.sock` into the agent container — which grants it control over the entire Docker daemon, not just visibility into the target app. It could inspect, stop, or start *any* container on the host.

Option 2 reads as more impressive on a resume line, but it actually violates this project's core architecture principle ("neither knows the other's internals") worse than the Week 1 log file ever did. A shared volume keeps the agent's knowledge of the target app limited to its log output — the same boundary Datadog respects when it observes a process from outside. Giving the agent root-equivalent access to the Docker daemon just to read logs is a much bigger trust boundary to cross for no real benefit at this stage of the project. The Docker SDK approach is worth revisiting only if a future requirement genuinely needs control-plane access — e.g., auto-restarting a crashed container — not for log reading alone.

**Why Docker now and not Week 1?** (unchanged from the original plan)
Week 1 proved the core logic works. Docker adds reproducibility and proper isolation. The order matters — prove correctness locally, then containerise. Adding Docker before the logic worked would have just added debugging complexity on top of an unproven system.

**Why `LOG_PATH` as an env var instead of hardcoding the container path?**
`target_app/logger.py` and `agent/log_collector.py` both defaulted to host-relative paths (`../logs/app.log` and `logs/app.log`) written for Week 1's "two processes on one machine" setup. Hardcoding the new container path (`/app/logs/app.log`) into the code would have broken local, non-Docker development. Reading the path from an env var with the Week 1 default as fallback means both workflows — bare `uvicorn`/`python` for fast local iteration, and `docker-compose up` for the real containerised run — work unmodified.

**Bug discovered during testing:** `sentinel-agent`'s incident alerts (`print()` statements in `log_collector.py`) never appeared in `docker compose logs`, even though the container was running and the log file was being written and read correctly. Cause: Python buffers stdout in fixed-size blocks when it isn't attached to a TTY — which is always true inside a container. The output wasn't lost, just sitting in a buffer that hadn't filled yet. Fixed by setting `PYTHONUNBUFFERED=1` on the `sentinel-agent` service. This is the same category of bug as the Week 1 structlog field-name mismatch — invisible until two systems are actually wired together and observed running, not something a unit test would catch.

---

## 2. PostgreSQL — Real Database

**Goal:** Replace `USERS_DB`/`INVENTORY` (in-memory dicts) with a real Postgres database, so failure modes that only exist in real databases — connection pool exhaustion, FK violations, deadlocks — become genuinely reproducible instead of simulated with `random.random()`.

### Driver: `asyncpg`, raw SQL, no ORM

Two real options: `asyncpg` (raw SQL, async-native) vs SQLAlchemy 2.0 async (ORM). Chose `asyncpg`. Reasons:
- **Direct pool control.** `asyncpg.create_pool(min_size=, max_size=)` lets the pool size itself be small and deliberate (`min_size=2, max_size=5` — see below), which is what makes pool exhaustion a real, reproducible failure under concurrent load rather than a coin flip. An ORM's pool is a layer further removed from this control.
- **Transparency for root-cause tracing.** The whole point of this project is reasoning about *why* something broke. Raw SQL means the future AI reasoning engine can read the exact query that failed; an ORM would generate that SQL implicitly, adding a layer to reverse-engineer.
- Consistent with the project's existing async-everywhere stance (FastAPI, `httpx` over `requests`).

An ORM + Alembic would be more resume-conventional, but the convenience (relationship modeling, migration tooling) isn't needed for 3 small, fixed tables — it would be complexity for its own sake.

### Schema: 3 tables, plain `init.sql`, no migrations tool

`users`, `items`, `orders` (see `db/init.sql`) — `orders` is new; Week 1 only ever mutated balance/stock in memory with no order history. Adding it gives:
- A real **foreign key constraint** (`orders.user_id → users.id`, `orders.item_name → items.name`) — directly enables the "FK violation" failure mode from the original Week 2 plan. MongoDB/NoSQL alternatives were considered and rejected here specifically because they have no FK concept — there'd be nothing to violate, and this failure mode would have to be faked in application code instead of coming from the database engine itself.
- Order history for the AI reasoning engine to trace later.

Schema is created once via Postgres's own `docker-entrypoint-initdb.d` mechanism (plain SQL file, auto-run on first container start against an empty volume) rather than Alembic. The schema isn't expected to evolve mid-iteration — versioned migrations would be solving a problem this project doesn't have yet, the same reasoning Week 1 used to justify deferring Docker until the core logic worked.

### Connection pool: small and timed out on purpose

`min_size=2, max_size=5`, with `pool.acquire(timeout=3.0)` (`target_app/db.py`). A small pool means a 6th concurrent request genuinely has to wait for a connection to free up — real exhaustion, not simulated. The timeout turns "no connection available" into a controlled, catchable `DBPoolExhausted` exception instead of a request hanging forever.

**Honesty about manual verification:** tested with bursts of 20 and then 300 concurrent requests against `/users/{id}` — both returned all `200`s, no exhaustion triggered. This is expected, not a bug: each query is a single-row `SELECT` that completes in microseconds, so even 300 requests cycle through 5 connections faster than the 3-second timeout could ever be hit. The pool/timeout mechanism is correct by code inspection, but a one-off burst of fast queries can't actually produce sustained contention. Real verification of this failure mode is pending the traffic simulator (later this week), which can generate *sustained* concurrent load over time rather than a single burst — same "documented as a gap, not faked" approach as the `db_deadlock` caveat below.

### Mapping real Postgres failures onto the existing two-mode classifier

New events added to `agent/error_detector.py`'s existing `IMMEDIATE_ERRORS`/`THRESHOLD_ERRORS` sets — **no changes to the detector's logic itself**, which is a good sign the Week 1 classification design generalizes beyond simulated failures:

| Event | Class | Why |
|---|---|---|
| `order_failed_fk_violation` | immediate | a dangling reference is a real bug, not a transient blip |
| `db_pool_exhausted` | threshold | one slow moment under load is noise; a pattern means a real capacity problem |
| `db_deadlock` | threshold | Postgres's own deadlock detector already resolves a single deadlock by killing one transaction; only a recurring pattern signals real contention |
| `db_connection_error` | threshold | one connection blip (e.g. Postgres restarting) vs. a real outage |

`db_timeout` (the Week 1 simulated event) was removed from `THRESHOLD_ERRORS` since nothing emits it anymore — see below.

**`db.py` raises its own typed exceptions** (`DBPoolExhausted`, `DBConnectionError`, `DBDeadlock`, `DBForeignKeyViolation`) rather than letting `asyncpg`'s exception types leak into `main.py`. Same "clean contract between layers" reasoning as the `Incident` dataclass in Week 1 — `main.py` only needs to know these four names, never `asyncpg` internals.

**Honesty about `db_deadlock`:** a real Postgres deadlock needs two transactions acquiring the same rows in opposite order. `apply_order` always touches `items` → `users` → `orders` in that same order on every call, so this specific code path is unlikely to produce a genuine deadlock under normal load. The classification and handler are kept (defensive, matches the agreed design), but triggering it for real would need a second code path with reversed lock ordering — not built yet, flagged here rather than overclaimed.

### Removed the simulated `db_timeout`

Week 1's `get_user` had `if random.random() < 0.2: await asyncio.sleep(5)` to fake a DB timeout. Once Postgres is real, that randomness should come from genuine concurrency pressure on the connection pool, not a coin flip — keeping both would mean two unrelated things produce the same log event, which muddies what `db_timeout` actually means. Removed entirely; `db_pool_exhausted`/`db_connection_error` are now the only DB-failure events, and they only fire from real conditions.

### The intentional race condition (kept, not fixed)

`create_order` reads the user's balance and the item's stock (`db.get_user`, `db.get_item`), checks them in Python, *then* writes via `db.apply_order` — and nothing re-validates that read at write time. Two concurrent orders from the same user can both read "balance = 100," both pass the check, both write, both succeed — overdrawing the balance.

This is deliberate, not an oversight: this project's own Week 2 plan names this exact scenario as a planned root-cause demo for the AI reasoning engine ("root cause: balance update not atomic — race condition"). Fixing it now with `SELECT ... FOR UPDATE` or an atomic conditional `UPDATE` would remove the bug the roadmap is counting on existing later. `apply_order`'s three internal writes (stock, balance, order insert) *are* wrapped in a transaction — that only prevents *this function* from partially applying (e.g. on a mid-write crash), it does not close the race with the earlier read, which is exactly where the intentional gap lives.

### Credentials: `.env`, gitignored, referenced by name only

`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` live in `.env` (gitignored) with `.env.example` committed as a placeholder template. `docker-compose.yml` and `target_app`'s `DATABASE_URL` reference these by `${VAR}` substitution — nothing in the codebase needs to know the actual values. For this project, the password's strength barely matters in practice (Postgres isn't exposed to the host or the internet — only `target-app`'s container can reach it over Docker's internal network), but the credentials/`.env` boundary is the actual safety mechanism regardless of exposure, which is why it's enforced anyway.

### Startup ordering: healthcheck, not just `depends_on`

`depends_on: postgres` alone only waits for the *container process* to start, not for Postgres to be ready to accept connections (it takes a couple seconds to initialize on a cold start). Without a healthcheck, `target-app` could start and immediately fail its first connection attempt. Fixed with a `pg_isready` healthcheck on the `postgres` service and `depends_on: postgres: condition: service_healthy` on `target-app`.

---

## 3. AI Reasoning Engine

**Goal:** When an incident's log line names *what* broke but not *why*, hand it to an LLM-backed pipeline that reads the actual code, proposes a root cause with a confidence score, and drafts a fix for a human to review. Never auto-applies anything.

### Why most incidents never reach the AI at all

Building this surfaced a real design flaw worth naming directly: by default, `requires_ai` had been set to `True` on every immediate/critical incident, including fully self-explanatory ones (`user_not_found` — the log line *is* the root cause). Calling an LLM on those would be decorative, not useful, and would undermine the project's own "AI debug agent" framing rather than support it.

`AI_WORTHY_EVENTS` in `error_detector.py` now narrows this to exactly the cases where the log names the symptom but not the cause:
- `negative_balance_detected` — visible that the balance went negative, not *why* (requires reading the non-atomic check-then-write gap in `db.py`/`main.py`)
- `analytics_failed` — visible that it's a `ZeroDivisionError`, not *which* variable or *why* it's structurally always zero (requires reading `main.py`)
- any confirmed cascade pattern — "is this real causation or coincidence" is a genuine hypothesis question the detector can flag but not answer

`db_pool_exhausted`/`db_deadlock`/`db_connection_error` were deliberately left out: pool exhaustion *could* be AI-worthy (it can mean either genuine capacity pressure or a slow query/leak masking real capacity — a real ambiguity), but no instrumentation exists yet (e.g. per-connection hold-time logging) to give an LLM more to reason with than the bare event already says. Revisit once that instrumentation exists, rather than marking it AI-worthy prematurely.

**A flagship trigger had a real gap that had to be fixed first:** the intentional balance race condition (kept deliberately non-atomic, see section 2 above) previously produced a *silent* data-integrity bug — two concurrent orders could overdraw a balance with no log event marking it as wrong, meaning there was no incident to ever hand off to AI in the first place. `db.apply_order` now returns the post-write balance via `UPDATE ... RETURNING`, and `create_order` logs `negative_balance_detected` when it goes negative — detected after the fact, not prevented; the race itself stays intentional. Verified for real: two genuinely concurrent orders against a user's exact balance both succeeded, balance went negative, and the agent raised an immediate incident showing both interleaved `creating_order` calls in context.

### Multi-agent, not a single LLM call

> Original design below was 3 agents; consolidated to 2 after testing — see "Then consolidated 3 agents down to 2" further down for why and what changed. Kept here as the record of the original reasoning, not the current state.

Three CrewAI agents, run sequentially, each with one responsibility:
1. **Retrieval agent** — reads relevant source files via a custom `read_source_file` tool, restricted to the target app's source directory only (filename basename only, no path traversal).
2. **Hypothesis agent** — given the incident summary and retrieved code, states the most likely root cause with a confidence score, explicitly allowed to say "ambiguous" rather than overclaim.
3. **Fix-proposal agent** — drafts a small code diff and plain-English explanation, explicitly instructed to state it is a *proposal requiring human review*, not an applied change.

Chosen over a single call because each step is a genuinely distinct responsibility (reading code vs. diagnosing vs. proposing a fix), and over hand-rolled orchestration because the three-agent shape matches the project's own multi-agent design intent more directly than assembling it from raw SDK calls. LLM: OpenAI (`gpt-4o-mini` — root-cause text generation doesn't need a flagship model; swap the `LLM_MODEL` constant in `ai_engine.py` if a cheaper/newer model becomes available).

**A real bug found during first live test: the retrieval agent guessed filenames instead of discovering them.** First end-to-end run, the fix-proposal agent returned a plausible-looking diff referencing functions that don't exist anywhere in this codebase (`get_balance`, `place_order`, `log_error`) — it had hallucinated a generic "race condition fix" rather than reasoning about the actual code. Traced it to the retrieval agent: given only `read_source_file` (which takes a filename), it guessed `order_processing.py`, then `logging.py`, both wrong (real files: `main.py`, `db.py`, `logger.py`) — it had no way to discover what files actually exist, only guess from naming convention. By the third guess it stumbled onto `main.py`, but the wasted attempts (and the model's limited reasoning budget) meant the final answer still wasn't well grounded.

**Fix:** added a `list_source_files` tool the retrieval agent must call first, and tightened all three task descriptions to explicitly require quoting real code verbatim and explicitly forbid inventing function/variable names — including telling the hypothesis/fix agents to say "evidence insufficient" rather than fabricate a plausible-sounding answer. Re-tested: the retrieval agent listed the real directory, read `main.py` correctly on the first attempt, and the fix-proposal's diff correctly referenced the real `db.apply_order` function and correctly diagnosed the actual root cause (order applied, balance never re-validated afterward) — still not 100% verbatim (invented one non-existent helper, `db.get_user_balance`), but a genuine, substantively correct diagnosis rather than hallucinated filler. This is the expected ceiling for a cheap mini-model with a small reasoning budget — usable as a real v1, not pretending to be perfect.

**Then consolidated 3 agents down to 2.** Worth being honest about a tradeoff here: 3 sequential agents (retrieval, hypothesis, fix-proposal) meant 3 LLM round-trips, compounding token cost (each agent's prompt includes the full previous agent's output), and — concretely — was the actual root cause of the bug above: the retrieval→hypothesis hand-off was the seam where a bad retrieval silently corrupted downstream reasoning, with no way for hypothesis to know retrieval had failed. The hypothesis→fix-proposal hand-off never caused a problem; diagnosing and proposing a fix are naturally sequential, tightly coupled steps. "Investigation" (find the code + figure out why) is naturally one continuous train of thought for a human engineer too — splitting it bought nothing and cost a real bug.

Merged retrieval + hypothesis into one `investigator_agent` (both tools, one task: list files, read the relevant one(s), state root cause + confidence in the same pass). Kept `fix_agent` separate — that boundary is worth preserving even though it's "just" prompt structure, because it cleanly isolates the one agent explicitly forbidden from claiming a change was applied, matching the propose-only principle above. Re-tested after merging: fully grounded result this time, no invented names at all — quoted the real `await db.apply_order(...)` call and the real `log.error("negative_balance_detected", ...)` line verbatim. The proposed fix itself wasn't fully correct (it added a redundant balance re-check using an already-stale value, which doesn't actually close the TOCTOU gap — a real concurrency-correct fix would need locking, which is exactly the kind of subtlety a human reviewer should catch, not something to auto-apply) — but the grounding problem that mattered is solved.

General lesson, not just specific to this project: don't add agent separation by default. Split a step into its own agent only when there's a concrete reason — a genuinely different toolset/data source, a need for independent/skeptical review (a critic checking another agent's own work), or a step that's actually multi-step/branching on its own (e.g. git-based multi-file tracing, planned for the next iteration, which may justify re-introducing a dedicated retrieval agent once it's no longer a 3-file lookup).

### Propose-only, never auto-apply

The fix-proposal agent's backstory and task explicitly state it is not authorized to apply changes — it drafts a diff and explanation for a human to review. This mirrors how real tools (Sentry AI, Copilot) draw this line: autonomous, unreviewed changes to running code/data is a known risk, not just caution for its own sake. If genuine autonomous *action* is wanted later, the safer scope for that is reversible infra remediation (auto-restart a crashed container, bump a pool size) — not auto-patching application code. Not built; flagged as a deliberate non-decision.

### Decoupled from the log-watching loop

`log_collector.py` is a single synchronous polling loop. Calling the CrewAI pipeline directly from `handle_error()` would block it for however long the three chained LLM calls take — any incident arriving during that window wouldn't be processed until the AI call finished. Instead, AI-worthy incidents go into a `queue.Queue()`; a background daemon thread (started once at startup) consumes it and runs the crew. `handle_error()` just does a non-blocking `queue.put()`.

### Code retrieval: a known, documented scale limitation

The retrieval agent reads `target_app`'s source via a Docker volume — `target_app/` is mounted read-only into `sentinel-agent` at `/app/target_app_src`. This is a deliberate toy-scale simplification, not a production pattern, and it's worth being explicit about why:

At real scale, mounting one shared agent's filesystem access into every other service's live container doesn't work — production containers are frequently source-stripped via multi-stage Docker builds (no source present to mount at all), and giving one observability agent live filesystem access into every team's running containers is a serious security/blast-radius problem. It violates this project's own "neither knows the other's internals" principle worse than the Docker-SDK-log-streaming approach rejected back in section 1 — that gave an agent access to container orchestration; this gives it access to source code directly.

The real pattern: fetch source via git/version-control APIs using the file path + line number from an error (decoupled from whatever's currently deployed), often backed by a pre-built searchable code index — the same idea as this project's own planned Week 3 vector memory, just applied to code instead of past incidents. **Planned follow-up:** switch the retrieval agent to git-based fetching in the next iteration; the volume mount is explicitly a placeholder, not the intended end state.

### Cost

Both input and output tokens are billed, separately. Three agents chained sequentially (`context=[earlier_task]`) means token usage compounds across the pipeline — later agents' inputs include earlier agents' full outputs. At this project's scale (a few thousand tokens per incident, incidents triggered manually/rarely) the cost is small, but it is real spend on a card, unlike using claude.ai or chatgpt.com's free chat tiers — the API is a separate, billed product.

---

### A pre-existing bug found while testing the cascade trigger: cascade detection never actually worked

Tried to manually trigger a confirmed cascade pattern (alternating `/external` and `/users/99` three times each) to verify the third AI trigger. It never confirmed — `cascade_counts` stayed completely empty no matter how many times the sequence ran. Root cause, in `error_detector.py`'s `process_error`: `self.last_error = error_event` was being set **before** `_handle_immediate`/`_handle_threshold` (which call `_detect_cascade`) ran. By the time `_detect_cascade` checked `self.last_error.event != current.event`, `self.last_error` had already been overwritten to equal the current event — so the comparison could never be true. **Cascade detection has been silently broken since Week 1** — present in the original design, never caught because nothing had tested it against a real alternating sequence until this session.

Fixed by moving the `self.last_error` update to after dispatch, so cascade detection compares against the *previous* error, not itself. Verified two ways: a deterministic standalone simulation of the exact sequence (confirms on the 3rd repetition as designed), and live against the running containers (`Cascade: external_api_timeout → user_not_found` printed, correctly queued for AI).

This is also the second real, previously-unknown bug found purely by testing rather than by reading the code (the first was `/external`'s unhandled `JSONDecodeError`, below) — both were "passed review" in the sense that the code looked reasonable on inspection, but neither had ever actually been exercised end-to-end before this session.

### A second pre-existing bug found in the same session: `/external` could fail silently with zero logging

While trying to trigger the cascade above, `/external` returned a raw, unlogged 500 instead of the expected `external_api_timeout`. Cause: `httpbin.org/delay/5` (a third-party service this project doesn't control) sometimes returns a fast, non-JSON/empty response instead of timing out — `response.json()` then throws an unhandled `JSONDecodeError`, which had no `except` clause and therefore never reached `log.error(...)` at all. SentinelAI's entire pipeline was blind to this failure mode — not misclassified, genuinely invisible.

Fixed with a broader `except Exception` clause logging a new `external_api_error` event (classified `threshold`, not AI-worthy — the exception message already names the failure clearly, same reasoning as `db_connection_error`). This is a good real-world lesson distinct from the cascade bug above: real external dependencies fail in ways the original code didn't anticipate, and "wrap every external call in error handling that actually logs, even for exceptions you didn't expect" is a genuine production lesson, not boilerplate paranoia.

### Prompting improvements: addressing shallow reasoning

First few live runs produced correct-but-shallow analysis — mostly restating the error message rather than naming a precise mechanism. Two changes to `_build_incident_summary` (`log_collector.py`) and the investigator task (`ai_engine.py`):
1. Added `APP_CONTEXT` — a short, factual description of the app's actual architecture (FastAPI + Postgres, what tables exist, that `create_order` reads then separately writes via `db.apply_order`) so the model isn't reasoning about a financial bug with zero domain framing. Deliberately phrased as a neutral architectural fact ("check whether this holds up under concurrent requests") rather than stating the conclusion outright — telling the model the bug already exists would make the demo hollow.
2. Told the investigator agent explicitly to follow cross-file function calls (e.g. if `main.py` calls `db.something(...)`, read `db.py` too) rather than stopping at the first file — the original prompt let it conclude from a single file even when the real mechanism spanned two.

### Expanded AI_WORTHY_EVENTS: external_api_timeout and external_api_error

Original classification excluded these as "self-explanatory" — wrong, on reflection. There's a real gap (the log only says "no response in 3s," not why), it just can't be closed by reading our own code *about the third party* (we have no visibility into `httpbin.org`'s internals). But there's a different, real, code-discoverable gap: `main.py`'s `/external` endpoint calls a **5-second-delay endpoint with a 3-second client timeout** — guaranteed to fail by construction, not actually "flaky third-party dependency." That mismatch is visible by reading `main.py` alone; the log line never states it.

Added both events to `AI_WORTHY_EVENTS`, and extended `APP_CONTEXT` to point the investigator at "is our own usage/defensive coding adequate" rather than "speculate about the third party." Verified live: confidence 1.0, correctly identified the 3s-vs-5s mismatch, proposed raising the timeout to 6s — a genuinely correct fix, not just plausible-sounding, for an insight nothing in this project had surfaced before this conversation.

General lesson: "is the log line self-explanatory" isn't really the right test for AI-worthiness on its own — better test is "does *our own code* contain a discoverable insight beyond the proximate symptom," even when the ultimate external cause is unknowable. db_pool_exhausted/db_deadlock/db_connection_error remain excluded specifically because no comparable code-level insight exists *yet* (no hold-time instrumentation) — not because the question itself is unanswerable in principle.

### A global exception handler, because patching one endpoint at a time doesn't scale

The `/external` fix earlier in this doc patched one specific, already-discovered gap. The same *class* of bug — an exception nobody anticipated, falling through with no structured logging at all — could exist in any endpoint we haven't happened to break yet. Manually wrapping every endpoint in `try/except` only catches failure modes we already thought of, which is exactly the losing game described above.

Added `target_app/main.py`'s `catch_all_exceptions`, registered via `@app.exception_handler(Exception)`. Starlette only falls back to this when no more specific handler matches, so every existing `raise HTTPException(...)` and FastAPI's own request-validation handling keep working untouched — this only catches what nothing else caught. Logs a new `unhandled_exception` event with `error`, `error_type`, and `path`, classified `immediate` (consistent with "unknown errors default to immediate, over-alert is safer than missing one") and added to `AI_WORTHY_EVENTS` — arguably the most genuinely organic AI-worthy case of all, since by definition nothing pre-diagnosed it the way every other entry in that set was deliberately engineered or anticipated.

Verified with a temporary test endpoint (`return 1/0`, removed after verifying): confirmed the structured log line fires correctly (`error_type: ZeroDivisionError`, real path, real message), `sentinel-agent` detects and classifies it correctly as an immediate incident, and it gets queued for AI. The actual AI call failed in this test run due to an invalid OpenAI key (unrelated account issue, not a bug in this mechanism) — everything up to and including the AI hand-off was confirmed working.

### A real bug found in the first live AI test after rotating the OpenAI key: cross-incident contamination from `APP_CONTEXT`

First test with a working key, triggering `unhandled_exception` via the test endpoint: the investigation completely misdiagnosed it, describing the `negative_balance_detected` race condition from an earlier incident instead of the actual crash that fired. Root cause: `APP_CONTEXT` was one static string containing hints for *every* incident type, sent unconditionally on *every* call regardless of which event actually triggered it. The longest, most detailed hint (the balance race condition) anchored the model even when investigating something unrelated.

**A second, sharper issue surfaced while fixing the first one** — caught by direct pushback, not by testing: my first fix kept an architecture-specific hint for `negative_balance_detected` ("`create_order` reads balance then separately calls `db.apply_order`, check for races") while only making the *other* hints generic. That's not investigation guidance, it's handing over the conclusion — it only exists because we already know where the bug is. The other two hints (`unhandled_exception`: "use the path field to find the actual function"; `external_api_*`: "focus on our own usage, not the third party") are genuinely transferable technique that would apply to any app. Removed the `negative_balance_detected` hint entirely rather than rewriting it — it should be findable (or not) through the investigator's own code-reading, the same as a real unknown bug would have to be.

Fixed both issues: `EVENT_SPECIFIC_CONTEXT` now selects only the hint matching the actual triggering event (fixes contamination), and contains only general methodology, never architecture or conclusions (fixes the hand-holding). A follow-up precision pass was also needed: even with the corrected `unhandled_exception` hint, the investigator initially matched on exception *type* alone (found a different, also-real `ZeroDivisionError` elsewhere in the codebase) rather than the specific `path` field — tightened the hint to explicitly require matching the literal route decorator, not just the exception type.

**Verified both fixes hold:** re-triggered `unhandled_exception` — correctly identified the exact endpoint and function this time, confidence 1.0. Re-triggered `negative_balance_detected` with its hint completely removed — found the race condition unaided anyway, confidence 0.95, by reading an actual code comment in `db.py` (not anything from the prompt) describing the intentional race. Confirms the hint was never load-bearing for diagnosis quality — only for occasionally drifting into hand-holding territory.

---

## 4. Traffic Simulator

**Goal:** generate genuine concurrent load against `target_app` to produce *emergent* failures (real pool contention, real race conditions) instead of hand-coded ones — directly addressing an earlier honest critique: most of this project's failure modes either are deterministic business-rule checks or were hand-triggered one request at a time. The only previously-organic exception was `negative_balance_detected`, manually fired via two concurrent `curl` calls.

### Deliberate stress generator, not realistic traffic

Two genuinely different designs were on the table: realistic everyday traffic (modest rate, mostly valid requests) vs. a deliberate stress/contention generator (concurrent bursts targeting the same resources). Chose the latter — at this app's scale (3 users, a 5-connection pool), realistic-rate traffic would almost never create real contention. The whole point was making `db_pool_exhausted` and similar contention bugs finally reproducible, which only deliberate stress achieves.

### Sustained pressure, not a burst — the actual lesson from the earlier manual attempt

An earlier manual test fired 300 `curl` requests at once and got all `200`s — single-row indexed queries finish in microseconds, so even 300 requests cycle through a 5-connection pool faster than the pool's acquire timeout could ever be hit. The fix isn't more requests in one burst, it's *sustained* concurrent pressure over time. `traffic_simulator/simulate.py` runs a configurable number of independent workers (default 50), each looping continuously — fire a request, immediately fire the next — for a configurable duration (default 30s), rather than firing a fixed batch and stopping.

### Plain Python + `httpx`/`asyncio`, not Locust/k6

Same reasoning as `asyncpg`-over-ORM and hand-rolled-vs-CrewAI earlier in this doc: a load-testing tool like Locust is something you configure, not something you engineered — weaker to explain in an interview than "I wrote a concurrency generator using `asyncio.gather`." Also avoids a new runtime dependency; `httpx` is already used elsewhere in this project.

### Docker-compose profile, not a service that starts automatically

`docker compose up` must keep working exactly as before — starting the simulator automatically would mean constant background load every time you just want to poke at one endpoint manually. Added as a 4th service gated behind `profiles: ["stress"]`, so it's containerized (same Docker network, reaches `target-app` by service name) but strictly opt-in: `docker compose --profile stress up traffic-simulator` runs it on demand.

### Result: real wins, one honest gap

First real run: 50 workers, 30 seconds, weighted toward `/users/{id}`, `/orders`, `/analytics` (the pool-touching endpoints) — **33,589 requests in 30 seconds.**

**Genuine organic failures, for the first time without manual engineering:**
- `negative_balance_detected` fired **twice**, purely from real concurrent traffic — the race condition emerged on its own this time, not from a deliberately-timed pair of `curl` calls.
- `analytics_failed` fired at real volume (1,733 times) under genuine sustained load.
- `target-app` survived the entire run without crashing or restarting.

**The gap: `db_pool_exhausted` still didn't fire**, even under this much heavier load. Traced why: `item_a`/`item_c`'s small seeded stock depleted almost immediately, so most of the 30 seconds was spent on fast, no-write `insufficient_stock` rejections rather than sustained writes — but even the ~17,000 sustained *read* requests (`get_user`/`analytics`, which always touch the pool regardless of stock) weren't enough, implying single-row indexed queries are simply too fast relative to the 3-second acquire timeout to exhaust a 5-connection pool through query volume alone, even at ~570 req/s sustained. The 5,358 `ConnectError`s the simulator's own client saw appear to be network/Uvicorn-level saturation (no tracebacks, no pool-related log lines, no crash) — a different bottleneck than the actual `asyncpg` pool.

**Decision: documented as still-theoretical at this query speed/pool size, not artificially forced.** The simulator's actual goal — proving real concurrency produces real, unscripted failures rather than every failure mode being hand-triggered — is already demonstrated by `negative_balance_detected` firing organically. Forcing `db_pool_exhausted` to reproduce (e.g. via an artificial query delay) would be the same hollow-feature pattern this project has repeatedly pushed back on elsewhere. Revisit only if a future feature naturally introduces slower queries (e.g. the deferred `target_app` complexity expansion).

---

## 5. Redis — 24-Hour Incident History

**Goal:** answer long-horizon pattern questions ("has this happened before today," "how many times this week") that the in-memory sliding window in `error_detector.py` can't — it forgets everything after `WINDOW_SECONDS=60`. Not yet wired into the AI engine's context; validated standalone first, per this project's usual one-piece-at-a-time sequencing (same reasoning as Docker-before-Postgres, Postgres-before-the-AI-engine).

### Originally proposed combined with git-based retrieval; deliberately un-bundled

The idea to feed the AI engine both real git history *and* broader Redis-sourced log history together is a good eventual picture, but building both at once would mean if something went wrong, there'd be no way to tell which piece caused it. Git-based retrieval was separately deferred to the final iteration (see below) — its value depends on genuine multi-author commit history, which `target_app` doesn't have since its entire history is us building it incrementally this week. Real companies grant this kind of access via API-scoped GitHub/GitLab tokens correlating deploys with incidents, not raw `.git` filesystem mounting — reinforcing that this is better revisited once there's an actual separate repo to point at (the same final-iteration plan as validating against a real external app), not solved as a monorepo workaround now.

### Incidents only, not raw log lines

Same "filter down to high-signal data" philosophy this whole pipeline already uses. The ring buffer/sliding window already handle short-window, real-time detection in memory; Redis's only job is the long horizon nothing else covers. Storing every raw log line for 24h was considered and rejected — the traffic simulator alone produces 30,000+ lines per 30-second burst, which would mean storing hundreds of thousands of lines per day for uncertain payoff. Confirmed `Incident` objects (already filtered by the two-mode classifier) stay small and tractable even under heavy load.

### Native TTL + sorted-set index, not a cleanup job

Each incident gets its own key with a 24h TTL (`SETEX`) — Redis expires it automatically, no background cleanup process needed. A per-event-type sorted set (`ZADD`, timestamp as score) indexes incidents for efficient time-range queries (`ZRANGEBYSCORE`/`ZCOUNT`); since sorted-set members don't expire on their own the way `SETEX` keys do, the index is trimmed (`ZREMRANGEBYSCORE`) on every write to stay in sync. This is a standard, idiomatic Redis pattern — the same primitives used for rate limiters and "recent activity" feeds, not a stretch or misuse of what Redis is "supposed to be for."

**Deliberately scoped to long-horizon queries only, not short windows.** A "how many in the last 5 minutes" query is mechanically just as easy with this same sorted set, but it would duplicate `error_detector.py`'s existing in-memory sliding window for no new capability — kept the line clear: Redis answers "today/this week," the in-memory window answers "right now."

### Failure isolation

Writing to Redis is wrapped in its own `try/except` in `handle_error()` — if Redis is ever unavailable, real-time detection and alerting keep working exactly as before; only the long-horizon history silently stops accumulating. Redis is additive infrastructure, not a dependency the core pipeline should ever be blocked by.

### Verified standalone

Triggered real incidents, confirmed via `redis-cli` directly: the key exists with the correct JSON record, TTL is ~86375s (correctly close to the full 24h), and the sorted-set index has the matching entry. Confirmed `count_in_window()` returns the correct count against real data, and returns 0 for an impossibly short window — basic sanity check that the time math is right.

### Connected to the AI engine as a tool, not injected context

Originally planned to inject frequency data ("this occurred N times in the last 24h") into every AI-worthy incident's prompt unconditionally, the same way `APP_CONTEXT` works. Reconsidered after a direct suggestion: give the investigator a **tool** (`get_incident_history`) instead, exactly like `list_source_files`/`read_source_file` — let the model decide *when* asking about frequency would actually help, rather than force-feeding it into every prompt regardless of relevance. More consistent with how the investigator already works, and avoids prompt bloat for incidents where recurrence doesn't end up mattering.

Worth being precise about why this is safe and doesn't repeat the earlier hand-holding mistake (the removed `negative_balance_detected` architecture hint): frequency data is **objective fact** ("this happened 3 times"), not a **conclusion** about *why* — the model still has to do its own reasoning about what the frequency means. Giving it real data to reason over is the right kind of context; giving it our own conclusion is not. The tool takes an optional `hours` parameter (default 24, the maximum retained) rather than being hardcoded, so the model can ask about a shorter window if relevant too.

**A real architectural question surfaced while building this, worth recording:** does Redis replace the in-memory sliding window (`error_detector.py`'s `WINDOW_SECONDS=60`)? No — they're different layers. The in-memory window *is* the real-time detector: it decides, synchronously and in-process, whether a threshold-class error should escalate to a confirmed incident, on every single log line. Redis only stores incidents *after* that decision is already made — it never participates in detection itself, only in retrospective queries the AI engine makes optionally. Collapsing them would mean every log line needs a network round-trip just to decide whether to escalate, and would make core real-time detection depend on Redis being up — directly contradicting the failure-isolation design (`handle_error()`'s Redis write is already wrapped in its own `try/except` specifically so Redis going down never breaks real-time alerting).

**Verified:** rebuilt, triggered `analytics_failed` to its critical threshold, confirmed the investigator correctly chose *not* to call the new tool (confidence was already 1.0 from the code alone — correct judgment that frequency data wouldn't add anything for a fully deterministic bug). Directly tested the tool itself independent of whether the model uses it: correct counts for both the default 24h window and a custom 1h window, correct 0 for an event that hasn't fired.

---

## 6. Background Worker — Silent Async Failures

**Goal:** model a genuinely realistic, common production failure class that nothing in this project produced before: an operation that *looks* successful to the client, but a fire-and-forget side effect silently fails afterward, with no error response, no failed request anywhere — invisible unless something is specifically watching for it.

### A real second service, not an in-process function

`fake_email_service` is a tiny, separate FastAPI app (its own Docker service, `/send` endpoint, ~40% random `500`s) that `target_app`'s background task calls over the network. Considered making this just an in-process Python function instead (simpler today) — rejected, because the very next planned feature (multi-service cascades) needs a real second service anyway; building it as an in-process function now would mean rebuilding it as a real service later for no reason. The random failure rate is fully controlled and predictable, unlike `/external`'s dependency on `httpbin.org`'s actual real-world behavior (which we'd already found to be unpredictable in ways we couldn't fully explain).

**The failure rate had to be genuinely intermittent, not guaranteed.** First instinct was to reuse `/external`'s exact target (a 5-second delay against a 3-second timeout) for the background task too — caught before building: that's rigged to fail 100% of the time, which isn't realistic and isn't interesting to detect (a permanently-broken task isn't "silent," it's just obviously broken). `fake_email_service`'s ~40% random failure rate is intermittent and believable instead.

### The actual mechanism: `asyncio.create_task` + a `done_callback`

`create_order` fires `send_order_confirmation()` via `asyncio.create_task()` right after returning success — the client's response goes back regardless of what happens next. Without anything else, a failure here is genuinely lost: Python prints at most a quiet `"Task exception was never retrieved"` warning whenever the task object happens to get garbage-collected, easily missed in a busy log stream.

A `done_callback` (`_on_background_task_done`) is the entire fix — it checks `task.exception()` when the task finishes and logs `background_task_failed` if there was one. This is a standard, real-world technique (not invented for this project) for preventing total silence; it's still genuinely "silent" in the sense that matters here because the log line appears *after* the response already went out, disconnected in time from the request that caused it — verified directly: a `background_task_failed` line appeared interleaved with two *other, unrelated* orders' success logs, reporting on a task that had been fired several hundred milliseconds earlier.

A module-level `_background_tasks` set holds a reference to each task until it finishes — `asyncio`'s own docs warn that a task with no surviving reference can be garbage-collected mid-execution, which would silently cancel it before the callback ever runs.

### Chose `asyncio.create_task` over FastAPI's `BackgroundTasks`

FastAPI has a built-in `BackgroundTasks` mechanism for exactly this use case. Didn't use it specifically because its exception-propagation behavior on failure isn't something to rely on with full confidence without verifying Starlette's exact internals — using raw `asyncio.create_task()` with an explicit, self-written `done_callback` gives full, certain control over exactly what happens on failure, rather than depending on framework behavior that wasn't independently confirmed.

### AI-worthy, same category as `external_api_timeout`

The proximate cause (`fake_email_service` returned 500) is visible in the log; the real question — *does our own code retry before giving up?* — isn't, and is discoverable by reading `main.py`. Same "investigate our own usage, not the dependency's internals" framing as `external_api_timeout`/`external_api_error`. Deliberately did **not** mount `fake_email_service`'s source into the investigator — even though we wrote it ourselves, treating it as a black box is more realistic (a real email provider wouldn't let you read its source either) and keeps the investigator's scope consistent with the existing `target_app/`-only mount.

**Verified end-to-end:** real second service, real background task, real intermittent async failure, correctly detected as an immediate incident, correctly classified as AI-worthy, and the investigator correctly diagnosed the actual gap (confidence 0.85: "no retry logic, failure is logged but no corrective action taken") rather than speculating about `fake_email_service`'s internals. The fix proposal added a 3-attempt retry loop — right concept, with a minor structural rough edge in the literal diff (same known ceiling as every other fix-proposal this project has produced) — exactly why this stays propose-only for human review.

### A second, structurally different background worker: periodic health monitoring

Considered a second background-worker example with the same shape as the first (another fire-and-forget task calling something) — rejected as redundant padding, same category of "more of the same" this project has pushed back on elsewhere. The two genuinely distinct shapes worth having: a *scheduled* job (runs on its own clock, not triggered by any request) and a worker that *hangs* instead of failing (a resource-leak shape needing metrics, not log events — deferred, same reasoning as the earlier memory-leak/CPU-spike deferral). Built the scheduled one.

**First instinct for what the scheduled job should check — re-detecting `negative_balance_detected` periodically — was rejected as redundant** ("I don't think negative balance is a good reason to have something triggering every 60sec"): that bug is already caught synchronously in `create_order`; a second detection mechanism for the same thing isn't new coverage. Landed on a genuinely independent purpose instead: **periodically health-checking `fake_email_service`** — a real, common monitoring pattern (active dependency health-checking), not redundant with anything else, and it sets up the very next planned feature (multi-service cascades) directly.

`monitor_email_service_health` (started via `lifespan`, same pattern as `db.init_pool()`) polls **`/send`, not `/health`**, every 5 seconds. This matters: `/health` always returns `200` by design (Docker's own healthcheck depends on it staying trustworthy), so polling it would never detect anything. `/send` is the real ~40% failure endpoint. Tracks **consecutive** failures (not a count over time) — 3 in a row is treated as a confirmed sustained outage rather than ordinary per-call randomness (1-in-3 chance by pure chance is ~6.4%, rare enough to mean something when it happens, common enough to occur naturally during normal testing without forcing it). Logs `email_service_unreachable` and resets the counter, so a *later*, separate outage can trigger a fresh alert rather than this staying permanently fired on the first one.

**A real, honest gap, left deliberately:** individual failed health-checks (1 or 2 in a row, never reaching 3) produce zero log output at all — not even at info level. Confirmed this is acceptable as-is rather than something to fix; the monitor's job is "is there a sustained outage," not "log every individual check."

**AI-worthy for a genuinely different reason than `background_task_failed`.** Not about retry logic on one call — about **coordination**: `monitor_email_service_health` already knows the service is in a confirmed outage, but nothing connects that knowledge to `create_order`, which keeps firing background tasks at the dependency regardless. The missing circuit breaker between two pieces of code that both exist but don't talk to each other. **Confirmed this gap is real and deliberately left in, not yet built** — raised directly mid-session ("but I thought this checks whether the email service is working and then sends, right?"); clarified the two systems are genuinely disconnected today, and the smarter coordinated version stays unbuilt on purpose, so it remains a real, discoverable gap for the AI to find rather than something already half-fixed.

### Per-event AI context hints consolidated, on direct pushback about scaling

Building `background_task_failed` and `email_service_unreachable` meant writing two more bespoke `EVENT_SPECIFIC_CONTEXT` entries, bringing the total to four near-identical "you're calling a dependency you don't control, focus on our own usage" hints (`external_api_timeout`, `external_api_error`, `background_task_failed`, `email_service_unreachable`). Flagged directly: *"the AI should be able to find without so much hints... so specific is not good"* — a real, valid concern. Writing a new bespoke hint every time a new dependency-touching feature gets added doesn't scale, and the trend (most AI-worthy events now have a specific hint) was undermining the "general investigation" framing this project has tried to hold to.

**Fix:** collapsed all four into one shared `_DEPENDENCY_HINT` constant applying to the whole category, rather than one bespoke entry per event. `unhandled_exception` kept its own distinct hint — it's genuinely different in kind (matching a route path, not about dependencies).

**Tested the trade-off honestly instead of assuming it was free**, same rigor as testing whether `negative_balance_detected`'s hint was load-bearing. Result: confidence dropped slightly (0.8 vs 0.85), and the model now lands on different valid angles run-to-run (retry logic, redundant logging, "no user feedback") rather than consistently the same sharp insight every time. Real cost, accepted as the right trade for not needing a bespoke hint for every future dependency-touching feature.

### Confidence score: not a calculated metric

Surfaced directly: *"how are you calculating the confidence score?"* Honest answer — it isn't calculated at all. The investigator task just instructs the model to "state your confidence (0-1)"; the number is pure LLM self-report, no formula, no verification step, no calibration against anything. Confirmed inconsistent for similar underlying diagnoses across runs (0.85 vs 0.8 seen directly in this session). Real calibration would need outcome-tracking data (was a past diagnosis actually verified correct) to measure against — the same prerequisite already identified for the learned classifier and Week 3 benchmarking. Decision: don't half-fix this now; fold "calibrate confidence using verified outcomes" into the vector memory / outcome-tracking work later, since all three depend on the same missing piece.

---

## 7. Multi-Service Cascades

**Goal:** model a genuine failure cascade — one dependency's slowness propagating into degraded behavior for *unrelated* requests — using real measured evidence rather than asserting it. `fake_email_service` (built for the background-worker feature) already gave this project a real second service; this adds a third, `fake_payment_service`, and the actual cascade mechanism.

### Why payment, and why slowness instead of failure

`fake_payment_service`'s `/charge` is called **synchronously** inside `create_order`, unlike the fire-and-forget email confirmation — a deliberate, realistic distinction: payment genuinely must complete before an order can be confirmed, while a confirmation email legitimately can't block that. Its failure mode is **occasional slowness** (~20% chance of an 8-second delay before responding, not a fast `500` like `fake_email_service`) — slowness, not errors, is what actually ties up resources across many concurrent requests; a fast failure doesn't hold anything open long enough to cause pressure elsewhere.

### First hypothesis disproven by direct measurement, not assumed

Original hypothesis: many concurrent slow payment calls would tie up `target_app`'s own request-handling capacity, degrading unrelated endpoints. Tested directly rather than assumed: fired 15 concurrent orders (exploiting the existing balance/stock race condition to get many of them reading pre-write state simultaneously, the same mechanism behind `negative_balance_detected`), timed `/users/1` mid-burst. **No degradation at all** (14ms baseline vs 21ms mid-burst, then 14ms again testing more carefully) even with several orders genuinely hitting the 8-second delay simultaneously.

**The real, technically substantive reason why:** in a well-built async Python app, `await client.post(...)` waiting on a slow response consumes zero CPU and holds nothing scarce — the event loop is completely free to service other concurrent requests at full speed in the meantime. That's one of async's core strengths, and it means "many concurrent slow I/O waits" does not, by itself, cascade into anything in this architecture. The original hypothesis was wrong, and rather than forcing a fake demonstration, this was confirmed and the actual mechanism rebuilt around it.

### The real mechanism: hold the one genuinely scarce resource during the wait

The Postgres pool (`max_size=5`) is the one truly limited resource in this system — unlike event-loop capacity (effectively unlimited for I/O-bound waits), there are only 5 connections, ever. `db.hold_connection()` (a thin public wrapper around the existing internal `_connection()` helper) lets `create_order` check out a connection and hold it for the *entire* duration of the payment call, not just for its own DB queries. This makes pool exhaustion a genuine, reachable consequence of payment slowness: enough concurrent slow charges holding connections simultaneously can exhaust the pool for *any* other request needing one, including completely unrelated ones.

**Verified for real, with a fresh seed and increasing concurrency until the effect appeared:** 15 concurrent orders — no effect (not enough simultaneous slow holds to exceed 5). 40 concurrent orders — **`/users/1`, with zero connection to payments or orders, returned a real `503 Database pool exhausted` after waiting the full 3-second acquire timeout.** 32 separate `db_pool_exhausted` events fired from that single burst.

**This is also the first time `db_pool_exhausted` has fired for real anywhere in this project.** Every earlier attempt (manual bursts, the traffic simulator's sustained load) failed to reproduce it, because single-row indexed queries are too fast relative to the 3-second timeout to ever exhaust 5 connections through query volume alone — documented as a known, accepted gap at the time. Holding a connection open for 8 seconds (instead of microseconds) is what finally made it reachable.

### Checked, not assumed: did this fire via AI_WORTHY_EVENTS or a confirmed cascade?

Saw AI analysis running during this test and initially assumed it was triggered by a confirmed cascade pattern (`db_pool_exhausted` co-occurring with something else) — checked the actual incident output rather than trusting that assumption, and it was wrong. The AI invocations were `background_task_failed` and `email_service_unreachable` (already independently AI-worthy from the background-worker feature), incidentally co-occurring because the same 40-order burst also stressed the email-confirmation path concurrently — not because any cascade pattern was confirmed. **`db_pool_exhausted` itself, despite firing 32 times and reaching critical severity repeatedly, correctly never triggered AI** — `Decision 3c` (left non-AI pending real hold-time instrumentation) held up exactly as designed, even under a genuine, severe real-world trigger of the event it governs.

---

## 8. AI Dispatch Cooldown — Deduplicating Repeated Identical Incidents

**The gap, found live while testing the multi-service cascade:** `error_detector.py` had no deduplication for AI dispatch. Once a threshold-class error crossed `INCIDENT_THRESHOLD`, *every single subsequent occurrence* within the window — not just the first — produced its own separate `requires_ai=True` incident. Immediate-class events had the same problem: every occurrence is its own incident with no grouping. Confirmed directly, twice: a small negative-balance test burst produced 2 separate `negative_balance_detected` + 4 separate `background_task_failed` AI calls from a few seconds of activity, all diagnosing the same root cause; the 32-occurrence `db_pool_exhausted` cascade burst would have meant roughly 28 AI calls had that event been AI-worthy.

**Fix: a per-key cooldown gating AI dispatch, not detection.** `ErrorDetector.last_ai_call` tracks the wall-time of the last *actual* AI dispatch, keyed by event name — or by the confirmed cascade pattern string, when one exists, since two different cascades are genuinely different incidents and shouldn't share a cooldown with each other or with the individual event names involved. `_should_call_ai()` checks this before setting `requires_ai=True`; if the same key was dispatched within `AI_COOLDOWN_SECONDS` (120s — double the existing 60s detection window, long enough that a single burst can't re-trigger itself, short enough that a genuinely new occurrence later still gets analyzed promptly), it's suppressed instead. Critically, the cooldown timestamp is only updated on an *actual* dispatch, not on every suppressed attempt — so the next occurrence after the cooldown expires triggers AI again immediately, rather than the cooldown perpetually resetting itself from suppressed attempts.

**Detection stays completely untouched.** Every occurrence is still logged, still counted, still shown in the terminal incident output exactly as before — only the "send this to OpenAI" decision is gated. `Incident` gained a new `ai_worthy` field, separate from `requires_ai`, specifically so callers can tell "never AI-worthy" apart from "AI-worthy, but suppressed by cooldown this time" — `log_collector.py` now prints `🤖 AI-worthy, but skipped — already analyzed recently (cooldown)` for the latter case instead of just silently omitting the AI line either way, same transparency principle as every other detector stat.

**A real bug caught by testing, not by inspection.** Verification was done with a standalone script directly exercising `ErrorDetector`'s internals with hand-built `ErrorEvent` objects — explicitly confirmed beforehand this makes zero API calls, pure local logic only, before running it. First run revealed: the very first-ever occurrence of an event was being incorrectly suppressed. Cause: `last_ai_call.get(key, 0)` used `0` as a "never called before" sentinel — with real `time.time()` epoch values (~1.7 billion), `wall_time - 0` is always far larger than the cooldown, so this coincidentally never breaks in production, but it's fragile by construction and the test's small, deliberately legible synthetic timestamps (0, 5, 200) exposed it immediately. Fixed by using `None` as an explicit sentinel instead of relying on numeric magnitude. Re-verified after the fix: first occurrence dispatches, a repeat within the cooldown is suppressed and counted, the next occurrence after the cooldown expires dispatches again, and the threshold path behaves identically to the immediate path.

**This was deliberately built standalone, not bundled with the previously-discussed learned classifier idea.** They solve different problems (dispatch throttling vs. learning classification from outcome data) and the classifier remains blocked on outcome-tracking data that doesn't exist yet — bundling them would have meant this small, ready-to-ship fix waiting on that much larger, still-blocked piece of work for no real benefit.

---

## 9. Vector Memory — Long-Term Semantic Incident Recall

**The goal, and how it's genuinely different from Redis.** `redis_store.py` already answers "how many times has this *exact* event happened in the last 24 hours" — exact match, short window, expires by design. Vector memory answers a different question: "has something *like* this happened before, ever, even if it was a different event type" — similarity match, no expiry, the entire point being accumulated knowledge over time rather than a deliberately short memory.

**Architecture: Chroma as its own Docker service, not reused Postgres infrastructure.** First instinct was `pgvector` on the existing Postgres instance — caught immediately as a real architectural mistake: Postgres belongs to `target_app` (the thing being monitored), and `sentinel-agent` reaching into it directly would break the same decoupling principle that already justified giving Redis its own separate store. Chroma runs as its own service (`chromadb/chroma`), same pattern as Redis/Postgres, with its own named volume.

**Embeddings computed locally, not via the OpenAI API.** `sentence-transformers` (`all-MiniLM-L6-v2`) runs entirely in the `sentinel-agent` container — zero marginal API cost no matter how many incidents get stored, deliberately independent of the AI-dispatch-cost-control work in section 8. Chroma itself is never given an embedding function and never computes one — `vector_memory.py` always supplies a precomputed embedding, keeping the "where does cost come from" story simple and auditable.

**What's embedded vs. what's stored are different things, on purpose.** The *incident summary* (the investigator's input) is what gets embedded — so a new incident is matched against what past incidents *looked like*. The investigator's *diagnosis* and the fix agent's *proposed fix* are what get stored as the retrievable payload — the actually useful thing to recall, not the summary that produced it.

**New tool, `get_similar_incidents`, available to the investigator alongside `get_incident_history`.** Unlike Redis's exact-event-name lookup, this does a genuine semantic similarity search — a past `external_api_timeout` incident could surface as relevant context for a new, differently-named incident if the underlying situation is similar. Framed explicitly in the tool description as precedent, not proof: "a similar-looking past incident isn't necessarily the same root cause this time... never a substitute for reading the code" — same discipline already applied to `get_incident_history`.

**Verified with zero API cost first, then with one real run.** Cold-start query against an empty collection returns `[]`, not an error — confirmed directly. Stored a synthetic incident, queried with a genuinely similar synthetic incident (distance 0.35) and a genuinely unrelated one (distance 1.6, same single stored item, much further away) — confirmed the embedding model is actually discriminating similarity, not just returning whatever exists. Then ran one real end-to-end AI test (`analytics_failed`, confidence 1.0): confirmed the real diagnosis and the real fix-proposal diff both came back correctly from `query_similar()` afterward.

**Storing the fix proposal, not just the diagnosis, was a direct improvement on the original plan, made when asked directly.** First version stored the diagnosis only; correctly challenged ("does it store the fix as well?") with the right instinct that the fix is arguably the more directly actionable thing to recall. `analyze_incident()`'s `result` (the crew's final output) is already the fix agent's text by construction (`Process.sequential` returns the last task's output) — no extra crew step needed, just passed it through to `store_incident()` too.

**A real, honest finding about Chroma's persistence guarantees, found by accident and then deliberately reproduced.** A real `analytics_failed` diagnosis stored during testing was lost after the whole stack got stopped (`docker compose down`) at almost the same moment a query was being attempted from a separate concurrent session. Investigated rather than dismissed: a controlled, sequential test (store, then cleanly restart) preserved data correctly across both a plain `docker compose restart` and a full `down`/`up` cycle — ruling out "any restart loses data." The likely real explanation is a narrow timing window: Chroma's client-server mode doesn't necessarily guarantee a write is durably flushed before acknowledging it, so a shutdown landing in that exact window can lose the most recent write. Documented as a known characteristic, not fixed — this is read-heavy, append-mostly long-term memory, not transactional state, so occasionally losing the very latest write under a true concurrent-shutdown race is an acceptable risk, not worth adding write-ahead durability machinery for at this scale.

**A separate, smaller oddity also observed and documented rather than chased further:** `collection.count()` returned `0` immediately after a successful real write that `query_similar()` could already retrieve correctly in the same script. Likely an eventual-consistency quirk between Chroma's count metadata and its vector index, not a real data-loss issue — the actual store-then-retrieve behavior was correct. Noted as a reason not to trust `count()` as an immediate post-write sanity check, without spending further time isolating Chroma's internal cause.
