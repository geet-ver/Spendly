"""
Tests for Step 8: Edit Expense
(.claude/specs/08-edit-expenses.md)

Scope (per spec):
- `GET /expenses/<id>/edit` and `POST /expenses/<id>/edit` are login-required.
- GET loads the expense via `get_expense_by_id(expense_id, user_id)` (scoped to
  the logged-in user's own rows) and renders `edit_expense.html` pre-filled
  with the expense's current amount/category/date/description; the category
  `<select>` must have the current category pre-selected.
- Ownership is enforced two ways: `get_expense_by_id` only returns a row that
  belongs to the requesting user, and `update_expense`'s `UPDATE` is scoped to
  `id = ? AND user_id = ?`. Either a non-existent id or an id owned by another
  user must 404 -- for both GET and POST.
- POST validates the submission with the same rules as Add Expense:
    * amount      -> required, parsed with float(), must be > 0
    * category    -> required, must be one of the 7 fixed categories
    * date        -> required, must parse as YYYY-MM-DD
    * description -> optional; stripped; stored as NULL when blank
  On any validation error the form is re-rendered (200) with an error message
  and the *submitted* (not the original DB) values pre-filled. No row is
  changed in the DB in that case.
- On success, the row is updated in place via `update_expense` (no new row is
  inserted) and the response redirects (302) to `/profile`; the updated
  values must then show up in the profile page's transaction table.

Spec note: the spec text names `database/queries.py` for the new helpers, but
this codebase (and CLAUDE.md) only has `database/db.py` -- the actual
implementation added `get_expense_by_id` and `update_expense` there,
alongside `insert_expense`, `get_recent_transactions`, etc. These tests
import from `database.db`, not `database.queries`.

These tests exercise the feature purely through the Flask test client and by
querying the sqlite file that `database.db.get_db()` points at (per test, via
the `app`/`client`/`login` fixtures in tests/conftest.py, which monkeypatch
`DB_PATH` to a fresh tmp file and reseed the demo user). No assertion here is
derived from reading how `app.py` implements the route -- only from the
spec's stated routes/validation rules/DoD checklist.

Import discipline: `tests/conftest.py`'s `app` fixture pops `app`,
`database.db`, and `database` from `sys.modules` and re-imports them with a
monkeypatched `DB_PATH` *after* the fixture starts running. Any test that
needs `get_db()` / `get_expense_by_id()` / `update_expense()` therefore
imports them lazily, inside the test function body, never at module level --
a module-level import would bind to the stale pre-patch module and read/write
the wrong (or a nonexistent) database file.
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

# One of the 8 seeded demo expenses (seed_db(): 2026-08-16, Food, 32.75,
# "Groceries"). Chosen because its amount has a non-trivial decimal, making
# "unchanged vs. updated" comparisons unambiguous.
DEMO_EXPENSE = {
    "amount": 32.75,
    "category": "Food",
    "date": "2026-08-16",
    "description": "Groceries",
}

# Deliberately different from DEMO_EXPENSE in every field, so pre-fill /
# retention assertions can't pass by accident.
VALID_UPDATE_PAYLOAD = {
    "amount": "77.00",
    "category": "Transport",
    "date": "2026-01-15",
    "description": "Updated desc",
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


def _get_demo_expense_id(user_id):
    """Look up the id of the seeded demo expense matching DEMO_EXPENSE."""
    from database.db import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT id FROM expenses WHERE user_id = ? AND description = ?",
        (user_id, DEMO_EXPENSE["description"]),
    ).fetchone()
    conn.close()
    return row["id"]


def _fetch_expense_row(expense_id):
    from database.db import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM expenses WHERE id = ?", (expense_id,)
    ).fetchone()
    conn.close()
    return row


def _count_expenses(user_id):
    from database.db import get_db

    conn = get_db()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM expenses WHERE user_id = ?", (user_id,)
    ).fetchone()["n"]
    conn.close()
    return n


def _seed_second_user_with_expense():
    """Create a second user with a single expense, inserted directly via
    database.db (lazy-imported, see module docstring). Returns
    (other_user_id, other_expense_id, original_values_dict)."""
    from database.db import get_db, insert_expense

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Other User", "other-user@example.com", "hash"),
    )
    conn.commit()
    other_user_id = cursor.lastrowid
    conn.close()

    original_values = {
        "amount": 200.0,
        "category": "Bills",
        "date": "2026-02-02",
        "description": "Other user's rent",
    }
    other_expense_id = insert_expense(
        user_id=other_user_id,
        amount=original_values["amount"],
        category=original_values["category"],
        date=original_values["date"],
        description=original_values["description"],
    )
    return other_user_id, other_expense_id, original_values


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


def _selected_option_values(select_block):
    """Return the list of <option> values that carry a `selected` attribute
    within the given <select> inner HTML."""
    pattern = re.compile(
        r'<option\s+value=["\'](?P<value>[^"\']*)["\'][^>]*>', re.IGNORECASE
    )
    return [
        m.group("value")
        for m in pattern.finditer(select_block)
        if "selected" in m.group(0).lower()
    ]


# --------------------------------------------------------------------- #
# Unit tests: database/db.py -> get_expense_by_id / update_expense     #
# --------------------------------------------------------------------- #

class TestEditExpenseDbHelpersUnit:
    def test_get_expense_by_id_returns_row_for_owner(self, app):
        from database.db import get_expense_by_id

        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        row = get_expense_by_id(expense_id, user_id)

        assert row is not None, "Expected the owner's expense to be returned"
        assert row["id"] == expense_id
        assert row["user_id"] == user_id
        assert row["amount"] == DEMO_EXPENSE["amount"]
        assert row["category"] == DEMO_EXPENSE["category"]
        assert row["date"] == DEMO_EXPENSE["date"]
        assert row["description"] == DEMO_EXPENSE["description"]

    def test_get_expense_by_id_returns_none_for_wrong_user(self, app):
        from database.db import get_expense_by_id

        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        other_user_id, _other_expense_id, _values = _seed_second_user_with_expense()

        row = get_expense_by_id(expense_id, other_user_id)

        assert row is None, (
            "get_expense_by_id must return None when the expense belongs to "
            "a different user"
        )

    def test_get_expense_by_id_returns_none_for_nonexistent_id(self, app):
        from database.db import get_expense_by_id

        user_id = _get_demo_user_id()

        row = get_expense_by_id(999999, user_id)

        assert row is None, "get_expense_by_id must return None for an unknown id"

    def test_update_expense_updates_row_for_owner(self, app):
        from database.db import update_expense

        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        update_expense(
            expense_id,
            user_id,
            amount=99.0,
            category="Health",
            date="2026-05-05",
            description="Updated via unit test",
        )

        row = _fetch_expense_row(expense_id)
        assert row["amount"] == 99.0
        assert row["category"] == "Health"
        assert row["date"] == "2026-05-05"
        assert row["description"] == "Updated via unit test"
        assert row["user_id"] == user_id, "Ownership must not change"

    def test_update_expense_is_noop_for_wrong_user(self, app):
        from database.db import update_expense

        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        other_user_id, _other_expense_id, _values = _seed_second_user_with_expense()

        # Attempting to update the demo user's expense while impersonating
        # another user id must not raise and must not change the row.
        update_expense(
            expense_id,
            other_user_id,
            amount=1.0,
            category="Other",
            date="2020-01-01",
            description="Should not stick",
        )

        row = _fetch_expense_row(expense_id)
        assert row["amount"] == DEMO_EXPENSE["amount"], (
            "update_expense must not modify a row that doesn't belong to the "
            "given user_id"
        )
        assert row["category"] == DEMO_EXPENSE["category"]
        assert row["date"] == DEMO_EXPENSE["date"]
        assert row["description"] == DEMO_EXPENSE["description"]


# --------------------------------------------------------------------- #
# Auth guard: both GET and POST must require login                     #
# --------------------------------------------------------------------- #

class TestEditExpenseAuthGuard:
    def test_get_unauthenticated_redirects_to_login(self, client):
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.get(f"/expenses/{expense_id}/edit")

        assert response.status_code == 302, "Unauthenticated GET must redirect, not render the form"
        assert "/login" in response.headers["Location"]

    def test_post_unauthenticated_redirects_to_login(self, client):
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(f"/expenses/{expense_id}/edit", data=VALID_UPDATE_PAYLOAD)

        assert response.status_code == 302, "Unauthenticated POST must redirect, not process the form"
        assert "/login" in response.headers["Location"]

    def test_post_unauthenticated_does_not_modify_expense(self, client):
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        client.post(f"/expenses/{expense_id}/edit", data=VALID_UPDATE_PAYLOAD)

        row = _fetch_expense_row(expense_id)
        assert row["amount"] == DEMO_EXPENSE["amount"], (
            "An unauthenticated POST must never modify an expense row"
        )
        assert row["category"] == DEMO_EXPENSE["category"]
        assert row["date"] == DEMO_EXPENSE["date"]
        assert row["description"] == DEMO_EXPENSE["description"]


# --------------------------------------------------------------------- #
# 404 handling: nonexistent id and other-user's id                      #
# --------------------------------------------------------------------- #

class TestEditExpense404Handling:
    def test_get_nonexistent_id_returns_404(self, client, login):
        login()

        response = client.get("/expenses/999999/edit")

        assert response.status_code == 404

    def test_post_nonexistent_id_returns_404(self, client, login):
        login()

        response = client.post("/expenses/999999/edit", data=VALID_UPDATE_PAYLOAD)

        assert response.status_code == 404

    def test_get_other_users_expense_returns_404(self, client, login):
        login()
        _other_user_id, other_expense_id, _values = _seed_second_user_with_expense()

        response = client.get(f"/expenses/{other_expense_id}/edit")

        assert response.status_code == 404, (
            "A user must not be able to view another user's expense edit form"
        )

    def test_post_other_users_expense_returns_404_and_leaves_row_unchanged(self, client, login):
        login()
        _other_user_id, other_expense_id, original_values = _seed_second_user_with_expense()

        response = client.post(f"/expenses/{other_expense_id}/edit", data=VALID_UPDATE_PAYLOAD)

        assert response.status_code == 404, (
            "A user must not be able to edit another user's expense"
        )

        row = _fetch_expense_row(other_expense_id)
        assert row["amount"] == original_values["amount"], (
            "A 404'd POST must not modify the other user's row"
        )
        assert row["category"] == original_values["category"]
        assert row["date"] == original_values["date"]
        assert row["description"] == original_values["description"]


# --------------------------------------------------------------------- #
# GET /expenses/<id>/edit -- authenticated, own expense                 #
# --------------------------------------------------------------------- #

class TestEditExpenseGetRoute:
    def test_get_own_expense_returns_200_with_post_form(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.get(f"/expenses/{expense_id}/edit")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "<form" in body, "Expected the edit-expense page to contain a <form"
        assert re.search(r'method=["\']post["\']', body, re.IGNORECASE), (
            "Expected the form to submit via POST"
        )

    def test_get_own_expense_form_prefilled_with_current_values(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.get(f"/expenses/{expense_id}/edit")
        body = response.get_data(as_text=True)

        assert str(DEMO_EXPENSE["amount"]) in body, (
            "Expected the current amount to be pre-filled"
        )
        assert DEMO_EXPENSE["date"] in body, "Expected the current date to be pre-filled"
        assert DEMO_EXPENSE["description"] in body, (
            "Expected the current description to be pre-filled"
        )

    def test_get_own_expense_category_select_has_correct_option_selected(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.get(f"/expenses/{expense_id}/edit")
        body = response.get_data(as_text=True)

        select_block = _extract_select_block(body, "category")
        assert select_block is not None, "Expected a <select name=\"category\"> element"

        for category in CATEGORIES:
            assert category in select_block, f"Expected category option '{category}' present"

        selected = _selected_option_values(select_block)
        assert selected == [DEMO_EXPENSE["category"]], (
            f"Expected exactly '{DEMO_EXPENSE['category']}' to be pre-selected, got {selected!r}"
        )

    def test_get_own_expense_form_action_targets_expense_id(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.get(f"/expenses/{expense_id}/edit")
        body = response.get_data(as_text=True)

        assert re.search(
            rf'action=["\']/expenses/{expense_id}/edit["\']', body
        ), f"Expected the form action to target /expenses/{expense_id}/edit"


# --------------------------------------------------------------------- #
# POST /expenses/<id>/edit -- validation errors                         #
# --------------------------------------------------------------------- #

class TestEditExpensePostValidation:
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
    def test_post_invalid_field_rerenders_form_with_error_and_no_db_change(
        self, client, login, field, bad_value
    ):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        payload = dict(VALID_UPDATE_PAYLOAD)
        payload[field] = bad_value

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200, (
            f"Expected the form to be re-rendered (200) for invalid {field}={bad_value!r}"
        )
        assert "<form" in body
        assert _looks_like_validation_error(body), (
            f"Expected a visible error message for invalid {field}={bad_value!r}"
        )

        # The DB row must be completely unchanged -- not partially applied.
        row = _fetch_expense_row(expense_id)
        assert row["amount"] == DEMO_EXPENSE["amount"], (
            "A validation failure must not modify the expense row"
        )
        assert row["category"] == DEMO_EXPENSE["category"]
        assert row["date"] == DEMO_EXPENSE["date"]
        assert row["description"] == DEMO_EXPENSE["description"]

        # The other (valid) fields the user just submitted must be retained
        # in the re-rendered form -- and they differ from the original DB
        # values, so this also proves the form isn't just re-showing the
        # untouched DB row.
        for other_field, other_value in VALID_UPDATE_PAYLOAD.items():
            if other_field == field:
                continue
            assert other_value in body, (
                f"Expected previously submitted {other_field}={other_value!r} "
                "to be retained in the re-rendered form"
            )


# --------------------------------------------------------------------- #
# POST /expenses/<id>/edit -- happy path                                #
# --------------------------------------------------------------------- #

class TestEditExpensePostHappyPath:
    def test_post_valid_data_redirects_to_profile(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(f"/expenses/{expense_id}/edit", data=VALID_UPDATE_PAYLOAD)

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

    def test_post_valid_data_persists_updated_fields(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(f"/expenses/{expense_id}/edit", data=VALID_UPDATE_PAYLOAD)
        assert response.status_code == 302

        row = _fetch_expense_row(expense_id)
        assert row["amount"] == float(VALID_UPDATE_PAYLOAD["amount"])
        assert row["category"] == VALID_UPDATE_PAYLOAD["category"]
        assert row["date"] == VALID_UPDATE_PAYLOAD["date"]
        assert row["description"] == VALID_UPDATE_PAYLOAD["description"]
        assert row["user_id"] == user_id, "Ownership must be preserved"

    def test_post_valid_data_does_not_change_expense_row_count(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        before_count = _count_expenses(user_id)

        response = client.post(f"/expenses/{expense_id}/edit", data=VALID_UPDATE_PAYLOAD)
        assert response.status_code == 302

        after_count = _count_expenses(user_id)
        assert after_count == before_count, (
            "Editing an expense must update the existing row, not insert a new one"
        )

    def test_post_blank_description_persists_null(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        payload = dict(VALID_UPDATE_PAYLOAD)
        payload["description"] = ""

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

        row = _fetch_expense_row(expense_id)
        assert row["description"] is None, (
            "A blank description should be stripped and stored as NULL"
        )
        assert row["amount"] == float(payload["amount"])
        assert row["category"] == payload["category"]
        assert row["date"] == payload["date"]

    def test_post_valid_data_reflected_on_profile_page_after_redirect(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(
            f"/expenses/{expense_id}/edit",
            data=VALID_UPDATE_PAYLOAD,
            follow_redirects=True,
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert VALID_UPDATE_PAYLOAD["description"] in body, (
            "Expected the updated description to appear in the transactions table"
        )
        assert VALID_UPDATE_PAYLOAD["category"] in body, (
            "Expected the updated category to appear in the transactions table"
        )
        assert VALID_UPDATE_PAYLOAD["date"] in body, (
            "Expected the updated date to appear in the transactions table"
        )
        # profile.html formats amounts with "%.2f", so "77.00" (not "77.0").
        assert f'{float(VALID_UPDATE_PAYLOAD["amount"]):.2f}' in body, (
            "Expected the updated amount to appear in the transactions table"
        )


# --------------------------------------------------------------------- #
# Cross-user isolation                                                  #
# --------------------------------------------------------------------- #

class TestEditExpenseCrossUserIsolation:
    def test_editing_own_expense_does_not_modify_other_users_expense_row(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        other_user_id, other_expense_id, original_values = _seed_second_user_with_expense()

        response = client.post(f"/expenses/{expense_id}/edit", data=VALID_UPDATE_PAYLOAD)
        assert response.status_code == 302

        other_row = _fetch_expense_row(other_expense_id)
        assert other_row["amount"] == original_values["amount"], (
            "Editing your own expense must not touch another user's row"
        )
        assert other_row["category"] == original_values["category"]
        assert other_row["date"] == original_values["date"]
        assert other_row["description"] == original_values["description"]
        assert other_row["user_id"] == other_user_id

    def test_editing_own_expense_does_not_appear_in_other_users_transactions(self, client, login):
        from database.db import get_recent_transactions

        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        other_user_id, _other_expense_id, original_values = _seed_second_user_with_expense()

        response = client.post(f"/expenses/{expense_id}/edit", data=VALID_UPDATE_PAYLOAD)
        assert response.status_code == 302

        other_transactions = get_recent_transactions(other_user_id)
        descriptions = [tx["description"] for tx in other_transactions]
        assert VALID_UPDATE_PAYLOAD["description"] not in descriptions, (
            "The other user's transaction list must not be affected by editing "
            "the demo user's expense"
        )
        assert original_values["description"] in descriptions, (
            "The other user's original expense must remain in their transaction list"
        )
