"""
Tests for Step 8: Edit Expense
(.claude/specs/08-edit-expenses.md)

Scope, derived strictly from the spec (not from reading edit_expense()'s
implementation in app.py):

- `GET /expenses/<id>/edit` and `POST /expenses/<id>/edit` require login;
  unauthenticated requests to either must redirect to `/login`.
- GET loads the target expense scoped to the logged-in user and renders a
  form pre-filled with its current amount/category/date/description; the
  category `<select>` must have the current category pre-selected.
- Ownership is enforced: a nonexistent expense id, or one that belongs to a
  different user, must 404 for both GET and POST -- it must never be
  editable and must never leak into the response.
- POST validates the submission with the same rules as Add Expense:
    * amount      -> required, parsed with float(), must be > 0
    * category    -> required, must be one of the 7 fixed categories
    * date        -> required, must parse as YYYY-MM-DD
    * description -> optional; stripped; stored as NULL when blank
  On any validation error, the form is re-rendered (200) with a visible
  error message and the *submitted* (not original) values pre-filled; the
  DB row must be left completely unchanged.
- On success, the existing row is updated in place (no new row inserted),
  the response redirects (302) to `/profile`, and the updated values show
  up in the profile page's transaction table, including an "Edit" link per
  row pointing at `/expenses/<id>/edit`.

Spec note: the spec text names `database/queries.py` for the new helpers,
but this codebase (per CLAUDE.md) keeps all DB helpers in `database/db.py`.
These tests import `get_expense_by_id` / `update_expense` from
`database.db`, matching the codebase's actual module layout, while treating
the spec (not the route implementation) as the source of truth for expected
*behavior*.

Import discipline: `tests/conftest.py`'s `app` fixture pops `app`,
`database.db`, and `database` from `sys.modules` and re-imports them with a
monkeypatched `DB_PATH` *after* the fixture starts running. Any test that
needs `get_db()` / `get_expense_by_id()` / `update_expense()` therefore
imports them lazily, inside the test function body, never at module level --
a module-level import would bind to the stale pre-patch module and read/write
the wrong (or a nonexistent) database file.

This file is intentionally independent from tests/test_edit_expense.py
(a prior pass at this same spec) -- it is not a modification of that file.
"""

import re

import pytest


# --------------------------------------------------------------------- #
# Constants / fixtures shared across this module                        #
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

# One of the 8 seeded demo expenses: 2026-08-12, Shopping, 80.00, "New shoes".
SEEDED_EXPENSE = {
    "amount": 80.00,
    "category": "Shopping",
    "date": "2026-08-12",
    "description": "New shoes",
}

# Deliberately differs from SEEDED_EXPENSE in every field so that
# pre-fill / "did it actually change" assertions can't pass by accident.
UPDATE_PAYLOAD = {
    "amount": "123.45",
    "category": "Entertainment",
    "date": "2026-02-14",
    "description": "Concert tickets",
}


# --------------------------------------------------------------------- #
# Local helpers (independent of the feature implementation)             #
# --------------------------------------------------------------------- #

def _get_user_id(email=DEMO_EMAIL):
    from database.db import get_db

    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row["id"]


def _get_expense_id_by_description(user_id, description):
    from database.db import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT id FROM expenses WHERE user_id = ? AND description = ?",
        (user_id, description),
    ).fetchone()
    conn.close()
    return row["id"]


def _fetch_expense(expense_id):
    from database.db import get_db

    conn = get_db()
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    conn.close()
    return row


def _create_other_user_with_expense():
    """Seeds a second user and one expense belonging to them, entirely
    independent of the demo user. Returns (user_id, expense_id, values)."""
    from database.db import get_db, insert_expense

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
        ("Second User", "second-user@example.com", "not-a-real-hash"),
    )
    conn.commit()
    other_user_id = cursor.lastrowid
    conn.close()

    values = {
        "amount": 999.99,
        "category": "Bills",
        "date": "2026-03-03",
        "description": "Other user's expense",
    }
    expense_id = insert_expense(
        user_id=other_user_id,
        amount=values["amount"],
        category=values["category"],
        date=values["date"],
        description=values["description"],
    )
    return other_user_id, expense_id, values


def _looks_like_validation_error(body):
    """The spec requires *some* visible error message on validation failure
    but doesn't mandate exact wording, so match common error-ish language."""
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
    pattern = re.compile(r'<option\s+value=["\'](?P<value>[^"\']*)["\'][^>]*>', re.IGNORECASE)
    return [
        m.group("value")
        for m in pattern.finditer(select_block)
        if "selected" in m.group(0).lower()
    ]


