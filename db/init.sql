-- Runs automatically on first Postgres container startup only
-- (docker-entrypoint-initdb.d only executes against a fresh, empty data volume).

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    balance NUMERIC(10, 2) NOT NULL DEFAULT 0
);

CREATE TABLE items (
    name TEXT PRIMARY KEY,
    stock INTEGER NOT NULL DEFAULT 0
);

-- No FK on quantity/total_charged correctness on purpose — create_order does the
-- balance/stock check in application code, non-atomically. See SECOND_ITERATION_ARCHITECTURE.md
-- for why that race condition is intentionally preserved rather than fixed here.
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    item_name TEXT NOT NULL REFERENCES items(name),
    quantity INTEGER NOT NULL,
    total_charged NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed data matches the Week 1 in-memory USERS_DB / INVENTORY exactly,
-- so endpoint behaviour is unchanged after the Postgres swap.
INSERT INTO users (name, email, balance) VALUES
    ('Alice', 'alice@example.com', 500.00),
    ('Bob', 'bob@example.com', 0.00),
    ('Charlie', 'charlie@example.com', 250.00);

INSERT INTO items (name, stock) VALUES
    ('item_a', 10),
    ('item_b', 0),
    ('item_c', 5);
