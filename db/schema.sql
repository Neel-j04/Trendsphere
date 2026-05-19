-- ══════════════════════════════════════════════════════════════════
--  TrendSphere — PostgreSQL Schema
--  Run: psql -U postgres -d trendsphere -f db/schema.sql
-- ══════════════════════════════════════════════════════════════════

-- Create DB (run once separately):
-- CREATE DATABASE trendsphere;

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for fast LIKE search

-- ─── Drop in safe order ───────────────────────────────────────────
DROP TABLE IF EXISTS behavior_events   CASCADE;
DROP TABLE IF EXISTS order_items       CASCADE;
DROP TABLE IF EXISTS orders            CASCADE;
DROP TABLE IF EXISTS products          CASCADE;
DROP TABLE IF EXISTS sellers           CASCADE;
DROP TABLE IF EXISTS users             CASCADE;
DROP TABLE IF EXISTS categories        CASCADE;
DROP TABLE IF EXISTS trend_predictions CASCADE;

-- ─── categories ──────────────────────────────────────────────────
CREATE TABLE categories (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    emoji       VARCHAR(10),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- ─── users ───────────────────────────────────────────────────────
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    email         VARCHAR(255) NOT NULL UNIQUE,
    name          VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20)  NOT NULL DEFAULT 'customer'
                  CHECK (role IN ('customer','seller','admin')),
    phone         VARCHAR(20),
    address       TEXT,
    city          VARCHAR(100),
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP DEFAULT NOW(),
    last_login    TIMESTAMP
);

-- ─── sellers ─────────────────────────────────────────────────────
CREATE TABLE sellers (
    id           SERIAL PRIMARY KEY,
    user_id      INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shop_name    VARCHAR(255) NOT NULL UNIQUE,
    description  TEXT,
    rating       DECIMAL(3,2) DEFAULT 0.00,
    total_sales  INT DEFAULT 0,
    is_verified  BOOLEAN DEFAULT FALSE,
    created_at   TIMESTAMP DEFAULT NOW()
);

-- ─── products ────────────────────────────────────────────────────
CREATE TABLE products (
    id           SERIAL PRIMARY KEY,
    product_code VARCHAR(50) UNIQUE,          -- maps to CSV product_id
    name         VARCHAR(500) NOT NULL,
    brand        VARCHAR(255),
    category_id  INT REFERENCES categories(id),
    seller_id    INT REFERENCES sellers(id),
    price        DECIMAL(12,2) NOT NULL,
    was_price    DECIMAL(12,2),               -- original price before discount
    description  TEXT,
    emoji        VARCHAR(10) DEFAULT '📦',
    badge        VARCHAR(20) DEFAULT ''       -- hot / new / top / ''
                 CHECK (badge IN ('hot','new','top','')),
    stock        INT DEFAULT 0,
    avg_rating   DECIMAL(3,2) DEFAULT 0.00,
    review_count INT DEFAULT 0,
    is_active    BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_products_brand     ON products(brand);
CREATE INDEX idx_products_name_trgm ON products USING GIN (name gin_trgm_ops);

-- ─── orders ──────────────────────────────────────────────────────
CREATE TABLE orders (
    id           SERIAL PRIMARY KEY,
    order_code   VARCHAR(50) NOT NULL UNIQUE,  -- e.g. ORD-20241201-001
    user_id      INT NOT NULL REFERENCES users(id),
    status       VARCHAR(30) NOT NULL DEFAULT 'Pending'
                 CHECK (status IN ('Pending','Processing','Shipped','Delivered','Cancelled','Refunded')),
    total_amount DECIMAL(12,2) NOT NULL,
    shipping_address TEXT,
    payment_method   VARCHAR(50) DEFAULT 'COD',
    placed_at    TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);

-- ─── order_items ─────────────────────────────────────────────────
CREATE TABLE order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INT NOT NULL REFERENCES products(id),
    qty         INT NOT NULL DEFAULT 1,
    unit_price  DECIMAL(12,2) NOT NULL,
    subtotal    DECIMAL(12,2) GENERATED ALWAYS AS (qty * unit_price) STORED
);

