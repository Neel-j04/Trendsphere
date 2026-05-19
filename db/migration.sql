-- ══════════════════════════════════════════════════════════════
--  TrendSphere — Schema Update / Migration
--  Run this in pgAdmin Query Tool on your 'trendsphere' database
--  Safe to run multiple times (uses IF NOT EXISTS)
-- ══════════════════════════════════════════════════════════════

-- ── 1. OTP tokens for password reset ─────────────────────────
CREATE TABLE IF NOT EXISTS otp_tokens (
    id         SERIAL PRIMARY KEY,
    email      VARCHAR(255) NOT NULL,
    otp        VARCHAR(10)  NOT NULL,
    purpose    VARCHAR(30)  DEFAULT 'reset',   -- reset / verify
    expires_at TIMESTAMP    NOT NULL,
    used       BOOLEAN      DEFAULT FALSE,
    created_at TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_tokens(email);

-- ── 2. Page visit analytics ───────────────────────────────────
CREATE TABLE IF NOT EXISTS page_visits (
    id         BIGSERIAL PRIMARY KEY,
    path       VARCHAR(255) NOT NULL,
    user_id    INT REFERENCES users(id) ON DELETE SET NULL,
    session_id VARCHAR(100),
    ip_address VARCHAR(45),
    user_agent TEXT,
    referrer   VARCHAR(500),
    visited_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pv_path    ON page_visits(path);
CREATE INDEX IF NOT EXISTS idx_pv_ts      ON page_visits(visited_at);
CREATE INDEX IF NOT EXISTS idx_pv_user    ON page_visits(user_id);

-- ── 3. Wishlist table (proper, per-user) ─────────────────────
CREATE TABLE IF NOT EXISTS wishlists (
    id         SERIAL PRIMARY KEY,
    user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    added_at   TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, product_id)
);

-- ── 4. Add image_url to products if missing ──────────────────
ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url VARCHAR(500);

-- ── 5. Add city / phone to users if missing ──────────────────
ALTER TABLE users ADD COLUMN IF NOT EXISTS city    VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone   VARCHAR(20);

-- ── 6. Update admin password to proper bcrypt hash ───────────
--  Password = admin123  (you'll reset via OTP after setup)
UPDATE users
SET password_hash = 'pbkdf2:sha256:600000$trendsphere$8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92'
WHERE email = 'admin@trendsphere.com';

-- ── 7. Useful admin view: daily sales ────────────────────────
CREATE OR REPLACE VIEW v_daily_sales AS
SELECT
    placed_at::DATE        AS sale_date,
    COUNT(*)               AS order_count,
    SUM(total_amount)      AS revenue,
    AVG(total_amount)      AS avg_order_value
FROM orders
WHERE status NOT IN ('Cancelled','Refunded')
GROUP BY placed_at::DATE
ORDER BY sale_date DESC;

-- ── 8. Useful admin view: product performance ─────────────────
CREATE OR REPLACE VIEW v_product_performance AS
SELECT
    p.id,
    p.name,
    c.name          AS category,
    p.price,
    p.stock,
    COALESCE(SUM(oi.qty), 0)            AS units_sold,
    COALESCE(SUM(oi.subtotal), 0)       AS revenue,
    COALESCE(SUM(be.views), 0)          AS total_views,
    COALESCE(SUM(be.cart), 0)           AS total_carts,
    p.avg_rating,
    p.review_count
FROM products p
JOIN categories c ON c.id = p.category_id
LEFT JOIN order_items oi ON oi.product_id = p.id
LEFT JOIN behavior_events be ON be.product_id = p.id
WHERE p.is_active = TRUE
GROUP BY p.id, p.name, c.name, p.price, p.stock, p.avg_rating, p.review_count;

-- ── 9. Useful admin view: customer activity ───────────────────
CREATE OR REPLACE VIEW v_customer_activity AS
SELECT
    u.id,
    u.name,
    u.email,
    u.city,
    u.created_at,
    u.last_login,
    COUNT(DISTINCT o.id)    AS total_orders,
    COALESCE(SUM(o.total_amount), 0) AS total_spent,
    COUNT(DISTINCT pv.id)   AS page_visits
FROM users u
LEFT JOIN orders o  ON o.user_id = u.id AND o.status != 'Cancelled'
LEFT JOIN page_visits pv ON pv.user_id = u.id
WHERE u.role = 'customer'
GROUP BY u.id, u.name, u.email, u.city, u.created_at, u.last_login;

SELECT 'Migration complete ✅' AS status;
