import os
import asyncio
import asyncpg
import structlog

log = structlog.get_logger()

POOL_MIN_SIZE = 2
POOL_MAX_SIZE = 5
ACQUIRE_TIMEOUT = 3.0  # seconds — how long a request waits for a free connection

_pool: asyncpg.Pool | None = None


# Typed exceptions raised by this module only — main.py never imports
# asyncpg directly, so it doesn't need to know Postgres internals to
# decide what structured log event to emit.
class DBPoolExhausted(Exception):
    pass


class DBConnectionError(Exception):
    pass


class DBDeadlock(Exception):
    pass


class DBForeignKeyViolation(Exception):
    pass


async def init_pool():
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=os.environ["DATABASE_URL"],
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
    )


async def close_pool():
    if _pool is not None:
        await _pool.close()


async def _acquire():
    try:
        return await _pool.acquire(timeout=ACQUIRE_TIMEOUT)
    except asyncio.TimeoutError:
        raise DBPoolExhausted(f"No connection available within {ACQUIRE_TIMEOUT}s")
    except (OSError, asyncpg.PostgresConnectionError) as e:
        raise DBConnectionError(str(e))


async def get_user(user_id: int) -> dict | None:
    conn = await _acquire()
    try:
        row = await conn.fetchrow(
            "SELECT id, name, email, balance FROM users WHERE id = $1", user_id
        )
        return dict(row) if row else None
    finally:
        await _pool.release(conn)


async def get_item(item_name: str) -> dict | None:
    conn = await _acquire()
    try:
        row = await conn.fetchrow(
            "SELECT name, stock FROM items WHERE name = $1", item_name
        )
        return dict(row) if row else None
    finally:
        await _pool.release(conn)


async def count_users() -> int:
    conn = await _acquire()
    try:
        return await conn.fetchval("SELECT count(*) FROM users")
    finally:
        await _pool.release(conn)


async def total_inventory() -> int:
    conn = await _acquire()
    try:
        return await conn.fetchval("SELECT coalesce(sum(stock), 0) FROM items")
    finally:
        await _pool.release(conn)


async def apply_order(user_id: int, item_name: str, quantity: int, total_charged: float):
    """
    Deducts stock and balance, then records the order.

    The three statements below are wrapped in a transaction so this
    function itself can't partially apply (e.g. stock deducted but no
    order row, if the process crashes mid-way). That is NOT the same
    as the order being safe from race conditions — main.py reads the
    user's balance and the item's stock *before* calling this function,
    and that earlier read is never re-validated inside this same
    transaction. Two concurrent orders can both pass that earlier check
    and both land here, overdrawing the balance. This is intentional —
    see docs/SECOND_ITERATION_ARCHITECTURE.md for why.
    """
    conn = await _acquire()
    try:
        try:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE items SET stock = stock - $1 WHERE name = $2",
                    quantity, item_name,
                )
                await conn.execute(
                    "UPDATE users SET balance = balance - $1 WHERE id = $2",
                    total_charged, user_id,
                )
                await conn.execute(
                    """INSERT INTO orders (user_id, item_name, quantity, total_charged)
                       VALUES ($1, $2, $3, $4)""",
                    user_id, item_name, quantity, total_charged,
                )
        except asyncpg.exceptions.ForeignKeyViolationError as e:
            raise DBForeignKeyViolation(str(e))
        except asyncpg.exceptions.DeadlockDetectedError as e:
            raise DBDeadlock(str(e))
    finally:
        await _pool.release(conn)
