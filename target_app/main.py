import os
import random
import asyncio
from typing import NoReturn
import structlog
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime
from logger import setup_logger
import db

# Setup logging — writes to stdout and logs/app.log
setup_logger()

log = structlog.get_logger()

ITEM_PRICE = 50.0
EMAIL_SERVICE_URL = os.environ.get("EMAIL_SERVICE_URL", "http://localhost:8001")

# asyncio's own docs warn that a task with no surviving reference can be
# garbage-collected mid-execution -- this set exists purely to hold that
# reference until the task finishes, not for any other bookkeeping.
_background_tasks: set = set()


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


@app.exception_handler(Exception)
async def catch_all_exceptions(request: Request, exc: Exception):
    """
    Safety net for exceptions nobody anticipated. Starlette only falls
    back to this when no more specific handler matches (HTTPException
    has its own, already-registered handler) -- so every deliberate
    `raise HTTPException(...)` in this file keeps working exactly as
    before. This exists because /external previously crashed with a
    completely unlogged 500 (a JSONDecodeError no one wrote a specific
    except clause for) -- invisible to the whole monitoring pipeline.
    Patching that one endpoint after finding it doesn't scale; this
    guarantees the *next* unanticipated exception, anywhere, is logged.
    """
    log.error("unhandled_exception",
              error=str(exc), error_type=type(exc).__name__, path=str(request.url))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _handle_db_exception(e: Exception, **log_context) -> NoReturn:
    """
    Translates db.py's typed pool/connection exceptions into the matching
    structured log event + HTTPException, in one place instead of the
    same four-line block repeated at every call site that touches the DB.
    """
    if isinstance(e, db.DBPoolExhausted):
        log.error("db_pool_exhausted", error=str(e), **log_context)
        raise HTTPException(status_code=503, detail="Database pool exhausted")
    if isinstance(e, db.DBConnectionError):
        log.error("db_connection_error", error=str(e), **log_context)
        raise HTTPException(status_code=503, detail="Database connection error")
    raise e


async def send_order_confirmation(user_id: int, item: str) -> None:
    """
    Fire-and-forget: calls fake_email_service, a deliberately unreliable
    internal service (~40% failure rate). Whatever happens here, the
    client already got their response before this runs -- that's the
    point. Nothing about success or failure here is visible to the
    caller; only _on_background_task_done (below) makes a failure
    visible at all, and only asynchronously, after the fact.
    """
    async with httpx.AsyncClient(timeout=3.0) as client:
        response = await client.post(
            f"{EMAIL_SERVICE_URL}/send", json={"user_id": user_id, "item": item}
        )
        response.raise_for_status()


def _on_background_task_done(task: "asyncio.Task") -> None:
    """
    Without this callback, a failed background task is lost completely --
    asyncio prints at most a quiet "Task exception was never retrieved"
    warning whenever the task object happens to get garbage-collected,
    easily missed in a busy log stream. This is the only thing that
    turns that into an actual structured event the agent can see.
    """
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("background_task_failed",
                  task="send_order_confirmation",
                  error=str(exc), error_type=type(exc).__name__)


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
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e, user_id=user_id)

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
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e, user_id=order.user_id)

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
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e, user_id=order.user_id)

    log.info("order_created_successfully", user_id=order.user_id, item=order.item)

    # Fire-and-forget -- the response below goes back to the client
    # whether this succeeds or not. See send_order_confirmation/
    # _on_background_task_done for why a failure here is genuinely
    # invisible unless specifically watched for.
    task = asyncio.create_task(send_order_confirmation(order.user_id, order.item))
    _background_tasks.add(task)
    task.add_done_callback(_on_background_task_done)

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
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e)

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
