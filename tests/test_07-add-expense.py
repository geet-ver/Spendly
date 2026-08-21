"""
Tests for Step 7: Add Expense
(.claude/specs/07-add-expense.md)

Scope (per spec):
- `GET /expenses/add` and `POST /expenses/add` are login-required.
- GET renders `add_expense.html` with an amount/category/date/description
  form; the category `<select>` must offer exactly the 7 fixed categories
  (Food, Transport, Bills, Health, Entertainment, Shopping, Other); the date
  field defaults to today.
- POST validates:
    * amount   -> required, parsed with float(), must be > 0
    * category -> required, must be one of the 7 fixed categories
    * date     -> required, must parse as YYYY-MM-DD
    * description -> optional; stripped; stored as NULL when blank
  On any validation error the form is re-rendered (200) with an error
  message and the previously submitted values pre-filled. No row is
  written to the DB in that case.
- On success, a new row is inserted via `insert_expense` and the response
  redirects (302) to `/profile`.

These tests exercise the feature purely through the Flask test client and
by querying the sqlite file that `database.db.get_db()` points at (per test,
via the `app`/`client`/`login` fixtures in tests/conftest.py, which
monkeypatch `DB_PATH` to a fresh tmp file and reseed the demo user). No
assertion here is derived from reading how `app.py` implements the route --
only from the spec's stated routes/validation rules/DoD checklist.

Import discipline: `tests/conftest.py`'s `app` fixture pops `app`,
`database.db`, and `database` from `sys.modules` and re-imports them with a
monkeypatched `DB_PATH` *after* the fixture starts running. Any test that
needs `get_db()` / `insert_expense()` therefore imports them lazily, inside
the test function body, never at module level -- a module-level import
would bind to the stale pre-patch module and read/write the wrong (or a
nonexistent) database file.
"""

import re

import pytest


# --------------------------------------------------------------------- #
# Local helpers (independent of the feature implementation)             #
# --------------------------------------------------------------------- #

DEMO_EMAIL = "demo@spendly.com"

CATEGORIES = [
    "Food",
    "Transport",
    "Bills",
    "Health",
    "Entertainment",
    "Shopping",
    "Other",
]

VALID_PAYLOAD = {
    "amount": "50.00",
    "category": "Food",
    "date": "2026-03-20",
    "description": "Lunch",
}


def _get_demo_user_id():
    """Look up the seeded demo user's id. Imports get_db lazily (see module
    docstring) so this only ever touches the per-test tmp DB."""
    from database.db import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT id FROM users WHERE email = ?", (DEMO_EMAIL,)
    ).fetchone()
    conn.close()
    return row["id"]


def _count_expenses(user_id):
    from database.db import get_db

    conn = get_db()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM expenses WHERE user_id = ?", (user_id,)
    ).fetchone()["n"]
    conn.close()
    return n


def _fetch_expense(user_id, date, description=None, category=None):
    from database.db import get_db

    conn = get_db()
    if description is not None:
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? AND date = ? AND description = ?",
            (user_id, date, description),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? AND date = ? AND category = ?",
            (user_id, date, category),
        ).fetchone()
    conn.close()
    return row


def _looks_like_validation_error(body):
    """The spec only requires *some* user-visible error message on a failed
    validation -- it does not mandate specific wording or markup, so this
    looks for common error-ish language rather than one exact string."""
    lowered = body.lower()
    keywords = ("error", "invalid", "required", "must be", "please enter", "please select")
    return any(kw in lowered for kw in keywords)


