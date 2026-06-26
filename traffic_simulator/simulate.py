"""
Traffic simulator -- deliberate stress/contention generator, not realistic
everyday traffic. The goal is to produce genuine pool exhaustion and other
contention-based failures by keeping sustained concurrent pressure on
target_app's database-touching endpoints, not by sending a one-off burst.

Why sustained, not a burst: an earlier manual test fired 300 requests at
once against target_app and got all 200s -- single-row queries finish in
microseconds, so even 300 requests cycle through a 5-connection pool
faster than the pool's acquire timeout could ever be hit. What actually
exhausts a small pool is many requests *continuously in flight* over a
period of time, not a large number fired and immediately done.

Each worker loops independently, firing one request, then immediately
firing the next, for the whole duration -- WORKERS controls how many of
these loops run concurrently at any instant.
"""
import asyncio
import os
import random
import time
import httpx

TARGET_BASE_URL = os.environ.get("TARGET_BASE_URL", "http://localhost:8000")
WORKERS = int(os.environ.get("WORKERS", "50"))
DURATION_SECONDS = int(os.environ.get("DURATION_SECONDS", "30"))

VALID_USER_IDS = [1, 2, 3]
INVALID_USER_IDS = [99, 100, 404]
ITEMS = ["item_a", "item_b", "item_c"]

# Weighted action list -- mostly endpoints that touch the connection pool,
# since contention is the actual goal here, not a realistic traffic mix.
ACTIONS = ["get_user", "get_user", "create_order", "create_order", "analytics"]


async def fire_one(client: httpx.AsyncClient, stats: dict):
    action = random.choice(ACTIONS)
    try:
        if action == "get_user":
            user_id = random.choice(VALID_USER_IDS + INVALID_USER_IDS)
            resp = await client.get(f"{TARGET_BASE_URL}/users/{user_id}")
        elif action == "create_order":
            resp = await client.post(
                f"{TARGET_BASE_URL}/orders",
                json={
                    "user_id": random.choice(VALID_USER_IDS),
                    "item": random.choice(ITEMS),
                    "quantity": random.randint(1, 10),
                },
            )
        else:
            resp = await client.get(f"{TARGET_BASE_URL}/analytics")
        stats[resp.status_code] = stats.get(resp.status_code, 0) + 1
    except httpx.RequestError as e:
        stats[f"request_error:{type(e).__name__}"] = stats.get(f"request_error:{type(e).__name__}", 0) + 1


async def worker(client: httpx.AsyncClient, stats: dict, stop_at: float):
    while time.monotonic() < stop_at:
        await fire_one(client, stats)


async def main():
    print(f"[traffic-simulator] target: {TARGET_BASE_URL}")
    print(f"[traffic-simulator] {WORKERS} concurrent workers for {DURATION_SECONDS}s")
    print("[traffic-simulator] sustained pressure, not a one-shot burst -- this is the point")

    stats: dict = {}
    stop_at = time.monotonic() + DURATION_SECONDS

    async with httpx.AsyncClient(timeout=10.0) as client:
        await asyncio.gather(*(worker(client, stats, stop_at) for _ in range(WORKERS)))

    total = sum(stats.values())
    print(f"\n[traffic-simulator] done -- {total} requests in {DURATION_SECONDS}s")
    for key, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {key}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
