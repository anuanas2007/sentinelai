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

- **Immediate-class event** → instant `🚨 IMMEDIATE INCIDENT` alert in `sentinel-agent`'s logs, on the very first occurrence, with the last 10 log lines as context and current detector stats.
- **Threshold-class event** → silent until 3 occurrences in the last rolling 60 seconds (`⚠️ WARNING`, no AI handoff), then `🚨 CRITICAL INCIDENT` (AI handoff flag set) once it crosses 5 in that same rolling window. The window decays continuously — old occurrences age out after 60s, so the count can drop back below threshold if errors stop.
- **Cascade detection** — if two *different* error types occur within 30 seconds of each other, 3+ times, SentinelAI flags it as a confirmed cascade pattern (e.g. `db_connection_error → order_failed_user_not_found` repeating) in the incident's `pattern` field.
- Right now, all of the above just prints to `sentinel-agent`'s container logs (`docker compose logs -f sentinel-agent`). No AI reasoning happens yet — `requires_ai: True` is set on the `Incident` object but nothing currently consumes it. That's the next major piece of Week 2.

## How to watch it happen

```bash
docker compose up --build -d
docker compose logs -f sentinel-agent   # watch this terminal
# in another terminal, trigger any of the events above
```
