import os
import random
import asyncio
from typing import NoReturn
import structlog
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime
from logger import setup_logger
import db

# Setup logging — writes to stdout and logs/app.log
setup_logger()

log = structlog.get_logger()

EMAIL_SERVICE_URL = os.environ.get("EMAIL_SERVICE_URL", "http://localhost:8001")
EMAIL_HEALTH_CHECK_INTERVAL = 60  # seconds between polls
EMAIL_HEALTH_FAILURE_THRESHOLD = 3  # consecutive failures = confirmed outage, not noise
PAYMENT_SERVICE_URL = os.environ.get("PAYMENT_SERVICE_URL", "http://localhost:8002")

# asyncio's own docs warn that a task with no surviving reference can be
# garbage-collected mid-execution -- this set exists purely to hold that
# reference until the task finishes, not for any other bookkeeping.
_background_tasks: set = set()


async def monitor_email_service_health() -> None:
    """
    Runs forever, polling fake_email_service's /send -- not /health,
    which always returns 200 by design and would never let this detect
    anything. Treats EMAIL_HEALTH_FAILURE_THRESHOLD consecutive failures
    as a confirmed sustained outage (not just normal per-call ~5%
    randomness) and logs email_service_unreachable, then resets the
    counter so a later, separate outage can trigger a fresh alert
    rather than this staying permanently "fired" on the first one.
    """
    consecutive_failures = 0
    async with httpx.AsyncClient(timeout=3.0) as client:
        while True:
            await asyncio.sleep(EMAIL_HEALTH_CHECK_INTERVAL)
            try:
                response = await client.post(
                    f"{EMAIL_SERVICE_URL}/send", json={"user_id": 0, "item": "_healthcheck"}
                )
                response.raise_for_status()
                consecutive_failures = 0
            except httpx.HTTPError:
                consecutive_failures += 1
                if consecutive_failures >= EMAIL_HEALTH_FAILURE_THRESHOLD:
                    log.error("email_service_unreachable", consecutive_failures=consecutive_failures)
                    consecutive_failures = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    health_check_task = asyncio.create_task(monitor_email_service_health())
    yield
    health_check_task.cancel()
    try:
        await health_check_task
    except asyncio.CancelledError:
        pass
    await db.close_pool()


app = FastAPI(
    title="Target App",
    description="A realistic app that SentinelAI monitors",
    lifespan=lifespan,
)

# Store runs on a different port — CORS required so the browser can call
# target-app directly. Single-user local demo so wide-open is fine.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    internal service (~5% failure rate). Whatever happens here, the
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


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str  # accepted but not stored — demo auth only


class OrderRequest(BaseModel):
    user_id: int
    item: str
    quantity: int
    payment_method: str = "credits"  # "credits" | "card"


class TopupRequest(BaseModel):
    user_id: int
    amount: float


class RestockRequest(BaseModel):
    item_name: str
    quantity: int


class SetStockRequest(BaseModel):
    item_name: str
    stock: int


@app.post("/users", status_code=201)
async def signup(req: SignupRequest):
    try:
        user = await db.create_user(req.name, req.email)
        log.info("user_created", user_id=user["id"])
        return user
    except db.DBUniqueViolation:
        raise HTTPException(status_code=409, detail="Email already in use.")
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e)


@app.post("/admin/topup")
async def admin_topup(req: TopupRequest):
    new_balance = await db.topup_balance(req.user_id, req.amount)
    return {"user_id": req.user_id, "new_balance": new_balance}


@app.post("/admin/restock")
async def admin_restock(req: RestockRequest):
    new_stock = await db.restock_item(req.item_name, req.quantity)
    return {"item_name": req.item_name, "new_stock": new_stock}


@app.post("/admin/set_stock")
async def admin_set_stock(req: SetStockRequest):
    new_stock = await db.set_stock(req.item_name, req.stock)
    return {"item_name": req.item_name, "new_stock": new_stock}


@app.get("/items")
async def list_items():
    try:
        return await db.get_all_items()
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e)


@app.get("/users")
async def list_users():
    try:
        return await db.get_all_users()
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e)


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/users/{user_id}/orders")
async def get_user_orders(user_id: int):
    try:
        user = await db.get_user(user_id)
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e, user_id=user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    try:
        orders = await db.get_orders_by_user(user_id)
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e, user_id=user_id)
    return orders


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

    total = float(item["price"]) * order.quantity

    # Credits path only: check the user has enough store credits before charging.
    # Card path skips this — the payment service is the only gate.
    if order.payment_method == "credits":
        balance = float(user["balance"])
        if balance < total:
            log.error("order_failed_insufficient_balance",
                      user_id=order.user_id,
                      required=total,
                      available=balance)
            raise HTTPException(status_code=400, detail="Insufficient store credits")

    # Charge before writing the order -- unlike the fire-and-forget email
    # confirmation below, payment genuinely must complete before we can
    # confirm anything, so this call is synchronous.
    #
    # Holding a DB connection across the call is the actual cascade
    # mechanism: async I/O waits don't tie up the event loop by
    # themselves (verified directly -- 15 concurrent slow payment calls
    # with no held connection caused zero latency change on an unrelated
    # endpoint), so a slow call alone doesn't cascade in this
    # architecture. A connection IS a genuinely scarce resource (5 max)
    # -- holding one for up to 8s per slow call can exhaust the pool for
    # *other*, unrelated requests too, under enough concurrent slow
    # charges. See docs/SECOND_ITERATION_ARCHITECTURE.md for the full
    # before/after verification.
    try:
        async with db.hold_connection():
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{PAYMENT_SERVICE_URL}/charge")
    except httpx.TimeoutException:
        log.error("payment_service_timeout",
                  user_id=order.user_id, item=order.item)
        raise HTTPException(status_code=503, detail="Payment service unavailable")
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e, user_id=order.user_id)

    # Write — deliberately not re-checking balance/stock here. See
    # docs/SECOND_ITERATION_ARCHITECTURE.md for why this gap is intentional.
    try:
        result = await db.apply_order(
            order.user_id, order.item, order.quantity, total, order.payment_method
        )
    except db.DBForeignKeyViolation as e:
        log.error("order_failed_fk_violation",
                  user_id=order.user_id, item=order.item, error=str(e))
        raise HTTPException(status_code=404, detail="Referenced user or item no longer exists")
    except db.DBDeadlock as e:
        log.error("db_deadlock", user_id=order.user_id, item=order.item, error=str(e))
        raise HTTPException(status_code=503, detail="Database deadlock, please retry")
    except (db.DBPoolExhausted, db.DBConnectionError) as e:
        _handle_db_exception(e, user_id=order.user_id)

    new_balance = result["new_balance"]
    new_stock   = result["new_stock"]

    log.info("order_created_successfully", user_id=order.user_id, item=order.item)

    # Fire-and-forget -- the response below goes back to the client
    # whether this succeeds or not. See send_order_confirmation/
    # _on_background_task_done for why a failure here is genuinely
    # invisible unless specifically watched for.
    task = asyncio.create_task(send_order_confirmation(order.user_id, order.item))
    _background_tasks.add(task)
    task.add_done_callback(_on_background_task_done)

    # Detected after the fact, not prevented — makes the non-atomic
    # check-then-write race conditions visible as incidents.
    if new_balance is not None and new_balance < 0:
        log.error("negative_balance_detected",
                  user_id=order.user_id, balance=new_balance)
    if new_stock < 0:
        log.error("negative_stock_detected",
                  item=order.item, stock=new_stock)

    return {"status": "success", "total_charged": total, "payment_method": order.payment_method}


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
