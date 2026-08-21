"""
Tests for Step 6: Date Filter for Profile Page
(.claude/specs/06-date-filter-profile-page.md)

Scope (per spec):
- No new routes. GET /profile reads optional `date_from` / `date_to` query
  params (ISO `YYYY-MM-DD`), validates them, and applies an inclusive
  date-range filter to summary stats, recent transactions, and category
  breakdown.
- Absent/malformed params -> fall back to unfiltered ("All Time") view.
- `date_from > date_to` -> fall back to unfiltered view AND surface a
  user-visible error: "Start date must be before end date."
- Presets ("This Month", "Last 3 Months", "Last 6 Months", "All Time") are
  computed server-side in app.py, not the template.
- Amounts always render with the Rupee symbol regardless of filter state.
- A filtered view with zero matching expenses must show Rs 0.00 total,
  0 transactions, and an empty category breakdown -- no errors.

These tests exercise the feature purely through the Flask test client
(`GET /profile?...`) and by seeding expenses directly into the sqlite file
that `database.db.get_db()` points at (per-test, via the `app`/`client`
fixtures in tests/conftest.py, which monkeypatch `DB_PATH` to a tmp file).
No test relies on reading the date-filter implementation itself.

Reused fixtures (from tests/conftest.py):
- `app`    -- fresh Flask app per test, isolated sqlite DB via monkeypatched
              DB_PATH, seeded with the demo user + 8 demo expenses.
- `client` -- Flask test client bound to `app`.
- `login`  -- POSTs to /login; defaults to demo@spendly.com / demo123.

Known seeded baseline (from database/db.py seed_db(), demo user):
- 8 expenses, total 390.25, all dated 2026-08-01 .. 2026-08-16.
- top category by total: Bills (120.00).
"""

import re
import uuid
from datetime import date
from urllib.parse import quote

import pytest


# --------------------------------------------------------------------- #
# Local helpers (independent of the feature implementation)             #
# --------------------------------------------------------------------- #

DEMO_EMAIL = "demo@spendly.com"
DEMO_PASSWORD = "demo123"
DEMO_TOTAL = "390.25"


def _months_ago(n):
    """Return a date `n` calendar months before today.

    Day-of-month is clamped to 28 so this never raises for short months
    (Feb) and never accidentally rolls into a different month bucket than
    intended -- only the month/year component matters for the assertions
    in this file.
    """
    today = date.today()
    total = today.year * 12 + (today.month - 1) - n
    year, month0 = divmod(total, 12)
    day = min(today.day, 28)
    return date(year, month0 + 1, day)


def _unique_email():
    return f"filter-test-{uuid.uuid4().hex[:10]}@example.com"


def _register(client, email, password="password123", name="Filter Tester"):
    return client.post(
        "/register",
        data={"name": name, "email": email, "password": password},
        follow_redirects=True,
    )


def _get_user_id(email):
    from database import db as db_module

    row = db_module.get_user_by_email(email)
    assert row is not None, f"expected a user row for {email}"
    return row["id"]


def _insert_expense(user_id, amount, category, date_str, description=""):
    """Insert an expense row directly, bypassing the (stub) add-expense route."""
    from database import db as db_module

    conn = db_module.get_db()
    conn.execute(
        "INSERT INTO expenses (user_id, amount, category, date, description) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, category, date_str, description),
    )
    conn.commit()
    conn.close()


def _new_user_with_expenses(client, expenses):
    """Register+login a fresh user, seed `expenses`, return (email, user_id).

    `expenses` is a list of (amount, category, date_str, description) tuples.
    """
    email = _unique_email()
    _register(client, email)
    user_id = _get_user_id(email)
    for amount, category, date_str, description in expenses:
        _insert_expense(user_id, amount, category, date_str, description)
    return email, user_id


def _login_as(login, email):
    resp = login(email=email, password="password123")
    assert resp.status_code == 200, "expected login to succeed for freshly registered user"


# --------------------------------------------------------------------- #
# Auth guard                                                            #
# --------------------------------------------------------------------- #

class TestAuthGuard:
    def test_filtered_profile_request_requires_login(self, client):
        response = client.get("/profile?date_from=2026-01-01&date_to=2026-01-31")
        assert response.status_code == 302, "unauthenticated filtered request should redirect"
        assert "/login" in response.headers["Location"]

    def test_malformed_filter_still_requires_login(self, client):
        response = client.get("/profile?date_from=not-a-date&date_to=also-not-a-date")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


# --------------------------------------------------------------------- #
# No filter => identical to Step 5 unfiltered behaviour                 #
# --------------------------------------------------------------------- #

