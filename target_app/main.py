import random
import structlog
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
from logger import setup_logger
import db

# Setup logging — writes to stdout and logs/app.log
setup_logger()

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await db.close_pool()


app = FastAPI(
    title="Target App",
    description="A realistic app that SentinelAI monitors",
    lifespan=lifespan,
)

ITEM_PRICE = 50.0


class OrderRequest(BaseModel):
    user_id: int
    item: str
    quantity: int


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/users/{user_id}")
async def get_user(user_id: int):
    log.info("fetching_user", user_id=user_id)

    try:
        user = await db.get_user(user_id)
    except db.DBPoolExhausted as e:
        log.error("db_pool_exhausted", user_id=user_id, error=str(e))
        raise HTTPException(status_code=503, detail="Database pool exhausted")
    except db.DBConnectionError as e:
        log.error("db_connection_error", user_id=user_id, error=str(e))
        raise HTTPException(status_code=503, detail="Database connection error")

    if not user:
        log.error("user_not_found", user_id=user_id, error="User does not exist")
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    log.info("user_fetched_successfully", user_id=user_id)
    return user


@app.post("/orders")
async def create_order(order: OrderRequest):
    log.info("creating_order", user_id=order.user_id, item=order.item)

    try:
        user = await db.get_user(order.user_id)
        item = await db.get_item(order.item)
    except db.DBPoolExhausted as e:
        log.error("db_pool_exhausted", user_id=order.user_id, error=str(e))
        raise HTTPException(status_code=503, detail="Database pool exhausted")
    except db.DBConnectionError as e:
        log.error("db_connection_error", user_id=order.user_id, error=str(e))
        raise HTTPException(status_code=503, detail="Database connection error")

    # Check user exists
    if not user:
        log.error("order_failed_user_not_found", user_id=order.user_id)
        raise HTTPException(status_code=404, detail="User not found")

    # Check inventory
    stock = item["stock"] if item else 0
    if stock < order.quantity:
        log.error("order_failed_insufficient_stock",
                  item=order.item,
                  requested=order.quantity,
                  available=stock)
        raise HTTPException(status_code=400, detail=f"Insufficient stock for {order.item}")

    # Check balance
    total = ITEM_PRICE * order.quantity
    balance = float(user["balance"])
    if balance < total:
        log.error("order_failed_insufficient_balance",
                  user_id=order.user_id,
                  required=total,
                  available=balance)
        raise HTTPException(status_code=400, detail="Insufficient balance")

    # Write — deliberately not re-checking balance/stock here. See
    # docs/SECOND_ITERATION_ARCHITECTURE.md for why this gap is intentional.
    try:
        new_balance = await db.apply_order(order.user_id, order.item, order.quantity, total)
    except db.DBForeignKeyViolation as e:
        log.error("order_failed_fk_violation",
                  user_id=order.user_id, item=order.item, error=str(e))
        raise HTTPException(status_code=404, detail="Referenced user or item no longer exists")
    except db.DBDeadlock as e:
        log.error("db_deadlock", user_id=order.user_id, item=order.item, error=str(e))
        raise HTTPException(status_code=503, detail="Database deadlock, please retry")
    except db.DBPoolExhausted as e:
        log.error("db_pool_exhausted", user_id=order.user_id, error=str(e))
        raise HTTPException(status_code=503, detail="Database pool exhausted")
    except db.DBConnectionError as e:
        log.error("db_connection_error", user_id=order.user_id, error=str(e))
        raise HTTPException(status_code=503, detail="Database connection error")

    log.info("order_created_successfully", user_id=order.user_id, item=order.item)

    # Detected after the fact, not prevented — this is what makes the
    # earlier non-atomic check-then-write race condition actually visible
    # as an incident instead of a silent data-integrity bug.
    if new_balance < 0:
        log.error("negative_balance_detected",
                  user_id=order.user_id, balance=new_balance)

    return {"status": "success", "total_charged": total}


@app.get("/analytics")
async def get_analytics():
    log.info("computing_analytics")

    try:
        total_users = await db.count_users()
        total_inventory = await db.total_inventory()
    except db.DBPoolExhausted as e:
        log.error("db_pool_exhausted", error=str(e))
        raise HTTPException(status_code=503, detail="Database pool exhausted")
    except db.DBConnectionError as e:
        log.error("db_connection_error", error=str(e))
        raise HTTPException(status_code=503, detail="Database connection error")

    # Simulate division by zero bug
    if random.random() < 0.3:
        try:
            active_users = 0  # bug: no active users tracked
            ratio = total_users / active_users  # this will crash
        except ZeroDivisionError as e:
            log.error("analytics_failed",
                      error=str(e),
                      error_type="ZeroDivisionError")
            raise HTTPException(status_code=500, detail="Analytics computation failed")

    return {
        "total_users": total_users,
        "total_inventory": total_inventory,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/external")
async def call_external():
    log.info("calling_external_api")

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            # This URL will timeout - simulating external API failure
            response = await client.get("https://httpbin.org/delay/5")
            return response.json()
    except httpx.TimeoutException:
        log.error("external_api_timeout",
                  error="External API did not respond within 3 seconds",
                  error_type="TimeoutException")
        raise HTTPException(status_code=503, detail="External service unavailable")
    except Exception as e:
        # Real third-party services don't always fail the way you expect --
        # discovered live that httpbin.org can return a non-JSON/empty body
        # instead of timing out, which previously crashed with an unlogged
        # 500. Anything other than a clean timeout lands here so it's at
        # least visible to the agent instead of silently invisible.
        log.error("external_api_error",
                  error=str(e),
                  error_type=type(e).__name__)
        raise HTTPException(status_code=503, detail="External service unavailable")