# --------------------------------------------------------------------- #
# Auth guard                                                            #
# --------------------------------------------------------------------- #

class TestEditExpenseAuthGuard:
    def test_get_edit_unauthenticated_redirects_to_login(self, client):
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.get(f"/expenses/{expense_id}/edit")

        assert response.status_code == 302, "Unauthenticated GET must redirect, never render the form"
        assert "/login" in response.headers["Location"]

    def test_post_edit_unauthenticated_redirects_to_login(self, client):
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.post(f"/expenses/{expense_id}/edit", data=UPDATE_PAYLOAD)

        assert response.status_code == 302, "Unauthenticated POST must redirect, never process the form"
        assert "/login" in response.headers["Location"]

    def test_post_edit_unauthenticated_does_not_modify_row(self, client):
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        client.post(f"/expenses/{expense_id}/edit", data=UPDATE_PAYLOAD)

        row = _fetch_expense(expense_id)
        assert row["amount"] == SEEDED_EXPENSE["amount"], (
            "An unauthenticated POST must never modify an expense row"
        )
        assert row["category"] == SEEDED_EXPENSE["category"]
        assert row["date"] == SEEDED_EXPENSE["date"]
        assert row["description"] == SEEDED_EXPENSE["description"]


# --------------------------------------------------------------------- #
# 404 / ownership enforcement                                           #
# --------------------------------------------------------------------- #

class TestEditExpenseOwnershipAnd404:
    def test_get_nonexistent_expense_returns_404(self, client, login):
        login()

        response = client.get("/expenses/987654/edit")

        assert response.status_code == 404, "Editing a nonexistent expense id must 404"

    def test_post_nonexistent_expense_returns_404(self, client, login):
        login()

        response = client.post("/expenses/987654/edit", data=UPDATE_PAYLOAD)

        assert response.status_code == 404, "Posting an edit for a nonexistent id must 404"

    def test_get_other_users_expense_returns_404(self, client, login):
        login()
        _other_user_id, other_expense_id, _values = _create_other_user_with_expense()

        response = client.get(f"/expenses/{other_expense_id}/edit")

        assert response.status_code == 404, (
            "A logged-in user must not be able to view another user's expense edit form"
        )

    def test_get_other_users_expense_body_does_not_leak_their_data(self, client, login):
        login()
        _other_user_id, other_expense_id, values = _create_other_user_with_expense()

        response = client.get(f"/expenses/{other_expense_id}/edit")
        body = response.get_data(as_text=True)

        assert values["description"] not in body, (
            "A 404 response must not leak the other user's expense data"
        )

    def test_post_other_users_expense_returns_404(self, client, login):
        login()
        _other_user_id, other_expense_id, _values = _create_other_user_with_expense()

        response = client.post(f"/expenses/{other_expense_id}/edit", data=UPDATE_PAYLOAD)

        assert response.status_code == 404, (
            "A logged-in user must not be able to edit another user's expense"
        )

    def test_post_other_users_expense_leaves_row_unchanged(self, client, login):
        login()
        _other_user_id, other_expense_id, values = _create_other_user_with_expense()

        client.post(f"/expenses/{other_expense_id}/edit", data=UPDATE_PAYLOAD)

        row = _fetch_expense(other_expense_id)
        assert row["amount"] == values["amount"], (
            "A 404'd POST against another user's expense must not modify it"
        )
        assert row["category"] == values["category"]
        assert row["date"] == values["date"]
        assert row["description"] == values["description"]


# --------------------------------------------------------------------- #
# GET /expenses/<id>/edit -- authenticated, own expense                 #
# --------------------------------------------------------------------- #