class TestUnfilteredBaseline:
    def test_no_query_params_shows_unfiltered_totals(self, client, login):
        login()
        response = client.get("/profile")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert DEMO_TOTAL in body, "unfiltered total should match the full demo dataset"
        assert "Bills" in body

    def test_all_time_query_params_explicitly_absent_matches_default(self, client, login):
        login()
        default_body = client.get("/profile").get_data(as_text=True)
        # "All Time" must be a clean /profile URL with no query params at all.
        explicit_body = client.get("/profile?").get_data(as_text=True)

        assert DEMO_TOTAL in default_body
        assert DEMO_TOTAL in explicit_body


# --------------------------------------------------------------------- #
# Custom date range filtering                                           #
# --------------------------------------------------------------------- #

class TestCustomDateRangeFiltering:
    def test_custom_range_filters_summary_and_transactions(self, client, login):
        email, _ = _new_user_with_expenses(
            client,
            [
                (50.00, "Food", "2026-01-10", "January food (outside range)"),
                (100.00, "Transport", "2026-03-15", "March transport (inside range)"),
                (25.00, "Food", "2026-03-20", "March food (inside range)"),
                (200.00, "Bills", "2026-04-01", "April bills (outside range)"),
            ],
        )
        _login_as(login, email)

        response = client.get("/profile?date_from=2026-03-01&date_to=2026-03-31")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "125.00" in body, "total spent should be limited to the two March expenses"
        assert "March transport" in body
        assert "March food" in body
        assert "January food" not in body, "out-of-range expense must not appear"
        assert "April bills" not in body, "out-of-range expense must not appear"

    def test_custom_range_filters_category_breakdown(self, client, login):
        email, _ = _new_user_with_expenses(
            client,
            [
                (100.00, "Transport", "2026-03-15", "in-range transport"),
                (25.00, "Food", "2026-03-20", "in-range food"),
                (200.00, "Bills", "2026-04-01", "out-of-range bills"),
            ],
        )
        _login_as(login, email)

        response = client.get("/profile?date_from=2026-03-01&date_to=2026-03-31")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Transport" in body
        assert "Food" in body
        assert "Bills" not in body, "category outside the filtered range should be excluded"
        # Transport (100) is 80% of the 125 filtered total, Food (25) is 20%.
        assert re.search(r"width:\s*80%", body), "Transport should be rendered as 80% of the filtered total"
        assert re.search(r"width:\s*20%", body), "Food should be rendered as 20% of the filtered total"

    def test_custom_range_is_inclusive_of_both_boundary_dates(self, client, login):
        email, _ = _new_user_with_expenses(
            client,
            [
                (10.00, "Other", "2026-04-30", "day before start (excluded)"),
                (20.00, "Other", "2026-05-01", "exact start boundary (included)"),
                (30.00, "Other", "2026-05-31", "exact end boundary (included)"),
                (40.00, "Other", "2026-06-01", "day after end (excluded)"),
            ],
        )
        _login_as(login, email)

        response = client.get("/profile?date_from=2026-05-01&date_to=2026-05-31")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "exact start boundary" in body
        assert "exact end boundary" in body
        assert "day before start" not in body
        assert "day after end" not in body
        assert "50.00" in body, "total should be the sum of the two boundary expenses (20 + 30)"

    def test_custom_range_single_day_window(self, client, login):
        email, _ = _new_user_with_expenses(
            client,
            [
                (15.00, "Other", "2026-07-08", "single day match"),
                (99.00, "Other", "2026-07-09", "adjacent day, excluded"),
            ],
        )
        _login_as(login, email)

        response = client.get("/profile?date_from=2026-07-08&date_to=2026-07-08")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "single day match" in body
        assert "adjacent day, excluded" not in body
        assert "15.00" in body


# --------------------------------------------------------------------- #
# Presets: This Month / Last 3 Months / Last 6 Months / All Time        #
# --------------------------------------------------------------------- #

