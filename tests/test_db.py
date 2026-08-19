import sqlite3

import pytest

from database import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_expense_tracker.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    return db_file


def test_get_db_enables_foreign_keys(temp_db):
    conn = db.get_db()
    result = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert result == 1
    conn.close()


def test_get_db_row_factory(temp_db):
    conn = db.get_db()
    assert conn.row_factory is sqlite3.Row
    conn.close()


def test_init_db_creates_tables(temp_db):
    db.init_db()
    conn = db.get_db()
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"users", "expenses"}.issubset(tables)
    conn.close()


def test_init_db_is_idempotent(temp_db):
    db.init_db()
    db.init_db()


def test_seed_db_inserts_demo_data(temp_db):
    db.init_db()
    db.seed_db()
    conn = db.get_db()
    user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    expense_count = conn.execute("SELECT COUNT(*) AS n FROM expenses").fetchone()["n"]
    assert user_count == 1
    assert expense_count == 8
    conn.close()


def test_seed_db_is_idempotent(temp_db):
    db.init_db()
    db.seed_db()
    db.seed_db()
    conn = db.get_db()
    user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    assert user_count == 1
    conn.close()


def test_email_unique_constraint(temp_db):
    db.init_db()
    conn = db.get_db()
    conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("A", "dup@example.com", "hash1"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("B", "dup@example.com", "hash2"),
        )
        conn.commit()
    conn.close()


def test_expense_requires_valid_user_fk(temp_db):
    db.init_db()
    conn = db.get_db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO expenses (user_id, amount, category, date) VALUES (?, ?, ?, ?)",
            (9999, 10.0, "Food", "2026-08-20"),
        )
        conn.commit()
    conn.close()
