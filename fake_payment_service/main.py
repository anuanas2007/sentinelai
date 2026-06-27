"""
A deliberately *slow*, not failure-prone, internal service -- stands in
for a payment processor. Unlike fake_email_service (which fails fast
~40% of the time), this one almost always succeeds, but ~20% of the
time takes 8 seconds to respond first.

The point isn't "does this call fail" -- it's "what happens to
target_app's OWN capacity when many concurrent requests are all
waiting on the same slow dependency at once." That's the actual
cascade mechanism: slowness, not errors, is what ties up resources
across requests that have nothing to do with payments.
"""
import asyncio
import random
from fastapi import FastAPI

app = FastAPI(title="Fake Payment Service")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/charge")
async def charge():
    if random.random() < 0.2:
        await asyncio.sleep(8)
    return {"status": "charged"}
