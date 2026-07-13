# SentinelAI — Iteration 3: A Real Store Worth Monitoring

> Continues the decision log started in [FIRST_ITERATION_ARCHITECTURE.md](FIRST_ITERATION_ARCHITECTURE.md) and continued in [SECOND_ITERATION_ARCHITECTURE.md](SECOND_ITERATION_ARCHITECTURE.md).
> Those files document Weeks 1 and 2 as actually built. This file documents Week 3's decisions in the same spirit: what was chosen, what was rejected, and why.

---

## The Feedback That Drove This Iteration

A friend reviewing the project gave the most direct critique yet: the app feels amateur. Triggering errors from a panel with no real context makes it feel like a bug demonstration, not an observability system for a real product. The specific ask: make the monitored application feel like something that would actually exist in production, so SentinelAI's job is "watching a real product" rather than "watching a test harness."

This iteration's entire goal follows from that critique. The changes are not about adding more AI sophistication — they are about making the thing being monitored credible enough that the monitoring matters.

---

## 1. The Store: TechNest — A Standalone E-Commerce App

### Why a separate app, not an enhanced target_app endpoint

Two options: add a few product-listing endpoints to `target_app` and build the store UI on top of what's already there, or build a genuinely separate React app that is the store, with `target_app` as its backend.

Chose the latter. The distinction matters:

- **Option 1 (add endpoints):** the store is still "the demo target app with a better frontend." The URL is still `localhost:8000`, the codebase is still called `target_app`, and the product still reads like a monitoring demo.
- **Option 2 (separate store):** a customer visiting `localhost:5174` sees a tech product store. SentinelAI at `localhost:5173` watches it. The relationship is the real one: SentinelAI is an external observability tool watching a separate application, which is exactly what Datadog or Sentry do. Building it as two genuinely separate things makes that relationship real, not performed.

`store/` is a separate Vite/React app (its own `package.json`, `Dockerfile`, port 5174) that calls `target_app` directly from the browser. SentinelAI's UI links to it via a "Store ↗" button that opens it in a new tab — not an iframe, not a nested route. Two separate applications that happen to be described together in the same monorepo.

### Name and branding

The store is called **TechNest**. The target app was never named before; anonymity made it feel like a test fixture. Giving it a name and brand identity is the smallest possible change that makes it feel like a real product rather than a demo app.

### Dark theme — matching SentinelAI's aesthetic

The store uses the same dark colour palette as the SentinelAI UI (`#0f1115` background, `#15171c` cards, `#c084fc` accent). This was a deliberate consistency choice: a visitor navigating from the store to the SentinelAI dashboard and back should feel like they're in the same product ecosystem, not jarred by a colour shift. Most real developer tooling (Vercel, Linear, Planetscale) uses dark-first design; a tech product store in 2024 matching that aesthetic is realistic rather than gimmicky.

---

## 2. Data Model: Real Products, Real Users, Two Payment Paths

### Products with prices and display names

The original `items` table had `name TEXT PRIMARY KEY, stock INTEGER` — functional for the backend but exposing nothing useful to a store frontend. Added:
- `display_name TEXT NOT NULL` — what the customer sees ("Wireless Headphones", not "headphones")
- `price NUMERIC(10,2) NOT NULL` — price per unit, fetched by the store and used by `create_order` at write time

The `name` field stays as the primary key slug (used throughout the codebase as the FK in `orders.item_name`). New products are real tech accessories:

| slug | display_name | price | initial stock |
|---|---|---|---|
| headphones | Wireless Headphones | $89.99 | 8 |
| keyboard | Mechanical Keyboard | $129.00 | 3 |
| usb_hub | USB-C Hub | $34.99 | 12 |
| webcam | Webcam 1080p | $59.99 | 0 (out of stock) |
| mouse_pad | Desk Mat XL | $24.99 | 20 |
| desk_lamp | LED Desk Lamp | $44.99 | 5 |
| ssd | Portable SSD 512GB | $79.99 | 2 |

`webcam` replacing `item_b` (always 0 stock) — same role in cascade detection, just named.

### Hardcoded ITEM_PRICE removed

Previously `ITEM_PRICE = 50.0` was a constant in `main.py` — the same price for every item. With prices now in the DB, `create_order` fetches the item record (already needed for stock check) and uses `float(item["price"]) * quantity` as the total. No extra query; the data was being fetched anyway.

### More realistic users

Expanded from 3 anonymous users to 5 named people. Bob Kumar stays at $0 credits to keep `order_failed_insufficient_balance` reliably triggerable for cascade detection. The other four have varied credit balances to give the store realistic per-user state.

### Why balance stayed, renamed to "store credits"

The original balance concept was questioned directly: "why should users have a balance?" The honest answer is that the balance is what makes `negative_balance_detected` — the race condition the AI investigator was designed to find — demonstrable. Without it, that entire scenario disappears.

The response wasn't to quietly keep it unchanged, but to make it explicitly realistic: store credits are a real product pattern (Shopee coins, Amazon Pay balance, Grab credits). The store UI shows the active user's credit balance in the header, labels it "credits" throughout, and provides it as one of two checkout options. A user who doesn't want to use credits can pay by card instead. The feature isn't hidden or misrepresented.

### Two payment methods at checkout

Both paths go through the same `fake_payment_service` call (holding a DB connection, making the cascade still possible) — but diverge on the balance check and deduction:

- **Store Credits:** `create_order` checks `balance >= total` before charging. If it passes, charges the payment service, then deducts from `balance`. The race condition exists between the check and the deduction — unchanged from Week 2, still demonstrable via the "Negative balance" trigger.
- **Credit Card:** skips the balance check and deduction entirely. The payment service call still happens (holds the DB connection, cascade still possible), but no balance column is touched.