class TestEditExpenseGetRoute:
    def test_get_own_expense_returns_200_with_form(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.get(f"/expenses/{expense_id}/edit")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "<form" in body, "Expected the edit-expense page to contain a <form"
        assert re.search(r'method=["\']post["\']', body, re.IGNORECASE), (
            "Expected the form to submit via POST"
        )

    def test_get_own_expense_prefills_current_amount_date_description(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.get(f"/expenses/{expense_id}/edit")
        body = response.get_data(as_text=True)

        assert str(SEEDED_EXPENSE["amount"]) in body, "Expected the current amount to be pre-filled"
        assert SEEDED_EXPENSE["date"] in body, "Expected the current date to be pre-filled"
        assert SEEDED_EXPENSE["description"] in body, "Expected the current description to be pre-filled"

    def test_get_own_expense_category_select_has_all_seven_and_correct_selection(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.get(f"/expenses/{expense_id}/edit")
        body = response.get_data(as_text=True)

        select_block = _extract_select_block(body, "category")
        assert select_block is not None, 'Expected a <select name="category"> element'

        for category in CATEGORIES:
            assert category in select_block, f"Expected category option '{category}' present"

        selected = _selected_option_values(select_block)
        assert selected == [SEEDED_EXPENSE["category"]], (
            f"Expected '{SEEDED_EXPENSE['category']}' to be the only pre-selected option, got {selected!r}"
        )

    def test_get_own_expense_form_action_targets_correct_expense_id(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.get(f"/expenses/{expense_id}/edit")
        body = response.get_data(as_text=True)

        assert re.search(rf'action=["\']/expenses/{expense_id}/edit["\']', body), (
            f"Expected the form action to target /expenses/{expense_id}/edit"
        )


# --------------------------------------------------------------------- #
# POST /expenses/<id>/edit -- validation errors                         #
# --------------------------------------------------------------------- #

class TestEditExpensePostValidation:
    @pytest.mark.parametrize(
        "field, bad_value",
        [
            pytest.param("amount", "", id="missing_amount"),
            pytest.param("amount", "0", id="zero_amount"),
            pytest.param("amount", "-5.00", id="negative_amount"),
            pytest.param("amount", "not-a-number", id="non_numeric_amount"),
            pytest.param("category", "NotARealCategory", id="invalid_category"),
            pytest.param("category", "", id="missing_category"),
            pytest.param("date", "14-Feb-2026", id="invalid_date_format"),
            pytest.param("date", "2026-02-31", id="invalid_date_value"),
            pytest.param("date", "", id="missing_date"),
        ],
    )
    def test_post_invalid_field_rerenders_form_and_leaves_row_unchanged(
        self, client, login, field, bad_value
    ):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = dict(UPDATE_PAYLOAD)
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

        row = _fetch_expense(expense_id)
        assert row["amount"] == SEEDED_EXPENSE["amount"], "A validation failure must not change the row"
        assert row["category"] == SEEDED_EXPENSE["category"]
        assert row["date"] == SEEDED_EXPENSE["date"]
        assert row["description"] == SEEDED_EXPENSE["description"]

    def test_post_invalid_field_repopulates_submitted_not_original_values(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = {
            "amount": "",  # triggers validation error
            "category": "Health",
            "date": "2026-09-09",
            "description": "Freshly typed description",
        }
        response = client.post(f"/expenses/{expense_id}/edit", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Health" in body, "Expected the submitted category to be retained"
        assert "2026-09-09" in body, "Expected the submitted date to be retained"
        assert "Freshly typed description" in body, "Expected the submitted description to be retained"

    def test_post_missing_amount_returns_error_message(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = dict(UPDATE_PAYLOAD)
        payload["amount"] = ""

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _looks_like_validation_error(body)

    def test_post_zero_amount_returns_error_message(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = dict(UPDATE_PAYLOAD)
        payload["amount"] = "0"

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _looks_like_validation_error(body)

    def test_post_non_numeric_amount_returns_error_message(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = dict(UPDATE_PAYLOAD)
        payload["amount"] = "twelve dollars"

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _looks_like_validation_error(body)

    def test_post_invalid_category_returns_error_message(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = dict(UPDATE_PAYLOAD)
        payload["category"] = "Vacation"

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _looks_like_validation_error(body)

    def test_post_invalid_date_returns_error_message(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = dict(UPDATE_PAYLOAD)
        payload["date"] = "not-a-real-date"

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert _looks_like_validation_error(body)


# --------------------------------------------------------------------- #
# POST /expenses/<id>/edit -- happy path                                #
# --------------------------------------------------------------------- #

class TestEditExpensePostHappyPath:
    def test_post_valid_data_redirects_to_profile(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.post(f"/expenses/{expense_id}/edit", data=UPDATE_PAYLOAD)

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

    def test_post_valid_data_updates_row_in_place(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.post(f"/expenses/{expense_id}/edit", data=UPDATE_PAYLOAD)
        assert response.status_code == 302

        row = _fetch_expense(expense_id)
        assert row["amount"] == float(UPDATE_PAYLOAD["amount"])
        assert row["category"] == UPDATE_PAYLOAD["category"]
        assert row["date"] == UPDATE_PAYLOAD["date"]
        assert row["description"] == UPDATE_PAYLOAD["description"]
        assert row["user_id"] == user_id, "Ownership must be preserved across the update"

    def test_post_valid_data_does_not_insert_a_new_row(self, client, login):
        from database.db import get_db

        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        conn = get_db()
        before_count = conn.execute(
            "SELECT COUNT(*) AS n FROM expenses WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        conn.close()

        response = client.post(f"/expenses/{expense_id}/edit", data=UPDATE_PAYLOAD)
        assert response.status_code == 302

        conn = get_db()
        after_count = conn.execute(
            "SELECT COUNT(*) AS n FROM expenses WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        conn.close()

        assert after_count == before_count, (
            "Editing an expense must update the existing row, not create a new one"
        )

    def test_post_blank_description_persists_as_null(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = dict(UPDATE_PAYLOAD)
        payload["description"] = ""

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

        row = _fetch_expense(expense_id)
        assert row["description"] is None, "A blank description must be stored as NULL"
        assert row["amount"] == float(payload["amount"])
        assert row["category"] == payload["category"]
        assert row["date"] == payload["date"]

    def test_post_whitespace_only_description_persists_as_null(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = dict(UPDATE_PAYLOAD)
        payload["description"] = "    "

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)

        assert response.status_code == 302
        row = _fetch_expense(expense_id)
        assert row["description"] is None, (
            "A whitespace-only description must be stripped and stored as NULL"
        )

    def test_post_omitted_description_field_persists_as_null(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        payload = {
            "amount": UPDATE_PAYLOAD["amount"],
            "category": UPDATE_PAYLOAD["category"],
            "date": UPDATE_PAYLOAD["date"],
            # no "description" key at all
        }

        response = client.post(f"/expenses/{expense_id}/edit", data=payload)

        assert response.status_code == 302
        row = _fetch_expense(expense_id)
        assert row["description"] is None

    def test_post_valid_data_appears_on_profile_page_after_redirect(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.post(
            f"/expenses/{expense_id}/edit", data=UPDATE_PAYLOAD, follow_redirects=True
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert UPDATE_PAYLOAD["description"] in body, (
            "Expected the updated description to appear in the profile transaction list"
        )
        assert UPDATE_PAYLOAD["category"] in body, (
            "Expected the updated category to appear in the profile transaction list"
        )
        assert UPDATE_PAYLOAD["date"] in body, (
            "Expected the updated date to appear in the profile transaction list"
        )
        assert f'{float(UPDATE_PAYLOAD["amount"]):.2f}' in body, (
            "Expected the updated amount to appear in the profile transaction list"
        )


# --------------------------------------------------------------------- #
# Cross-user isolation on write                                         #
# --------------------------------------------------------------------- #

class TestEditExpenseCrossUserIsolation:
    def test_editing_own_expense_never_touches_another_users_row(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])
        _other_user_id, other_expense_id, other_values = _create_other_user_with_expense()

        response = client.post(f"/expenses/{expense_id}/edit", data=UPDATE_PAYLOAD)
        assert response.status_code == 302

        other_row = _fetch_expense(other_expense_id)
        assert other_row["amount"] == other_values["amount"]
        assert other_row["category"] == other_values["category"]
        assert other_row["date"] == other_values["date"]
        assert other_row["description"] == other_values["description"]


# --------------------------------------------------------------------- #
# Profile page: Edit link per transaction row                           #
# --------------------------------------------------------------------- #

class TestProfilePageEditLinks:
    def test_profile_transaction_row_contains_edit_link_to_correct_url(self, client, login):
        login()
        user_id = _get_user_id()
        expense_id = _get_expense_id_by_description(user_id, SEEDED_EXPENSE["description"])

        response = client.get("/profile")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert f"/expenses/{expense_id}/edit" in body, (
            "Expected an Edit link on the profile page pointing at the expense's edit URL"
        )

    def test_profile_transactions_table_has_actions_column_header(self, client, login):
        login()

        response = client.get("/profile")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert re.search(r"<th[^>]*>\s*Actions\s*</th>", body, re.IGNORECASE), (
            "Expected an 'Actions' column header in the transactions table"
        )

    def test_profile_page_has_one_edit_link_per_seeded_expense(self, client, login):
        from database.db import get_db

        login()
        user_id = _get_user_id()

        conn = get_db()
        expense_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM expenses WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]
        conn.close()

        response = client.get("/profile")
        body = response.get_data(as_text=True)

        for expense_id in expense_ids:
            assert f"/expenses/{expense_id}/edit" in body, (
                f"Expected an edit link for expense id {expense_id} on the profile page"
            )
