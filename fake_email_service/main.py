"""
A deliberately unreliable internal service -- stands in for something
like a real email/notification provider. Exists specifically to give
target_app's background-task failure mode (see main.py's
send_order_confirmation) something real to call over the network,
instead of an in-process function dressed up as a remote call.

~5% random failure rate, controlled and predictable -- unlike
httpbin.org (used by /external), we own this service's exact behavior,
so its failure rate doesn't depend on a third party's real-world
flakiness. Deliberately kept low: a higher rate made the health
monitor (see target_app/main.py's monitor_email_service_health) fire
spontaneously just from background polling, with no real outage and
no deliberate test -- 5% makes that astronomically rare while still
leaving a real, occasionally-reachable failure path for the
background task demo.
"""
import random
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Fake Email Service")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/send")
async def send():
    if random.random() < 0.05:
        raise HTTPException(status_code=500, detail="Email provider unavailable")
    return {"status": "sent"}
