import pytest


# ---------------------------------------------------------------------------
# Unit tests: database/db.py -> insert_expense
# ---------------------------------------------------------------------------
# These tests exercise insert_expense() directly against the sqlite file the
# `app` fixture points DB_PATH at (see tests/conftest.py). They don't touch
# the Flask app/routes at all.
#
# get_db/insert_expense are imported inside each test (after the `app` fixture
# has patched DB_PATH and reimported database.db) rather than at module level,
# since a module-level import would bind to the pre-patch module and connect
# to the wrong database file.

class TestInsertExpenseUnit:
    def test_insert_expense_with_valid_data_persists_row(self, app):
        from database.db import get_db, insert_expense

        # A user must exist first because expenses.user_id has a FK constraint.
        conn = get_db()
        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Unit Test User", "unit-test-user@example.com", "hash"),
        )
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()

        expense_id = insert_expense(
            user_id=user_id,
            amount=50.0,
            category="Food",
            date="2026-03-20",
            description="Lunch",
        )

        assert expense_id is not None, "insert_expense should return the new row's id"

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM expenses WHERE id = ?", (expense_id,)
        ).fetchone()
        conn.close()

        assert row is not None, "Expected the inserted expense row to exist in the DB"
        assert row["user_id"] == user_id
        assert row["amount"] == 50.0
        assert row["category"] == "Food"
        assert row["date"] == "2026-03-20"
        assert row["description"] == "Lunch"

    def test_insert_expense_with_none_description_stores_null(self, app):
        from database.db import get_db, insert_expense

        conn = get_db()
        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Unit Test User 2", "unit-test-user-2@example.com", "hash"),
        )
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()

        expense_id = insert_expense(
            user_id=user_id,
            amount=12.34,
            category="Transport",
            date="2026-01-01",
            description=None,
        )

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM expenses WHERE id = ?", (expense_id,)
        ).fetchone()
        conn.close()

        assert row is not None, "Expected the inserted expense row to exist in the DB"
        assert row["description"] is None, "description should be stored as NULL when None is passed"


# ---------------------------------------------------------------------------
# Route tests: GET/POST /expenses/add
# ---------------------------------------------------------------------------

class TestAddExpenseGetRoute:
    def test_get_unauthenticated_redirects_to_login(self, client):
        response = client.get("/expenses/add")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_get_authenticated_returns_form_with_all_categories(self, client, login):
        login()  # demo@spendly.com / demo123

        response = client.get("/expenses/add")
        body = response.get_data(as_text=True)

        assert response.status_code == 200

        assert "<form" in body, "Expected the add-expense page to contain a <form"
        assert 'method="POST"' in body or 'method="post"' in body, (
            "Expected the form to use the POST method"
        )

        for category in [
            "Food",
            "Transport",
            "Bills",
            "Health",
            "Entertainment",
            "Shopping",
            "Other",
        ]:
            assert category in body, f"Expected category option '{category}' in the form"


class TestAddExpensePostRoute:
    def test_post_unauthenticated_redirects_to_login(self, client):
        response = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_post_valid_data_redirects_to_profile_and_persists_expense(self, client, login):
        from database.db import get_db

        login_response = login()
        body = login_response.get_data(as_text=True)
        assert login_response.status_code == 200, "Login should succeed for demo user"

        conn = get_db()
        user = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("demo@spendly.com",)
        ).fetchone()
        conn.close()
        user_id = user["id"]

        response = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? AND date = ? AND description = ?",
            (user_id, "2026-03-20", "Lunch"),
        ).fetchone()
        conn.close()

        assert row is not None, "Expected the new expense to be persisted for the logged-in user"
        assert row["amount"] == 50.0
        assert row["category"] == "Food"

    def test_post_missing_amount_rerenders_form_with_error(self, client, login):
        login()

        response = client.post(
            "/expenses/add",
            data={
                "amount": "",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "<form" in body
        assert _has_error_message(body), "Expected an error message for missing amount"

    def test_post_zero_amount_rerenders_form_with_error(self, client, login):
        login()

        response = client.post(
            "/expenses/add",
            data={
                "amount": "0",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _has_error_message(body), "Expected an error message for zero amount"

    def test_post_negative_amount_rerenders_form_with_error(self, client, login):
        login()

        response = client.post(
            "/expenses/add",
            data={
                "amount": "-25.00",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _has_error_message(body), "Expected an error message for negative amount"

    def test_post_non_numeric_amount_rerenders_form_with_error(self, client, login):
        login()

        response = client.post(
            "/expenses/add",
            data={
                "amount": "not-a-number",
                "category": "Food",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _has_error_message(body), "Expected an error message for non-numeric amount"

    def test_post_invalid_category_rerenders_form_with_error(self, client, login):
        login()

        response = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "NotARealCategory",
                "date": "2026-03-20",
                "description": "Lunch",
            },
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _has_error_message(body), "Expected an error message for an invalid category"

    def test_post_invalid_date_rerenders_form_with_error(self, client, login):
        login()

        response = client.post(
            "/expenses/add",
            data={
                "amount": "50.0",
                "category": "Food",
                "date": "20-March-2026",
                "description": "Lunch",
            },
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _has_error_message(body), "Expected an error message for an invalid date string"

    def test_post_no_description_is_valid_and_stores_null(self, client, login):
        from database.db import get_db

        login()

        conn = get_db()
        user = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("demo@spendly.com",)
        ).fetchone()
        conn.close()
        user_id = user["id"]

        response = client.post(
            "/expenses/add",
            data={
                "amount": "75.50",
                "category": "Shopping",
                "date": "2026-04-01",
                # description intentionally omitted
            },
        )

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

        conn = get_db()
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? AND date = ? AND category = ?",
            (user_id, "2026-04-01", "Shopping"),
        ).fetchone()
        conn.close()

        assert row is not None, "Expected the expense with no description to be persisted"
        assert row["description"] is None, "description should be NULL when omitted"

    def test_post_validation_failure_preserves_submitted_values(self, client, login):
        login()

        response = client.post(
            "/expenses/add",
            data={
                "amount": "",
                "category": "Bills",
                "date": "2026-05-05",
                "description": "Should be retained",
            },
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Should be retained" in body, (
            "Expected previously submitted description to be pre-filled after a validation error"
        )
        assert "2026-05-05" in body, (
            "Expected previously submitted date to be pre-filled after a validation error"
        )


def _has_error_message(body):
    """Check that the add-expense form's error banner (class="auth-error") is present."""
    return "auth-error" in body
