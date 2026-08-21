import math
import os
import sqlite3

from werkzeug.security import generate_password_hash

DB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "expense_tracker.db")
)

CATEGORIES = ["Food", "Transport", "Bills", "Health", "Entertainment", "Shopping", "Other"]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_user_by_email(email):
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    conn.close()
    return user


def create_user(name, email, password_hash):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        (name, email, password_hash),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def insert_expense(user_id, amount, category, date, description):
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO expenses (user_id, amount, category, date, description) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, category, date, description),
    )
    conn.commit()
    expense_id = cursor.lastrowid
    conn.close()
    return expense_id


def get_expense_by_id(expense_id, user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM expenses WHERE id = ? AND user_id = ?",
        (expense_id, user_id),
    ).fetchone()
    conn.close()
    return row


def update_expense(expense_id, user_id, amount, category, date, description):
    conn = get_db()
    conn.execute(
        "UPDATE expenses SET amount = ?, category = ?, date = ?, description = ? "
        "WHERE id = ? AND user_id = ?",
        (amount, category, date, description, expense_id, user_id),
    )
    conn.commit()
    conn.close()


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            amount      REAL NOT NULL,
            category    TEXT NOT NULL,
            date        TEXT NOT NULL,
            description TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()


def seed_db():
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if existing > 0:
        conn.close()
        return

    password_hash = generate_password_hash("demo123")
    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Demo User", "demo@spendly.com", password_hash),
    )
    user_id = cursor.lastrowid

    sample_expenses = [
        (user_id, 12.50, "Food", "2026-08-01", "Coffee and bagel"),
        (user_id, 45.00, "Transport", "2026-08-03", "Monthly metro pass"),
        (user_id, 120.00, "Bills", "2026-08-05", "Electricity bill"),
        (user_id, 60.00, "Health", "2026-08-07", "Pharmacy"),
        (user_id, 25.00, "Entertainment", "2026-08-10", "Movie tickets"),
        (user_id, 80.00, "Shopping", "2026-08-12", "New shoes"),
        (user_id, 15.00, "Other", "2026-08-14", "Miscellaneous"),
        (user_id, 32.75, "Food", "2026-08-16", "Groceries"),
    ]
    conn.executemany(
        "INSERT INTO expenses (user_id, amount, category, date, description) VALUES (?, ?, ?, ?, ?)",
        sample_expenses,
    )
    conn.commit()
    conn.close()


def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def _user_date_filter(user_id, date_from, date_to):
    where = "WHERE user_id = ?"
    params = [user_id]
    if date_from and date_to:
        where += " AND date BETWEEN ? AND ?"
        params += [date_from, date_to]
    return where, params


def get_summary_stats(user_id, date_from=None, date_to=None):
    conn = get_db()
    where, params = _user_date_filter(user_id, date_from, date_to)
    totals = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS cnt FROM expenses {where}",
        params,
    ).fetchone()
    top = conn.execute(
        f"SELECT category, SUM(amount) AS total FROM expenses {where} "
        "GROUP BY category ORDER BY total DESC LIMIT 1",
        params,
    ).fetchone()
    conn.close()
    return {
        "total_spent": totals["total"],
        "transaction_count": totals["cnt"],
        "top_category": top["category"] if top else "—",
    }


def get_recent_transactions(user_id, date_from=None, date_to=None, limit=10):
    conn = get_db()
    where, params = _user_date_filter(user_id, date_from, date_to)
    params.append(limit)
    rows = conn.execute(
        f"SELECT id, date, description, category, amount FROM expenses {where} "
        "ORDER BY date DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_category_breakdown(user_id, date_from=None, date_to=None):
    conn = get_db()
    where, params = _user_date_filter(user_id, date_from, date_to)
    rows = conn.execute(
        f"SELECT category, SUM(amount) AS amount FROM expenses {where} "
        "GROUP BY category ORDER BY amount DESC",
        params,
    ).fetchall()
    conn.close()
    if not rows:
        return []
    total = sum(r["amount"] for r in rows)
    breakdown = [
        {
            "name": r["category"],
            "amount": r["amount"],
            "percent": math.floor((r["amount"] / total) * 100 + 0.5),
        }
        for r in rows
    ]
    remainder = 100 - sum(c["percent"] for c in breakdown)
    if remainder:
        max(breakdown, key=lambda c: c["amount"])["percent"] += remainder
    return breakdown
