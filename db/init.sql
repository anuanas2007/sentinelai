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
    display_name TEXT NOT NULL,
    price NUMERIC(10, 2) NOT NULL,
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
    payment_method TEXT NOT NULL DEFAULT 'credits',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Bob has $0 credits so payment_cascade reliably produces order_failed_insufficient_balance.
-- Webcam has 0 stock so payment_cascade reliably produces order_failed_insufficient_stock.
-- Both are needed for the cascade pattern (3 alternating co-occurrences) to confirm.
INSERT INTO users (name, email, balance) VALUES
    ('Alice Chen',   'alice@example.com',   800.00),
    ('Bob Kumar',    'bob@example.com',       0.00),
    ('Charlie Lee',  'charlie@example.com', 400.00),
    ('Diana Park',   'diana@example.com',   600.00),
    ('Ethan Wright', 'ethan@example.com',   150.00);

INSERT INTO items (name, display_name, price, stock) VALUES
    ('headphones',      'Wireless Headphones',   89.99,  8),
    ('keyboard',        'Mechanical Keyboard',  129.00,  3),
    ('usb_hub',         'USB-C Hub',             34.99, 12),
    ('webcam',          'Webcam 1080p',          59.99,  0),
    ('mouse_pad',       'Desk Mat XL',           24.99, 20),
    ('desk_lamp',       'LED Desk Lamp',         44.99,  5),
    ('ssd',             'Portable SSD 512GB',    79.99,  2),
    ('monitor',         '27" IPS Monitor',      349.99,  2),
    ('laptop_stand',    'Laptop Stand',          49.99,  7),
    ('wireless_charger','Wireless Charger Pad',  29.99,  8),
    ('microphone',      'USB Condenser Mic',     99.99,  3),
    ('led_strip',       'LED Strip Lights',      24.99, 10),
    ('controller',      'Gaming Controller',     69.99,  4),
    ('cable_pack',      'USB-C Cable Pack',      14.99, 25),
    ('headphone_stand', 'Headphone Stand',       34.99,  6);
