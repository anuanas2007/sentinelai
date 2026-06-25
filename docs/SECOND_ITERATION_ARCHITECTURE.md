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

---

*(Postgres, Redis, traffic simulator, and AI reasoning engine sections to be added as each is built.)*