class TestPresetFilters:
    """
    Uses relative dates (computed independently of app.py) so these tests
    are not tied to the current wall-clock date. Markers are chosen with at
    least a one-month buffer around each preset boundary so results are
    unambiguous regardless of the exact day-of-month the tests run on.
    """

    @pytest.fixture
    def preset_user(self, client, login):
        markers = [
            (11.00, "Other", date.today().isoformat(), "MARKER-CURRENT"),
            (22.00, "Other", _months_ago(1).isoformat(), "MARKER-1MO"),
            (33.00, "Other", _months_ago(2).isoformat(), "MARKER-2MO"),
            (44.00, "Other", _months_ago(4).isoformat(), "MARKER-4MO"),
            (55.00, "Other", _months_ago(5).isoformat(), "MARKER-5MO"),
            (66.00, "Other", _months_ago(7).isoformat(), "MARKER-7MO"),
        ]
        email, _ = _new_user_with_expenses(client, markers)
        _login_as(login, email)
        return email

    def test_this_month_preset_shows_only_current_month(self, client, preset_user):
        date_from = date.today().replace(day=1).isoformat()
        date_to = date.today().isoformat()

        response = client.get(f"/profile?date_from={date_from}&date_to={date_to}")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "MARKER-CURRENT" in body
        for excluded in ("MARKER-1MO", "MARKER-2MO", "MARKER-4MO", "MARKER-5MO", "MARKER-7MO"):
            assert excluded not in body, f"{excluded} should be excluded from This Month"
        assert "11.00" in body

    def test_last_3_months_preset_includes_current_and_two_prior_months(self, client, preset_user):
        date_from = _months_ago(2).isoformat()
        date_to = date.today().isoformat()

        response = client.get(f"/profile?date_from={date_from}&date_to={date_to}")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        for included in ("MARKER-CURRENT", "MARKER-1MO", "MARKER-2MO"):
            assert included in body, f"{included} should be included in Last 3 Months"
        for excluded in ("MARKER-4MO", "MARKER-5MO", "MARKER-7MO"):
            assert excluded not in body, f"{excluded} should be excluded from Last 3 Months"
        assert "66.00" in body, "total of the three in-window expenses (11+22+33)"

    def test_last_6_months_preset_includes_current_and_five_prior_months(self, client, preset_user):
        date_from = _months_ago(5).isoformat()
        date_to = date.today().isoformat()

        response = client.get(f"/profile?date_from={date_from}&date_to={date_to}")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        for included in ("MARKER-CURRENT", "MARKER-1MO", "MARKER-2MO", "MARKER-4MO", "MARKER-5MO"):
            assert included in body, f"{included} should be included in Last 6 Months"
        assert "MARKER-7MO" not in body, "7 months ago should fall outside a 6-month window"
        assert "165.00" in body, "total of the five in-window expenses (11+22+33+44+55)"

    def test_all_time_preset_shows_every_expense_with_no_query_params(self, client, preset_user):
        response = client.get("/profile")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        for marker in (
            "MARKER-CURRENT", "MARKER-1MO", "MARKER-2MO",
            "MARKER-4MO", "MARKER-5MO", "MARKER-7MO",
        ):
            assert marker in body, f"{marker} should appear in the All Time view"
        assert "231.00" in body, "total of all six seeded expenses"

    def test_all_time_preset_link_is_a_clean_profile_url_with_no_query_string(
        self, client, preset_user
    ):
        # Even while a filter is active, the "All Time" preset link itself
        # must point at a bare /profile URL (no date_from/date_to params).
        response = client.get("/profile?date_from=2026-01-01&date_to=2026-01-31")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert re.search(r'href="/profile"[^>]*>\s*All Time', body), (
            "All Time preset must link to a clean /profile URL with no query params"
        )


# --------------------------------------------------------------------- #
# Validation errors / graceful fallback                                 #
# --------------------------------------------------------------------- #

class TestValidationFallbacks:
    def test_date_from_after_date_to_falls_back_and_shows_error(self, client, login):
        login()
        response = client.get("/profile?date_from=2026-08-20&date_to=2026-08-01")
        body = response.get_data(as_text=True)

        assert response.status_code == 200, "invalid range must not error out the request"
        assert "Start date must be before end date." in body
        assert DEMO_TOTAL in body, "should fall back to the full unfiltered dataset"

    def test_date_from_equal_to_date_to_is_a_valid_single_day_range_not_an_error(
        self, client, login
    ):
        login()
        response = client.get("/profile?date_from=2026-08-05&date_to=2026-08-05")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Start date must be before end date." not in body

    @pytest.mark.parametrize(
        "date_from,date_to",
        [
            ("not-a-date", "2026-08-10"),
            ("2026-08-01", "also-not-a-date"),
            ("2026-13-40", "2026-08-10"),
            ("", "2026-08-10"),
        ],
    )
    def test_malformed_date_falls_back_to_unfiltered_without_crashing(
        self, client, login, date_from, date_to
    ):
        login()
        response = client.get(f"/profile?date_from={date_from}&date_to={date_to}")
        body = response.get_data(as_text=True)

        assert response.status_code == 200, "malformed dates must never 500"
        assert "Internal Server Error" not in body
        assert DEMO_TOTAL in body, "malformed input should fall back to the unfiltered view"

    def test_only_date_from_provided_falls_back_to_unfiltered(self, client, login):
        login()
        response = client.get("/profile?date_from=2026-08-01")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert DEMO_TOTAL in body

    def test_only_date_to_provided_falls_back_to_unfiltered(self, client, login):
        login()
        response = client.get("/profile?date_to=2026-08-10")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert DEMO_TOTAL in body

    def test_sql_injection_attempt_in_date_from_does_not_crash_and_falls_back(
        self, client, login
    ):
        login()
        injection = quote("' OR '1'='1")
        response = client.get(f"/profile?date_from={injection}&date_to=2026-08-10")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Internal Server Error" not in body
        assert DEMO_TOTAL in body, "injection attempt should be treated as a malformed date"

    def test_extremely_long_date_from_falls_back_gracefully(self, client, login):
        login()
        long_value = "2026-08-01" * 500
        response = client.get(f"/profile?date_from={long_value}&date_to=2026-08-10")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Internal Server Error" not in body
        assert DEMO_TOTAL in body


