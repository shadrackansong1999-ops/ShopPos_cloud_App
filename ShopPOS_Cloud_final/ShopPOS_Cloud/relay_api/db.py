"""
ShopPOS Cloud Relay - Database layer
=====================================
Defaults to a local SQLite file so the relay runs with zero setup.

IMPORTANT (Render free tier): the filesystem on Render's free web-service
plan is EPHEMERAL - it is wiped on every deploy/restart. For anything
beyond a demo, either:
  (a) attach a Render Persistent Disk mounted at /data and set
      RELAY_DB_PATH=/data/relay.db, or
  (b) set DATABASE_URL to a Postgres connection string (Render's free
      Postgres works fine) and this module will use that instead.

Both paths use the exact same SQL (written with "?" placeholders and
translated to "%s" automatically for Postgres), so nothing else in the
app needs to know which backend is active.

SCHEMA: owners (login: email/password) can each own several shops. This
replaces an earlier version where email/password lived directly on the
shops table (one shop per login) - init_db() migrates any existing data
from that shape forward automatically, so nothing already registered is
lost when this deploys.
"""

import os
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
else:
    DB_PATH = os.environ.get("RELAY_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "relay.db"))
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_lock = threading.Lock()


def _translate(sql: str) -> str:
    """Translate sqlite-style "?" placeholders to psycopg2 "%s"."""
    return sql.replace("?", "%s") if USE_POSTGRES else sql


@contextmanager
def get_conn():
    """Yields a raw connection (psycopg2 or sqlite3), committed/closed on exit."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        with _lock:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()


def execute(sql, params=()):
    """Run a single write statement (INSERT/UPDATE/DELETE) and commit."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_translate(sql), params)
        return getattr(cur, "lastrowid", None)


def query_one(sql, params=()):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_translate(sql), params)
        row = cur.fetchone()
        return dict(row) if row is not None else None


def query_all(sql, params=()):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(_translate(sql), params)
        return [dict(r) for r in cur.fetchall()]


# ── Schema ────────────────────────────────────────────────────────────────
_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS owners (
    id             TEXT PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shops (
    id                TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    shop_name         TEXT NOT NULL,
    shop_key_hash     TEXT NOT NULL,
    low_stock_json    TEXT NOT NULL DEFAULT '[]',
    low_stock_count   INTEGER NOT NULL DEFAULT 0,
    inventory_json    TEXT NOT NULL DEFAULT '[]',
    users_json        TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen         TEXT
);

CREATE TABLE IF NOT EXISTS sales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id         TEXT NOT NULL,
    remote_sale_id  INTEGER NOT NULL,
    invoice_number  TEXT,
    customer_name   TEXT,
    cashier         TEXT,
    subtotal        REAL,
    discount        REAL,
    total           REAL,
    amount_paid     REAL,
    change_given    REAL,
    payment_method  TEXT,
    status          TEXT,
    notes           TEXT,
    items_json      TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT,
    received_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(shop_id, remote_sale_id)
);
CREATE INDEX IF NOT EXISTS idx_sales_shop_created ON sales(shop_id, created_at DESC);

CREATE TABLE IF NOT EXISTS movements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id         TEXT NOT NULL,
    remote_move_id  INTEGER NOT NULL,
    product_name    TEXT,
    movement_type   TEXT,
    quantity        REAL,
    reference       TEXT,
    notes           TEXT,
    created_at      TEXT,
    received_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(shop_id, remote_move_id)
);
CREATE INDEX IF NOT EXISTS idx_moves_shop_created ON movements(shop_id, created_at DESC);

