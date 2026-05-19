"""
db/database.py  —  PostgreSQL connection pool via psycopg2
"""
import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Neel2004@localhost:5432/trendsphere")
_pool = None

def init_pool():
    global _pool
    _pool = pool.ThreadedConnectionPool(
        minconn=2,
        maxconn=10,
        dsn=DATABASE_URL,
        cursor_factory=RealDictCursor,
    )
    return _pool

def get_pool():
    global _pool
    if _pool is None or _pool.closed:
        init_pool()
    return _pool

@contextmanager
def get_db():
    """Context manager — yields a cursor, auto-commits or rolls back."""
    p   = get_pool()
    conn = p.getconn()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


# ─── Query helpers ────────────────────────────────────────────────

def fetchall(sql: str, params=None):
    with get_db() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()

def fetchone(sql: str, params=None):
    with get_db() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()

def execute(sql: str, params=None):
    with get_db() as cur:
        cur.execute(sql, params or ())
        if cur.description:
            return cur.fetchall()
        return None

def execute_returning(sql: str, params=None):
    with get_db() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()
