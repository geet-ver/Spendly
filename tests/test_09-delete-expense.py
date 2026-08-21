"""
Tests for Step 9: Delete Expense
(.claude/specs/09-delete-expense.md)

Scope (per spec):
- The stub `GET /expenses/<id>/delete` route is replaced by a POST-only
  `POST /expenses/<int:expense_id>/delete` route.
- The route is login-required: an unauthenticated POST must redirect (302)
  to `/login` and must not delete anything.
- Ownership is enforced the same way as edit: the expense is looked up via
  `get_expense_by_id(expense_id, user_id)` scoped to the logged-in user; a
  missing id or an id owned by another user must 404, and the row must
  remain in the DB untouched.
- On success (own expense, valid id): the row is permanently removed from
  the DB and the response redirects (302) to `/profile`.
- GET is not an allowed method for this route -- it must respond 405 (the
  stub's plain-GET behavior is gone).
- After a successful delete, the deleted expense must no longer appear in
  the "Recent transactions" table on `/profile`, and the summary stats
  (transaction count / total spent) and category breakdown must reflect
  the removal.
- Deleting one user's expense must never affect another user's expenses,
  row counts, or transaction lists.

These tests exercise the feature purely through the Flask test client and
by querying the sqlite file that `database.db.get_db()` points at (per
test, via the `app`/`client`/`login` fixtures in tests/conftest.py, which
monkeypatch `DB_PATH` to a fresh tmp file and reseed the demo user). No
assertion here is derived from reading how `app.py` or `database/db.py`
implements the route/helper -- only from the spec's stated routes,
ownership rules, and Definition of Done checklist.

Import discipline: `tests/conftest.py`'s `app` fixture pops `app`,
`database.db`, and `database` from `sys.modules` and re-imports them with a
monkeypatched `DB_PATH` *after* the fixture starts running. Any test that
needs `get_db()` / `get_expense_by_id()` / `delete_expense()` /
`insert_expense()` therefore imports them lazily, inside the test function
body, never at module level -- a module-level import would bind to the
stale pre-patch module and read/write the wrong (or a nonexistent)
database file.
"""

import pytest


# --------------------------------------------------------------------- #
# Local helpers (independent of the feature implementation)             #
# --------------------------------------------------------------------- #

DEMO_EMAIL = "demo@spendly.com"

