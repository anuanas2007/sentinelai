import random
import asyncio
import structlog
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime

# Configure structured logging
log = structlog.get_logger()

app = FastAPI(
    title="Target App",
    description="A realistic app that SentinelAI monitors"
)

# Simulated database
USERS_DB = {
    1: {"id": 1, "name": "Alice", "email": "alice@example.com", "balance": 500.0},
    2: {"id": 2, "name": "Bob", "email": "bob@example.com", "balance": 0.0},
    3: {"id": 3, "name": "Charlie", "email": "charlie@example.com", "balance": 250.0},
}

INVENTORY = {
    "item_a": 10,
    "item_b": 0,  # deliberately out of stock
    "item_c": 5,
}

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

    # Simulate DB timeout randomly
    if random.random() < 0.2:
        await asyncio.sleep(5)
        log.error("db_timeout", user_id=user_id, error="Database connection timed out")
        raise HTTPException(status_code=504, detail="Database timeout")

    user = USERS_DB.get(user_id)
    if not user:
        log.error("user_not_found", user_id=user_id, error="User does not exist")
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    log.info("user_fetched_successfully", user_id=user_id)
    return user


@app.post("/orders")
async def create_order(order: OrderRequest):
    log.info("creating_order", user_id=order.user_id, item=order.item)

    # Check user exists
    user = USERS_DB.get(order.user_id)
    if not user:
        log.error("order_failed_user_not_found", user_id=order.user_id)
        raise HTTPException(status_code=404, detail="User not found")

    # Check inventory
    stock = INVENTORY.get(order.item, 0)
    if stock < order.quantity:
        log.error("order_failed_insufficient_stock",
                  item=order.item,
                  requested=order.quantity,
                  available=stock)
        raise HTTPException(status_code=400, detail=f"Insufficient stock for {order.item}")

    # Check balance
    item_price = 50.0
    total = item_price * order.quantity
    if user["balance"] < total:
        log.error("order_failed_insufficient_balance",
                  user_id=order.user_id,
                  required=total,
                  available=user["balance"])
        raise HTTPException(status_code=400, detail="Insufficient balance")

    INVENTORY[order.item] -= order.quantity
    user["balance"] -= total
    log.info("order_created_successfully", user_id=order.user_id, item=order.item)
    return {"status": "success", "total_charged": total}


@app.get("/analytics")
async def get_analytics():
    log.info("computing_analytics")

    # Simulate division by zero bug
    if random.random() < 0.3:
        try:
            total_users = len(USERS_DB)
            active_users = 0  # bug: no active users tracked
            ratio = total_users / active_users  # this will crash
        except ZeroDivisionError as e:
            log.error("analytics_failed",
                      error=str(e),
                      error_type="ZeroDivisionError")
            raise HTTPException(status_code=500, detail="Analytics computation failed")

    return {
        "total_users": len(USERS_DB),
        "total_inventory": sum(INVENTORY.values()),
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