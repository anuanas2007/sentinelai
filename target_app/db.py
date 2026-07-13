import os
import asyncio
from contextlib import asynccontextmanager
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


class DBUniqueViolation(Exception):
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


@asynccontextmanager
async def _connection():
    """Acquire-then-always-release, in one place instead of a try/finally per function."""
    conn = await _acquire()
    try:
        yield conn
    finally:
        await _pool.release(conn)


@asynccontextmanager
async def hold_connection():
    """
    Exposes a connection-pool checkout to callers outside this module.
    Not for normal queries -- those should use the query functions
    above. This exists specifically for operations that need to
    demonstrably hold a connection across a slow external call (see
    target_app/main.py's payment call) to make pool exhaustion a real,
    reachable cascade mechanism rather than something that only
    happens to fast in-process queries.
    """
    async with _connection() as conn:
        yield conn


async def create_user(name: str, email: str, balance: float = 200.0) -> dict:
    async with _connection() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO users (name, email, balance) VALUES ($1, $2, $3) RETURNING id, name, email, balance",
                name, email, balance,
            )
            return dict(row)
        except asyncpg.exceptions.UniqueViolationError:
            raise DBUniqueViolation(f"Email already in use: {email}")


async def get_user(user_id: int) -> dict | None:
    async with _connection() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, email, balance FROM users WHERE id = $1", user_id
        )
        return dict(row) if row else None


async def get_all_users() -> list[dict]:
    async with _connection() as conn:
        rows = await conn.fetch("SELECT id, name, email, balance FROM users ORDER BY id")
        return [dict(r) for r in rows]


async def get_item(item_name: str) -> dict | None:
    async with _connection() as conn:
        row = await conn.fetchrow(
            "SELECT name, display_name, price, stock FROM items WHERE name = $1", item_name
        )
        return dict(row) if row else None


async def get_all_items() -> list[dict]:
    async with _connection() as conn:
        rows = await conn.fetch(
            "SELECT name, display_name, price, stock FROM items ORDER BY display_name"
        )
        return [dict(r) for r in rows]


async def get_orders_by_user(user_id: int) -> list[dict]:
    async with _connection() as conn:
        rows = await conn.fetch(
            """SELECT o.id, o.item_name, i.display_name, o.quantity,
                      o.total_charged, o.payment_method, o.created_at
               FROM orders o
               JOIN items i ON i.name = o.item_name
               WHERE o.user_id = $1
               ORDER BY o.created_at DESC""",
            user_id,
        )
        return [dict(r) for r in rows]


async def count_users() -> int:
    async with _connection() as conn:
        return await conn.fetchval("SELECT count(*) FROM users")


async def total_inventory() -> int:
    async with _connection() as conn:
        return await conn.fetchval("SELECT coalesce(sum(stock), 0) FROM items")


async def apply_order(user_id: int, item_name: str, quantity: int, total_charged: float, payment_method: str = "credits") -> float:
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
    async with _connection() as conn:
        try:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE items SET stock = stock - $1 WHERE name = $2",
                    quantity, item_name,
                )
                # Card payments don't touch the balance — only credits path deducts.
                # RETURNING lets the caller detect the race condition (negative balance)
                # after the fact without a second query; returns None for card payments.
                if payment_method == "credits":
                    new_balance = await conn.fetchval(
                        "UPDATE users SET balance = balance - $1 WHERE id = $2 RETURNING balance",
                        total_charged, user_id,
                    )
                else:
                    new_balance = None
                await conn.execute(
                    """INSERT INTO orders (user_id, item_name, quantity, total_charged, payment_method)
                       VALUES ($1, $2, $3, $4, $5)""",
                    user_id, item_name, quantity, total_charged, payment_method,
                )
        except asyncpg.exceptions.ForeignKeyViolationError as e:
            raise DBForeignKeyViolation(str(e))
        except asyncpg.exceptions.DeadlockDetectedError as e:
            raise DBDeadlock(str(e))

    return float(new_balance) if new_balance is not None else None


async def topup_balance(user_id: int, amount: float) -> float:
    async with _connection() as conn:
        new_balance = await conn.fetchval(
            "UPDATE users SET balance = balance + $1 WHERE id = $2 RETURNING balance",
            amount, user_id,
        )
        return float(new_balance) if new_balance is not None else 0.0


async def restock_item(item_name: str, quantity: int) -> int:
    async with _connection() as conn:
        new_stock = await conn.fetchval(
            "UPDATE items SET stock = stock + $1 WHERE name = $2 RETURNING stock",
            quantity, item_name,
        )
        return int(new_stock) if new_stock is not None else 0
