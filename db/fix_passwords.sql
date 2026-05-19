-- ──────────────────────────────────────────────────────────────────
--  TrendSphere — Password Hash Fix Patch
--  Run this if you already seeded the DB with broken placeholder hashes.
--  Passwords:  admin → admin123 | sellers → seller123 | users → demo123
-- ──────────────────────────────────────────────────────────────────

-- Admin
UPDATE users
SET password_hash = 'pbkdf2:sha256:600000$2JZgUDt2zfJwzFiw$8241766e0ca97e433c7f3c85cf8c94ba9bf1f5242882eaa9a1e3d530199d576c'
WHERE email = 'admin@trendsphere.com';

-- All sellers (seller1@..  through seller10@..)
UPDATE users
SET password_hash = 'pbkdf2:sha256:600000$qNqI8AuAFVaCdCg2$561cef6f2b16217f1ffde401873e1826907494f1232adf129c416bcc9a3572bc'
WHERE role = 'seller';

-- All demo customers
UPDATE users
SET password_hash = 'pbkdf2:sha256:600000$DzqHPhtLwa8Ec2VV$1a27e0d70fec4e226a20442e01f6154f857a68fe936383c154adcc10bbcf59d3'
WHERE role = 'customer';

-- Confirm
SELECT email, role,
       CASE WHEN password_hash LIKE 'pbkdf2:sha256:600000$x$%' THEN '❌ STILL BROKEN' ELSE '✅ Fixed' END AS status
FROM users
ORDER BY role, email;
