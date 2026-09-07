"""
Database layer for Bookstore Agent using Python's built-in sqlite3.
Zero external dependencies required.
Handles persistence for:
  - Users (authentication, credentials, profiles)
  - Carts (session-bound shopping carts)
  - Orders (persisted order history & cancellation tracking)
  - Token Blacklist (revoked JWT identifiers)
"""
from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# In production (Fargate) DB_PATH is set to an EFS-mounted path via environment variable.
# Locally it defaults to bookstore.db beside this file.
DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).parent / "bookstore.db")))


class _DBConnection:
    """Context manager that opens, yields, and always closes a sqlite3 connection."""

    def __init__(self) -> None:
        self._conn: sqlite3.Connection = sqlite3.connect(str(DB_PATH))
        self._conn.row_factory = sqlite3.Row

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()

    # Delegate attribute access so callers that do conn.execute() outside a
    # with-block still work (backward-compat for direct usage).
    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def get_db_connection() -> "_DBConnection":
    return _DBConnection()


def init_db() -> None:
    """Initialize SQLite database tables if they do not exist."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Users Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                full_name TEXT,
                phone_number TEXT,
                hashed_password TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_verified INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        """)

        # 2. Token Blacklist Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_blacklist (
                jti TEXT PRIMARY KEY,
                revoked_at TEXT NOT NULL
            )
        """)

        # 3. Carts Table (session_id -> items)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS carts (
                session_id TEXT NOT NULL,
                book_id TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (session_id, book_id)
            )
        """)

        # 4. Orders Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                items_json TEXT NOT NULL,
                delivery_address TEXT NOT NULL,
                total REAL NOT NULL,
                status TEXT NOT NULL,
                estimated_delivery TEXT NOT NULL,
                placed_at TEXT NOT NULL
            )
        """)
        conn.commit()


# Initialize database on module import
init_db()


# ── User DB Operations ────────────────────────────────────────────────────────
def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            d = dict(row)
            d["is_active"] = bool(d["is_active"])
            d["is_verified"] = bool(d["is_verified"])
            return d
        return None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row:
            d = dict(row)
            d["is_active"] = bool(d["is_active"])
            d["is_verified"] = bool(d["is_verified"])
            return d
        return None


def save_user(user: Dict[str, Any]) -> None:
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO users (id, email, full_name, phone_number, hashed_password, is_active, is_verified, created_at, last_login)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user["id"],
            user["email"],
            user.get("full_name"),
            user.get("phone_number"),
            user["hashed_password"],
            1 if user.get("is_active", True) else 0,
            1 if user.get("is_verified", True) else 0,
            user["created_at"],
            user.get("last_login"),
        ))
        conn.commit()


def update_user_last_login(email: str, last_login: str) -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE email = ?", (last_login, email))
        conn.commit()


# ── Blacklist Operations ──────────────────────────────────────────────────────
def is_jti_blacklisted(jti: str) -> bool:
    with get_db_connection() as conn:
        row = conn.execute("SELECT 1 FROM token_blacklist WHERE jti = ?", (jti,)).fetchone()
        return row is not None


def blacklist_jti(jti: str, revoked_at: str) -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO token_blacklist (jti, revoked_at) VALUES (?, ?)", (jti, revoked_at))
        conn.commit()


# ── Cart DB Operations ────────────────────────────────────────────────────────
def get_cart_items(session_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT book_id, title, author, price, quantity FROM carts WHERE session_id = ?", (session_id,)).fetchall()
        return [dict(r) for r in rows]


def save_cart_item(session_id: str, item: Dict[str, Any], quantity: int = 1) -> None:
    with get_db_connection() as conn:
        existing = conn.execute("SELECT quantity FROM carts WHERE session_id = ? AND book_id = ?", (session_id, item["book_id"])).fetchone()
        if existing:
            new_qty = existing["quantity"] + quantity
            conn.execute("UPDATE carts SET quantity = ? WHERE session_id = ? AND book_id = ?", (new_qty, session_id, item["book_id"]))
        else:
            conn.execute("""
                INSERT INTO carts (session_id, book_id, title, author, price, quantity)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, item["book_id"], item["title"], item["author"], item["price"], quantity))
        conn.commit()


def remove_cart_item(session_id: str, book_id: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.execute("DELETE FROM carts WHERE session_id = ? AND book_id = ?", (session_id, book_id))
        conn.commit()
        return cursor.rowcount > 0


def clear_cart(session_id: str) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM carts WHERE session_id = ?", (session_id,))
        conn.commit()


# ── Order DB Operations ───────────────────────────────────────────────────────
def save_order(order: Dict[str, Any]) -> None:
    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO orders (order_id, session_id, items_json, delivery_address, total, status, estimated_delivery, placed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order["order_id"],
            order["session_id"],
            json.dumps(order["items"]),
            order["delivery_address"],
            order["total"],
            order["status"],
            order["estimated_delivery"],
            order["placed_at"],
        ))
        conn.commit()


def get_orders_by_session(session_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM orders WHERE session_id = ? ORDER BY placed_at DESC", (session_id,)).fetchall()
        orders = []
        for r in rows:
            d = dict(r)
            d["items"] = json.loads(d["items_json"])
            del d["items_json"]
            orders.append(d)
        return orders


def get_order_by_id(session_id: str, order_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM orders WHERE session_id = ? AND order_id = ?", (session_id, order_id)).fetchone()
        if row:
            d = dict(row)
            d["items"] = json.loads(d["items_json"])
            del d["items_json"]
            return d
        return None


def update_order_status(order_id: str, status: str) -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
        conn.commit()