# One of the 8 seeded demo expenses (seed_db(): 2026-08-16, Food, 32.75,
# "Groceries"). Chosen because it is unique among the seeded rows, making
# "still present / no longer present" assertions unambiguous.
DEMO_EXPENSE = {
    "amount": 32.75,
    "category": "Food",
    "date": "2026-08-16",
    "description": "Groceries",
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


# --------------------------------------------------------------------- #
# Unit tests: database/db.py -> delete_expense                         #
# --------------------------------------------------------------------- #

class TestDeleteExpenseDbHelperUnit:
    def test_delete_expense_removes_row_for_owner(self, app):
        from database.db import delete_expense

        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        delete_expense(expense_id, user_id)

        row = _fetch_expense_row(expense_id)
        assert row is None, "Expected the expense row to be gone after delete_expense"

    def test_delete_expense_is_noop_for_wrong_user(self, app):
        from database.db import delete_expense

        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        other_user_id, _other_expense_id, _values = _seed_second_user_with_expense()

        # Attempting to delete the demo user's expense while impersonating
        # another user id must not raise and must not remove the row.
        delete_expense(expense_id, other_user_id)

        row = _fetch_expense_row(expense_id)
        assert row is not None, (
            "delete_expense must not remove a row that doesn't belong to the "
            "given user_id"
        )
        assert row["amount"] == DEMO_EXPENSE["amount"]
        assert row["category"] == DEMO_EXPENSE["category"]
        assert row["date"] == DEMO_EXPENSE["date"]
        assert row["description"] == DEMO_EXPENSE["description"]

    def test_delete_expense_nonexistent_id_does_not_raise(self, app):
        from database.db import delete_expense

        user_id = _get_demo_user_id()

        # Deleting an id that doesn't exist at all must be a safe no-op.
        delete_expense(999999, user_id)

        assert _count_expenses(user_id) == 8, (
            "Deleting a nonexistent id must not change the demo user's row count"
        )


# --------------------------------------------------------------------- #
# Method guard: GET must not be accepted                                #
# --------------------------------------------------------------------- #

class TestDeleteExpenseMethodGuard:
    def test_get_is_not_allowed(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.get(f"/expenses/{expense_id}/delete")

        assert response.status_code == 405, (
            "GET must no longer be accepted on the delete route (the stub's "
            "plain-GET behavior must be gone)"
        )

        # A rejected GET must never delete the row.
        row = _fetch_expense_row(expense_id)
        assert row is not None, "A 405'd GET must not delete the expense"

    def test_get_is_not_allowed_when_logged_out(self, client):
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.get(f"/expenses/{expense_id}/delete")

        # Flask's method routing (405) takes precedence over auth for a
        # method the route was never registered for.
        assert response.status_code in (302, 405), (
            "A GET to the delete route must not succeed -- expect either a "
            "login redirect or a 405 Method Not Allowed"
        )
        if response.status_code == 302:
            assert "/login" in response.headers["Location"]

        row = _fetch_expense_row(expense_id)
        assert row is not None, "A rejected GET must not delete the expense"


# --------------------------------------------------------------------- #
# Auth guard: unauthenticated POST must redirect to /login               #
# --------------------------------------------------------------------- #

class TestDeleteExpenseAuthGuard:
    def test_post_unauthenticated_redirects_to_login(self, client):
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(f"/expenses/{expense_id}/delete")

        assert response.status_code == 302, (
            "Unauthenticated POST must redirect, not process the delete"
        )
        assert "/login" in response.headers["Location"]

    def test_post_unauthenticated_does_not_delete_expense(self, client):
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        client.post(f"/expenses/{expense_id}/delete")

        row = _fetch_expense_row(expense_id)
        assert row is not None, (
            "An unauthenticated POST must never delete an expense row"
        )
        assert _count_expenses(user_id) == 8


# --------------------------------------------------------------------- #
# 404 handling: nonexistent id and other-user's id                      #
# --------------------------------------------------------------------- #

class TestDeleteExpense404Handling:
    def test_post_nonexistent_id_returns_404(self, client, login):
        login()

        response = client.post("/expenses/999999/delete")

        assert response.status_code == 404

    def test_post_nonexistent_id_does_not_change_row_count(self, client, login):
        login()
        user_id = _get_demo_user_id()
        before_count = _count_expenses(user_id)

        client.post("/expenses/999999/delete")

        assert _count_expenses(user_id) == before_count

    def test_post_other_users_expense_returns_404(self, client, login):
        login()
        _other_user_id, other_expense_id, _values = _seed_second_user_with_expense()

        response = client.post(f"/expenses/{other_expense_id}/delete")

        assert response.status_code == 404, (
            "A user must not be able to delete another user's expense"
        )

    def test_post_other_users_expense_leaves_row_intact(self, client, login):
        login()
        other_user_id, other_expense_id, original_values = _seed_second_user_with_expense()

        response = client.post(f"/expenses/{other_expense_id}/delete")
        assert response.status_code == 404

        row = _fetch_expense_row(other_expense_id)
        assert row is not None, (
            "A 404'd delete attempt must not remove the other user's row"
        )
        assert row["amount"] == original_values["amount"]
        assert row["category"] == original_values["category"]
        assert row["date"] == original_values["date"]
        assert row["description"] == original_values["description"]
        assert row["user_id"] == other_user_id
        assert _count_expenses(other_user_id) == 1


# --------------------------------------------------------------------- #
# Happy path: deleting your own expense                                 #
# --------------------------------------------------------------------- #

class TestDeleteExpenseHappyPath:
    def test_post_own_expense_redirects_to_profile(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(f"/expenses/{expense_id}/delete")

        assert response.status_code == 302
        assert "/profile" in response.headers["Location"]

    def test_post_own_expense_removes_row_from_db(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(f"/expenses/{expense_id}/delete")
        assert response.status_code == 302

        row = _fetch_expense_row(expense_id)
        assert row is None, "Expected the expense row to be gone after a successful delete"

    def test_post_own_expense_decreases_row_count_by_one(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        before_count = _count_expenses(user_id)

        response = client.post(f"/expenses/{expense_id}/delete")
        assert response.status_code == 302

        after_count = _count_expenses(user_id)
        assert after_count == before_count - 1, (
            "Deleting an expense must decrease the user's row count by exactly one"
        )

    def test_post_own_expense_no_longer_in_recent_transactions(self, client, login):
        from database.db import get_recent_transactions

        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(f"/expenses/{expense_id}/delete")
        assert response.status_code == 302

        transactions = get_recent_transactions(user_id)
        ids = [tx["id"] for tx in transactions]
        descriptions = [tx["description"] for tx in transactions]
        assert expense_id not in ids
        assert DEMO_EXPENSE["description"] not in descriptions

    def test_post_own_expense_removed_from_profile_page_after_redirect(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(
            f"/expenses/{expense_id}/delete", follow_redirects=True
        )
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert DEMO_EXPENSE["description"] not in body, (
            "Expected the deleted expense's description to no longer appear "
            "in the 'Recent transactions' table on /profile"
        )

    def test_post_own_expense_updates_summary_stats(self, client, login):
        from database.db import get_summary_stats

        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        before_stats = get_summary_stats(user_id)

        response = client.post(f"/expenses/{expense_id}/delete")
        assert response.status_code == 302

        after_stats = get_summary_stats(user_id)

        assert after_stats["transaction_count"] == before_stats["transaction_count"] - 1, (
            "Transaction count must decrease by one after a successful delete"
        )
        assert after_stats["total_spent"] == pytest.approx(
            before_stats["total_spent"] - DEMO_EXPENSE["amount"]
        ), "Total spent must decrease by the deleted expense's amount"

    def test_post_own_expense_updates_category_breakdown(self, client, login):
        from database.db import get_category_breakdown

        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        before_breakdown = {
            c["name"]: c["amount"] for c in get_category_breakdown(user_id)
        }

        response = client.post(f"/expenses/{expense_id}/delete")
        assert response.status_code == 302

        after_breakdown = {
            c["name"]: c["amount"] for c in get_category_breakdown(user_id)
        }

        food_before = before_breakdown.get(DEMO_EXPENSE["category"], 0)
        food_after = after_breakdown.get(DEMO_EXPENSE["category"], 0)
        assert food_after == pytest.approx(food_before - DEMO_EXPENSE["amount"]), (
            "The category breakdown total for the deleted expense's category "
            "must decrease by its amount"
        )

    def test_post_own_expense_stats_reflected_on_profile_page(self, client, login):
        from database.db import get_summary_stats

        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)

        response = client.post(
            f"/expenses/{expense_id}/delete", follow_redirects=True
        )
        body = response.get_data(as_text=True)
        assert response.status_code == 200

        after_stats = get_summary_stats(user_id)
        assert f'{after_stats["total_spent"]:.2f}' in body, (
            "Expected the updated total spent to appear in the profile stats"
        )
        assert str(after_stats["transaction_count"]) in body, (
            "Expected the updated transaction count to appear in the profile stats"
        )


# --------------------------------------------------------------------- #
# Cross-user isolation                                                  #
# --------------------------------------------------------------------- #

class TestDeleteExpenseCrossUserIsolation:
    def test_deleting_own_expense_does_not_affect_other_users_row(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        other_user_id, other_expense_id, original_values = _seed_second_user_with_expense()

        response = client.post(f"/expenses/{expense_id}/delete")
        assert response.status_code == 302

        other_row = _fetch_expense_row(other_expense_id)
        assert other_row is not None, (
            "Deleting your own expense must not remove another user's row"
        )
        assert other_row["amount"] == original_values["amount"]
        assert other_row["category"] == original_values["category"]
        assert other_row["date"] == original_values["date"]
        assert other_row["description"] == original_values["description"]
        assert other_row["user_id"] == other_user_id

    def test_deleting_own_expense_does_not_change_other_users_row_count(self, client, login):
        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        other_user_id, _other_expense_id, _values = _seed_second_user_with_expense()
        before_other_count = _count_expenses(other_user_id)

        response = client.post(f"/expenses/{expense_id}/delete")
        assert response.status_code == 302

        assert _count_expenses(other_user_id) == before_other_count

    def test_deleting_own_expense_leaves_other_users_transactions_intact(self, client, login):
        from database.db import get_recent_transactions

        login()
        user_id = _get_demo_user_id()
        expense_id = _get_demo_expense_id(user_id)
        other_user_id, _other_expense_id, original_values = _seed_second_user_with_expense()

        response = client.post(f"/expenses/{expense_id}/delete")
        assert response.status_code == 302

        other_transactions = get_recent_transactions(other_user_id)
        descriptions = [tx["description"] for tx in other_transactions]
        assert original_values["description"] in descriptions, (
            "The other user's expense must remain untouched by deleting the "
            "demo user's expense"
        )