def _extract_select_block(body, field_name):
    match = re.search(
        rf'<select[^>]*name=["\']{field_name}["\'][^>]*>(.*?)</select>',
        body,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else None


# --------------------------------------------------------------------- #
# Unit tests: database/db.py -> insert_expense                         #
# --------------------------------------------------------------------- #

class TestInsertExpenseUnit:
    def test_insert_expense_with_valid_data_is_queryable(self, app):
        from database.db import get_db, insert_expense

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

        assert row is not None
        assert row["description"] is None, (
            "description should be stored as NULL when None is passed"
        )


# --------------------------------------------------------------------- #
# Auth guard: both GET and POST must require login                     #
# --------------------------------------------------------------------- #

class TestAddExpenseAuthGuard:
    def test_get_unauthenticated_redirects_to_login(self, client):
        response = client.get("/expenses/add")
        assert response.status_code == 302, "Unauthenticated GET must redirect, not render the form"
        assert "/login" in response.headers["Location"]

    def test_post_unauthenticated_redirects_to_login(self, client):
        response = client.post("/expenses/add", data=VALID_PAYLOAD)
        assert response.status_code == 302, "Unauthenticated POST must redirect, not process the form"
        assert "/login" in response.headers["Location"]

    def test_post_unauthenticated_does_not_write_to_db(self, client):
        from database.db import get_db

        conn = get_db()
        before = conn.execute("SELECT COUNT(*) AS n FROM expenses").fetchone()["n"]
        conn.close()

        client.post("/expenses/add", data=VALID_PAYLOAD)

        conn = get_db()
        after = conn.execute("SELECT COUNT(*) AS n FROM expenses").fetchone()["n"]
        conn.close()

        assert after == before, "An unauthenticated POST must never insert an expense row"


# --------------------------------------------------------------------- #
# GET /expenses/add -- authenticated                                    #
# --------------------------------------------------------------------- #

class TestAddExpenseGetRoute:
    def test_get_authenticated_returns_200_with_post_form(self, client, login):
        login()

        response = client.get("/expenses/add")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "<form" in body, "Expected the add-expense page to contain a <form"
        assert re.search(r'method=["\']post["\']', body, re.IGNORECASE), (
            "Expected the form to submit via POST"
        )

    def test_get_authenticated_form_has_required_fields(self, client, login):
        login()

        response = client.get("/expenses/add")
        body = response.get_data(as_text=True)

        for field_name in ("amount", "category", "date", "description"):
            assert re.search(rf'name=["\']{field_name}["\']', body), (
                f"Expected a form field named '{field_name}'"
            )

    def test_get_authenticated_category_select_has_exactly_seven_options(self, client, login):
        login()

        response = client.get("/expenses/add")
        body = response.get_data(as_text=True)

        select_block = _extract_select_block(body, "category")
        assert select_block is not None, "Expected a <select name=\"category\"> element"

        for category in CATEGORIES:
            assert category in select_block, f"Expected category option '{category}' in the select"

        option_count = len(re.findall(r"<option", select_block, re.IGNORECASE))
        assert option_count == len(CATEGORIES), (
            f"Expected exactly {len(CATEGORIES)} category options, found {option_count}"
        )

    def test_get_authenticated_date_field_defaults_to_today(self, client, login):
        from datetime import date

        login()

        response = client.get("/expenses/add")
        body = response.get_data(as_text=True)

        today_iso = date.today().isoformat()
        assert today_iso in body, "Expected the date field to default to today's date"


# --------------------------------------------------------------------- #
# POST /expenses/add -- validation errors                              #
# --------------------------------------------------------------------- #

class TestAddExpensePostValidation:
    @pytest.mark.parametrize(
        "field, bad_value",
        [
            pytest.param("amount", "", id="missing_amount"),
            pytest.param("amount", "0", id="zero_amount"),
            pytest.param("amount", "-15.00", id="negative_amount"),
            pytest.param("amount", "not-a-number", id="non_numeric_amount"),
            pytest.param("category", "NotARealCategory", id="invalid_category"),
            pytest.param("category", "", id="missing_category"),
            pytest.param("date", "20-March-2026", id="invalid_date_format"),
            pytest.param("date", "2026-13-40", id="invalid_date_value"),
            pytest.param("date", "", id="missing_date"),
        ],
    )
    def test_post_invalid_field_rerenders_form_with_error_and_no_db_write(
        self, client, login, field, bad_value
    ):
        login()
        user_id = _get_demo_user_id()
        before_count = _count_expenses(user_id)

        payload = dict(VALID_PAYLOAD)
        payload[field] = bad_value

        response = client.post("/expenses/add", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200, (
            f"Expected the form to be re-rendered (200) for invalid {field}={bad_value!r}"
        )
        assert "<form" in body
        assert _looks_like_validation_error(body), (
            f"Expected a visible error message for invalid {field}={bad_value!r}"
        )

        after_count = _count_expenses(user_id)
        assert after_count == before_count, (
            "A validation failure must not insert an expense row"
        )

    def test_post_missing_amount_preserves_other_submitted_values(self, client, login):
        login()

        payload = {
            "amount": "",
            "category": "Bills",
            "date": "2026-05-05",
            "description": "Should be retained",
        }
        response = client.post("/expenses/add", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Bills" in body, "Expected the previously submitted category to be pre-filled"
        assert "2026-05-05" in body, "Expected the previously submitted date to be pre-filled"
        assert "Should be retained" in body, (
            "Expected the previously submitted description to be pre-filled"
        )

    def test_post_invalid_category_preserves_other_submitted_values(self, client, login):
        login()

        payload = {
            "amount": "88.25",
            "category": "NotARealCategory",
            "date": "2026-06-06",
            "description": "Keep me",
        }
        response = client.post("/expenses/add", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "88.25" in body, "Expected the previously submitted amount to be pre-filled"
        assert "2026-06-06" in body, "Expected the previously submitted date to be pre-filled"
        assert "Keep me" in body, "Expected the previously submitted description to be pre-filled"

    def test_post_invalid_date_preserves_other_submitted_values(self, client, login):
        login()

        payload = {
            "amount": "42.10",
            "category": "Health",
            "date": "not-a-date",
            "description": "Keep me too",
        }
        response = client.post("/expenses/add", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "42.10" in body, "Expected the previously submitted amount to be pre-filled"
        assert "Health" in body, "Expected the previously submitted category to be pre-filled"
        assert "Keep me too" in body, "Expected the previously submitted description to be pre-filled"


# --------------------------------------------------------------------- #
# POST /expenses/add -- happy path and optional description            #
# --------------------------------------------------------------------- #

class TestAddExpensePostHappyPath:
    def test_post_valid_data_redirects_to_profile(self, client, login):
        login()

        response = client.post("/expenses/add", data=VALID_PAYLOAD)

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

    def test_post_valid_data_persists_expense_with_correct_fields(self, client, login):
        login()
        user_id = _get_demo_user_id()

        response = client.post("/expenses/add", data=VALID_PAYLOAD)
        assert response.status_code == 302

        row = _fetch_expense(
            user_id, VALID_PAYLOAD["date"], description=VALID_PAYLOAD["description"]
        )
        assert row is not None, "Expected the new expense to be persisted for the logged-in user"
        assert row["amount"] == float(VALID_PAYLOAD["amount"])
        assert row["category"] == VALID_PAYLOAD["category"]
        assert row["user_id"] == user_id

    def test_post_no_description_persists_null_description(self, client, login):
        login()
        user_id = _get_demo_user_id()

        payload = {"amount": "75.50", "category": "Shopping", "date": "2026-04-01"}
        response = client.post("/expenses/add", data=payload)

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

        row = _fetch_expense(user_id, "2026-04-01", category="Shopping")
        assert row is not None, "Expected the expense with no description to be persisted"
        assert row["description"] is None, "description should be NULL when omitted"

    def test_post_blank_whitespace_description_persists_null_description(self, client, login):
        login()
        user_id = _get_demo_user_id()

        payload = {
            "amount": "18.00",
            "category": "Other",
            "date": "2026-04-02",
            "description": "   ",
        }
        response = client.post("/expenses/add", data=payload)

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

        row = _fetch_expense(user_id, "2026-04-02", category="Other")
        assert row is not None
        assert row["description"] is None, (
            "A whitespace-only description should be stripped and stored as NULL"
        )

    def test_post_valid_data_increments_expense_count_by_one(self, client, login):
        login()
        user_id = _get_demo_user_id()
        before_count = _count_expenses(user_id)

        response = client.post("/expenses/add", data=VALID_PAYLOAD)
        assert response.status_code == 302

        after_count = _count_expenses(user_id)
        assert after_count == before_count + 1, "Exactly one new expense row should be inserted"


# --------------------------------------------------------------------- #
# Edge cases                                                            #
# --------------------------------------------------------------------- #

class TestAddExpenseEdgeCases:
    def test_post_sql_injection_style_description_is_stored_literally(self, client, login):
        login()
        user_id = _get_demo_user_id()

        malicious = "Lunch'); DROP TABLE expenses;--"
        payload = {
            "amount": "10.00",
            "category": "Food",
            "date": "2026-07-01",
            "description": malicious,
        }
        response = client.post("/expenses/add", data=payload)

        assert response.status_code == 302, (
            "A well-formed submission with a malicious-looking description must still succeed"
        )

        row = _fetch_expense(user_id, "2026-07-01", description=malicious)
        assert row is not None, (
            "Expected the expenses table to still exist and contain the new row"
        )
        assert row["description"] == malicious, (
            "The description must be stored literally via a parameterized query, "
            "never interpreted as SQL"
        )

    def test_post_sql_injection_style_category_is_rejected_as_invalid(self, client, login):
        login()

        payload = {
            "amount": "10.00",
            "category": "Food'; DROP TABLE expenses;--",
            "date": "2026-07-02",
            "description": "x",
        }
        response = client.post("/expenses/add", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200, "An unrecognized category must be rejected, not inserted"
        assert _looks_like_validation_error(body)

    def test_post_long_description_does_not_error(self, client, login):
        login()

        long_description = "A" * 500
        payload = {
            "amount": "10.00",
            "category": "Food",
            "date": "2026-07-03",
            "description": long_description,
        }
        response = client.post("/expenses/add", data=payload)

        assert response.status_code in (200, 302), (
            "A long description must not cause a server error (500)"
        )