`OrderRequest` got a `payment_method: str = "credits"` field. `db.apply_order()` conditionally skips the `UPDATE users SET balance = balance - ...` query for the card path, returning `None` instead of the post-write balance. `main.py`'s `negative_balance_detected` log is gated on `new_balance is not None and new_balance < 0`.

The `orders` table gained a `payment_method TEXT NOT NULL DEFAULT 'credits'` column so order history shows which path each purchase used — visible in the store's "My Orders" page.

---

## 3. New target_app Endpoints

Three new endpoints added to serve the store:

- **`GET /items`** — returns all items with `name`, `display_name`, `price`, `stock`. Used by the store's product grid on load and after checkout to refresh stock counts.
- **`GET /users`** — returns all users with `id`, `name`, `email`, `balance`. Used by the store's user-switcher dropdown in the header and to refresh credit balance after a purchase.
- **`GET /users/{user_id}/orders`** — returns a user's full order history, joined to `items` to include `display_name`. Used by the "My Orders" page.

None of these produce new monitored error events — they're read-only and don't interact with the logic the detector cares about.

### CORS added to target_app

`target_app` previously had no CORS headers — the only callers were backend services (agent, payment/email services) over Docker's internal network, so CORS wasn't needed. Now the store runs in the browser at a different port (`5174`) and calls `target_app` (`8000`) directly, which requires CORS. Added `CORSMiddleware` with `allow_origins=["*"]` — same wide-open-CORS reasoning as `sentinel-agent`'s existing middleware, since this is a single-user local demo, not a multi-tenant deployment where CORS is a real boundary.

---

## 4. The Store UI in Detail

### Product grid

Products are displayed in a responsive grid (`auto-fill, minmax(220px, 1fr)`). Each card shows:
- An emoji as the product image (🎧 headphones, ⌨️ keyboard, etc.) — no actual image hosting needed, and the emojis are immediately recognisable
- Display name and price
- A stock badge: "In stock" (green), "Only N left" for ≤3 units (yellow), "Out of stock" (grey)
- "Add to cart" button, disabled and relabelled "Unavailable" for out-of-stock items

### Cart drawer

Slides in from the right when a product is added or the cart icon is clicked. Shows item quantities (adjustable inline), per-item subtotals, and the order total. Stays out of the main product grid's way without requiring a navigation change.

### Checkout flow

The checkout section inside the cart drawer has two `<radio>` options: "Store Credits" (showing the active user's available balance) and "Credit Card". Clicking "Checkout" fires one `POST /orders` per unit per item concurrently (`Promise.all`), which is the right shape: checkout is naturally concurrent from a user's perspective (you don't want to wait for each item sequentially), and it's exactly the concurrency pattern that makes the race condition detectable — 30 concurrent orders of the same item from the "Negative balance" trigger does the same thing, just from the trigger panel instead of the store's cart button.

After checkout: clears the cart, refreshes the user's credit balance and the product stock counts, and shows a per-item success/error summary inline.

### User switcher

A dropdown in the header sets the active user. The store is demo software, not a real application — there's no login flow, no sessions. Making the user switcher obvious and prominent makes the demo experience straightforward: "switch to Bob, try to buy something expensive with store credits, watch it fail."

### Order history

"My Orders" page shows all past orders for the active user in reverse chronological order — item name, quantity, payment method, total, timestamp. Refreshes when switching users.

---

## 5. SentinelAI UI Changes

### Store tab

A "Store ↗" button in the tab bar opens `http://localhost:5174` in a new tab. This is an external link, not a tab state change — the store has its own full-page navigation with header, pages, and cart drawer, none of which would work embedded inside SentinelAI's 60px-tall header area. A `window.open()` is the right mechanism.

### Updated scenarios

All trigger scenarios updated to use real item names:
- "Negative balance" uses `headphones` ($89.99) instead of `item_a` ($50)
- "Add stock" dropdown now lists the 7 real item slugs
- "Payment cascade" uses `headphones` and `webcam` (the always-0-stock item) to keep the cascade pattern working
- Traffic simulator's ITEMS list updated to all 7 real slugs; VALID user list expanded to 1–5

### Label updates

"Add balance" renamed to "Add credits" and "User ID (1, 2, or 3)" labels updated to reflect 5 users. Scenario explanations updated to name real products and prices.

---

## 6. What Stays Unchanged

- All error detection, classification, AI dispatch, cooldown, cascade detection — no changes
- `negative_balance_detected` event name — same string the detector, AI engine, and vector memory all reference
- AI pipeline (investigator → fixer) — unchanged
- Prometheus metrics, Grafana dashboard — unchanged
- Redis, ChromaDB — unchanged
- All existing trigger scenarios — still work, just use real item names

---

## 7. Deferred

The items below were explicitly not built in this iteration:

- **Dedicated trigger buttons for `order_failed_insufficient_balance` and `order_failed_insufficient_stock` in the store UI** — the cascade scenario already covers both; a separate trigger is additive rather than necessary
- **`db_pool_exhausted` in `AI_WORTHY_EVENTS`** — hold-time instrumentation still missing; same reasoning as iteration 2
- **Product categories / search / filtering in the store** — the 7-product grid doesn't need filtering; deferred until product count grows
- **Fix `payment_cascade` to reliably hit `hold_connection()` under lower concurrency** — still the same trade-off as Week 2: reliable reproduction needs sustained load, not a one-shot burst
