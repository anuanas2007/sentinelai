# SentinelAI — Error & Failure Mode Reference

> Practical reference: every error this app can currently produce, how to trigger it,
> and what SentinelAI does about it. For *why* things are built this way, see
> [FIRST_ITERATION_ARCHITECTURE.md](FIRST_ITERATION_ARCHITECTURE.md) and
> [SECOND_ITERATION_ARCHITECTURE.md](SECOND_ITERATION_ARCHITECTURE.md).

Two error classes (`agent/error_detector.py`):
- **immediate** — one occurrence is a real incident, alerted instantly.
- **threshold** — needs 3+ in 60s to warn, 5+ in 60s to escalate to critical (sliding window, not a fixed bucket — see below).

---

## `GET /users/{user_id}`

| Event | Class | HTTP | How to trigger |
|---|---|---|---|
| `user_not_found` | immediate | 404 | `curl localhost:8000/users/99` — any id not in the seeded users (1=Alice, 2=Bob, 3=Charlie) |
| `db_pool_exhausted` | threshold | 503 | Sustained concurrent load beyond the 5-connection pool, held longer than the 3s acquire timeout. **Not currently reproducible with a single burst** — queries are sub-millisecond, see SECOND_ITERATION_ARCHITECTURE.md. Will become reachable once the traffic simulator exists. |
| `db_connection_error` | threshold | 503 | Stop the postgres container while target-app is running: `docker compose stop postgres`, then hit any endpoint |

## `POST /orders`

Body: `{"user_id": int, "item": string, "quantity": int}`

| Event | Class | HTTP | How to trigger |
|---|---|---|---|
| `order_failed_user_not_found` | immediate | 404 | `user_id` not in seeded users, e.g. `user_id: 99` |
| `order_failed_insufficient_stock` | immediate | 400 | `item: "item_b"` (seeded with 0 stock), any quantity ≥ 1 |
| `order_failed_insufficient_balance` | immediate | 400 | `user_id: 2` (Bob, balance 0.00), any order — `total = 50 * quantity` will always exceed 0 |
| `order_failed_fk_violation` | immediate | 404 | Not currently reachable through normal use — would need the referenced user/item to be deleted between the pre-check and the write, and there's no delete endpoint yet. Handler exists defensively. |
| `db_deadlock` | threshold | 503 | Not currently reproducible — `apply_order` always locks `items` → `users` → `orders` in the same order on every call, so there's no circular wait to deadlock on. Documented gap, see SECOND_ITERATION_ARCHITECTURE.md. |
| `db_pool_exhausted` / `db_connection_error` | threshold | 503 | Same as above |

**Valid order** (no error): `user_id: 1` (Alice, balance 500), `item: "item_a"` (stock 10), `quantity: 1` → `{"status": "success", "total_charged": 50.0}`, Alice's balance persists at 450 in Postgres afterward.

## `GET /analytics`

| Event | Class | HTTP | How to trigger |
|---|---|---|---|
| `analytics_failed` | threshold | 500 | Random — fires on ~30% of calls regardless of input. Hit the endpoint repeatedly to land on it. |
| `db_pool_exhausted` / `db_connection_error` | threshold | 503 | Same as `/users/{id}` |

## `GET /external`

| Event | Class | HTTP | How to trigger |
|---|---|---|---|
| `external_api_timeout` | threshold | 503 | Always — every call hits `httpbin.org/delay/5` with a 3s client timeout, so it always times out. Hit it 5 times within 60s to escalate to critical. |

## `GET /health`

No failure modes. Always returns `{"status": "ok"}`.

---

## What SentinelAI actually does when one of these fires

- **Immediate-class event** → instant `🚨 IMMEDIATE INCIDENT` alert, AI analysis queued immediately.
- **Threshold-class event** → `⚠️ WARNING` on first occurrence, `🚨 CRITICAL INCIDENT` with AI analysis queued once 3+ in 60s window. Window decays continuously — old occurrences age out after 60s.
- **Cascade detection** — if two different error types occur within 30s of each other 3+ times, confirmed as a cascade pattern. AI receives both events' context and must give a verdict: `Cascade verdict: YES` (causal) or `Cascade verdict: NO` (independent).
- **AI pipeline** — investigator agent reads actual source code, states root cause + confidence. Fixer agent proposes a code diff for human review. Never auto-applies. Fix proposals can be rated correct / partial / incorrect.
- **AI cooldown** — 120s per event/cascade to prevent redundant calls from a single burst.

## DB reset utilities

If balance or stock is depleted and you want to repeat a scenario without restarting Docker, use the trigger panel's **Add balance** and **Add stock** buttons (or call the endpoints directly):

```bash
# Top up Alice's balance by $500
curl -X POST localhost:8000/admin/topup -H "Content-Type: application/json" -d '{"user_id": 1, "amount": 500}'

# Restock item_a by 100 units
curl -X POST localhost:8000/admin/restock -H "Content-Type: application/json" -d '{"item_name": "item_a", "quantity": 100}'
```

## Observability

- **Live UI** — `http://localhost:5173` — 4-column pipeline view (activity, detector, investigator, fixer)
- **Grafana** — `http://localhost:3000` — AI pipeline metrics dashboard (detection funnel, fix accuracy, pipeline latency, cascade analysis, vector memory hit rate)
- **Prometheus** — `http://localhost:9090` — raw metrics, scraped from `sentinel-agent:9000/metrics` every 15s
- **Agent logs** — `docker compose logs -f sentinel-agent`

## How to watch it happen

```bash
docker compose up --build -d
# Live UI at http://localhost:5173
# Grafana at http://localhost:3000 (admin / admin)
# Trigger scenarios from the UI's trigger panel
```
