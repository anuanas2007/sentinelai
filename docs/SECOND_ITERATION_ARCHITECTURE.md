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

*(Redis, traffic simulator, and AI reasoning engine sections to be added as each is built.)*
