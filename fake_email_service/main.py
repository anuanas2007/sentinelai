"""
A deliberately unreliable internal service -- stands in for something
like a real email/notification provider. Exists specifically to give
target_app's background-task failure mode (see main.py's
send_order_confirmation) something real to call over the network,
instead of an in-process function dressed up as a remote call.

~40% random failure rate, controlled and predictable -- unlike
httpbin.org (used by /external), we own this service's exact behavior,
so its failure rate doesn't depend on a third party's real-world
flakiness.
"""
import random
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Fake Email Service")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/send")
async def send():
    if random.random() < 0.4:
        raise HTTPException(status_code=500, detail="Email provider unavailable")
    return {"status": "sent"}