-- ─── behavior_events ─────────────────────────────────────────────
--  Core table: every row is one product's aggregated signals for a session.
--  Maps 1:1 to the CSV schema.
CREATE TABLE behavior_events (
    id            BIGSERIAL PRIMARY KEY,
    product_id    INT NOT NULL REFERENCES products(id),
    user_id       INT REFERENCES users(id),      -- NULL = anonymous
    session_id    VARCHAR(100),
    device_type   VARCHAR(30),
    user_location VARCHAR(100),
    views         INT DEFAULT 0,
    search        INT DEFAULT 0,
    cart          INT DEFAULT 0,
    wishlist      INT DEFAULT 0,
    purchase      INT DEFAULT 0,
    time_spent    DECIMAL(10,2) DEFAULT 0,       -- seconds
    event_ts      TIMESTAMP NOT NULL DEFAULT NOW(),
    hour          SMALLINT,
    day_of_week   VARCHAR(15),
    month         SMALLINT
);

CREATE INDEX idx_be_product  ON behavior_events(product_id);
CREATE INDEX idx_be_session  ON behavior_events(session_id);
CREATE INDEX idx_be_ts       ON behavior_events(event_ts);

-- ─── trend_predictions ───────────────────────────────────────────
--  Cached ML predictions — refreshed by the background job.
CREATE TABLE trend_predictions (
    id              SERIAL PRIMARY KEY,
    product_id      INT NOT NULL REFERENCES products(id),
    trend_score     DECIMAL(6,4),
    trend_status    VARCHAR(20),               -- viral/hot/rising/stable/cold
    confidence      DECIMAL(6,4),
    view_velocity   DECIMAL(8,4),
    search_momentum DECIMAL(8,4),
    wishlist_signal DECIMAL(8,4),
    cart_intent     DECIMAL(8,4),
    anomaly         BOOLEAN DEFAULT FALSE,
    forecast_7d     DECIMAL(6,4),
    predicted_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_tp_product ON trend_predictions(product_id);
CREATE INDEX idx_tp_score   ON trend_predictions(trend_score DESC);

-- ─── Seed: categories ────────────────────────────────────────────
INSERT INTO categories (name, emoji) VALUES
  ('Electronics',     '📱'),
  ('Fashion',         '👕'),
  ('Home & Kitchen',  '🏠'),
  ('Beauty',          '💄'),
  ('Sports',          '⚽'),
  ('Books',           '📚'),
  ('Toys',            '🧸'),
  ('Health',          '💊'),
  ('Grocery',         '🛒'),
  ('Automotive',      '🚗'),
  ('Computers',       '💻');

-- ─── Seed: admin user (password: admin123) ────────────────────────
INSERT INTO users (email, name, password_hash, role) VALUES
  ('admin@trendsphere.com', 'Admin User',
   'pbkdf2:sha256:600000$admin123hash',  -- replace with real hash in prod
   'admin');

-- ─── Views for convenience ───────────────────────────────────────

CREATE OR REPLACE VIEW v_top_trending AS
SELECT
    p.id,
    p.name,
    p.brand,
    c.name AS category,
    p.price,
    p.avg_rating,
    tp.trend_score,
    tp.trend_status,
    tp.forecast_7d,
    tp.anomaly,
    tp.predicted_at
FROM products p
JOIN trend_predictions tp ON tp.product_id = p.id
JOIN categories c ON c.id = p.category_id
WHERE p.is_active = TRUE
ORDER BY tp.trend_score DESC;

CREATE OR REPLACE VIEW v_product_signals_7d AS
SELECT
    be.product_id,
    p.name          AS product_name,
    c.name          AS category,
    SUM(be.views)   AS total_views,
    SUM(be.search)  AS total_searches,
    SUM(be.wishlist)AS total_wishlist,
    SUM(be.cart)    AS total_cart,
    SUM(be.purchase)AS total_purchases,
    COUNT(DISTINCT be.session_id) AS unique_sessions
FROM behavior_events be
JOIN products p  ON p.id = be.product_id
JOIN categories c ON c.id = p.category_id
WHERE be.event_ts >= NOW() - INTERVAL '7 days'
GROUP BY be.product_id, p.name, c.name;