CREATE TABLE IF NOT EXISTS snapshots (
    shop_id            TEXT NOT NULL,
    date               TEXT NOT NULL,
    total_revenue      REAL DEFAULT 0,
    num_sales          INTEGER DEFAULT 0,
    total_discounts    REAL DEFAULT 0,
    total_collected    REAL DEFAULT 0,
    by_payment_json    TEXT NOT NULL DEFAULT '[]',
    by_cashier_json    TEXT NOT NULL DEFAULT '[]',
    low_stock_count    INTEGER DEFAULT 0,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (shop_id, date)
);
"""

_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS owners (
    id             TEXT PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shops (
    id                TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    shop_name         TEXT NOT NULL,
    shop_key_hash     TEXT NOT NULL,
    low_stock_json    TEXT NOT NULL DEFAULT '[]',
    low_stock_count   INTEGER NOT NULL DEFAULT 0,
    inventory_json    TEXT NOT NULL DEFAULT '[]',
    users_json        TEXT NOT NULL DEFAULT '[]',
    created_at        TIMESTAMP NOT NULL DEFAULT now(),
    last_seen         TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales (
    id              BIGSERIAL PRIMARY KEY,
    shop_id         TEXT NOT NULL,
    remote_sale_id  BIGINT NOT NULL,
    invoice_number  TEXT,
    customer_name   TEXT,
    cashier         TEXT,
    subtotal        DOUBLE PRECISION,
    discount        DOUBLE PRECISION,
    total           DOUBLE PRECISION,
    amount_paid     DOUBLE PRECISION,
    change_given    DOUBLE PRECISION,
    payment_method  TEXT,
    status          TEXT,
    notes           TEXT,
    items_json      TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT,
    received_at     TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE(shop_id, remote_sale_id)
);
CREATE INDEX IF NOT EXISTS idx_sales_shop_created ON sales(shop_id, created_at DESC);

CREATE TABLE IF NOT EXISTS movements (
    id              BIGSERIAL PRIMARY KEY,
    shop_id         TEXT NOT NULL,
    remote_move_id  BIGINT NOT NULL,
    product_name    TEXT,
    movement_type   TEXT,
    quantity        DOUBLE PRECISION,
    reference       TEXT,
    notes           TEXT,
    created_at      TEXT,
    received_at     TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE(shop_id, remote_move_id)
);
CREATE INDEX IF NOT EXISTS idx_moves_shop_created ON movements(shop_id, created_at DESC);

CREATE TABLE IF NOT EXISTS snapshots (
    shop_id            TEXT NOT NULL,
    date               TEXT NOT NULL,
    total_revenue      DOUBLE PRECISION DEFAULT 0,
    num_sales          INTEGER DEFAULT 0,
    total_discounts    DOUBLE PRECISION DEFAULT 0,
    total_collected    DOUBLE PRECISION DEFAULT 0,
    by_payment_json    TEXT NOT NULL DEFAULT '[]',
    by_cashier_json    TEXT NOT NULL DEFAULT '[]',
    low_stock_count    INTEGER DEFAULT 0,
    updated_at         TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (shop_id, date)
);
"""


def _table_exists(table):
    if USE_POSTGRES:
        row = query_one("SELECT to_regclass(?) as t", (table,))
        return bool(row and row.get("t"))
    row = query_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return row is not None


def _column_exists(table, column):
    if USE_POSTGRES:
        row = query_one(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=? AND column_name=?",
            (table, column),
        )
        return row is not None
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return any(r["name"] == column for r in cur.fetchall())


def _migrate_old_single_owner_shops():
    """
    Earlier versions of this relay stored admin_email/admin_password_hash
    directly on the shops table (one shop per login, no owners table).
    If that shape is detected, migrate every existing shop into the new
    owners/shops split - preserving shop_id, shop_key, and all synced
    data - rather than losing whatever was already registered.
    """
    if not _table_exists("shops") or not _column_exists("shops", "admin_email"):
        return  # fresh install already on the new schema - nothing to do

    old_shops = query_all(
        "SELECT id, admin_email, admin_password_hash FROM shops WHERE owner_id IS NULL"
    )
    if not old_shops:
        return  # already migrated (or nothing to migrate) - nothing left to do

    print(f"[migration] old single-owner shops schema detected - "
          f"migrating {len(old_shops)} shop(s) to the owners/shops model")

    for s in old_shops:
        owner = query_one("SELECT id FROM owners WHERE email=?", (s["admin_email"],))
        owner_id = owner["id"] if owner else secrets.token_hex(12)
        if not owner:
            execute("INSERT INTO owners (id, email, password_hash) VALUES (?, ?, ?)",
                    (owner_id, s["admin_email"], s["admin_password_hash"]))
        execute("UPDATE shops SET owner_id=? WHERE id=?", (owner_id, s["id"]))

    # The old admin_email/admin_password_hash columns are left in place,
    # unused, rather than dropped: SQLite refuses to drop a
    # UNIQUE-constrained column (admin_email) without a much more
    # involved rebuild-the-table dance, and a couple of harmless leftover
    # columns cost nothing functionally.
    print("[migration] done (old admin_email/admin_password_hash columns "
          "left in place, unused, but harmless)")


def init_db():
    schema = _SCHEMA_POSTGRES if USE_POSTGRES else _SCHEMA_SQLITE
    with get_conn() as conn:
        if USE_POSTGRES:
            conn.cursor().execute(schema)
        else:
            conn.executescript(schema)

    # Add owner_id/inventory_json/users_json to a shops table that existed
    # before those columns did, then move old email/password data over.
    if not _column_exists("shops", "owner_id"):
        execute("ALTER TABLE shops ADD COLUMN owner_id TEXT")
    if not _column_exists("shops", "inventory_json"):
        execute("ALTER TABLE shops ADD COLUMN inventory_json TEXT NOT NULL DEFAULT '[]'")
    if not _column_exists("shops", "users_json"):
        execute("ALTER TABLE shops ADD COLUMN users_json TEXT NOT NULL DEFAULT '[]'")

    _migrate_old_single_owner_shops()

    execute("CREATE INDEX IF NOT EXISTS idx_shops_owner ON shops(owner_id)")