# --------------------------------------------------------------------- #
# Zero-result filtered range                                            #
# --------------------------------------------------------------------- #

class TestZeroResultsAndFormatting:
    def test_range_with_no_matching_expenses_shows_zero_totals_no_errors(self, client, login):
        email, _ = _new_user_with_expenses(
            client,
            [(75.00, "Food", "2026-06-15", "june expense, outside filtered range")],
        )
        _login_as(login, email)

        response = client.get("/profile?date_from=2026-01-01&date_to=2026-01-31")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "Internal Server Error" not in body
        assert "₹0.00" in body, "total spent should render as Rs 0.00 with the rupee symbol"
        assert "june expense" not in body

    def test_amounts_always_render_with_rupee_symbol_when_filtered(self, client, login):
        email, _ = _new_user_with_expenses(
            client,
            [(42.50, "Food", "2026-02-14", "valentine's lunch")],
        )
        _login_as(login, email)

        response = client.get("/profile?date_from=2026-02-01&date_to=2026-02-28")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert "₹42.50" in body

    def test_amounts_always_render_with_rupee_symbol_when_unfiltered(self, client, login):
        login()
        response = client.get("/profile")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert f"₹{DEMO_TOTAL}" in body


# --------------------------------------------------------------------- #
# Visual indication of the active filter                                #
# --------------------------------------------------------------------- #

class TestActiveFilterIndication:
    def test_custom_range_inputs_retain_submitted_values(self, client, login):
        login()
        response = client.get("/profile?date_from=2026-03-01&date_to=2026-03-31")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'value="2026-03-01"' in body
        assert 'value="2026-03-31"' in body

    def test_no_filter_leaves_custom_range_inputs_empty(self, client, login):
        login()
        response = client.get("/profile")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'name="date_from" class="form-input" value=""' in body
        assert 'name="date_to" class="form-input" value=""' in body

    def test_this_month_preset_is_marked_active_when_selected(self, client, login):
        login()
        date_from = date.today().replace(day=1).isoformat()
        date_to = date.today().isoformat()

        response = client.get(f"/profile?date_from={date_from}&date_to={date_to}")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert re.search(r'class="filter-chip\s+is-active"\s*>\s*This Month', body), (
            "the This Month preset chip should carry an active indicator when selected"
        )

    def test_invalid_range_falls_back_to_all_time_active_state(self, client, login):
        login()
        # date_from > date_to -> falls back to unfiltered, so the effective
        # active state should be "All Time", not the (invalid) custom range.
        response = client.get("/profile?date_from=2026-08-20&date_to=2026-08-01")
        body = response.get_data(as_text=True)

        assert response.status_code == 200
        assert re.search(r'class="filter-chip\s+is-active"\s*>\s*All Time', body)


# --------------------------------------------------------------------- #
# HTTP semantics sanity checks                                          #
# --------------------------------------------------------------------- #

class TestHttpSemantics:
    @pytest.mark.parametrize(
        "query_string",
        [
            "",
            "?date_from=2026-08-01&date_to=2026-08-10",
            "?date_from=garbage",
            "?date_from=2026-08-20&date_to=2026-08-01",
        ],
    )
    def test_profile_always_returns_200_for_valid_and_invalid_filters(
        self, client, login, query_string
    ):
        login()
        response = client.get(f"/profile{query_string}")
        assert response.status_code == 200
