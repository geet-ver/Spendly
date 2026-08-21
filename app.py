import os
from datetime import date, datetime
from functools import wraps

import sqlite3

from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash, generate_password_hash

from database.db import (
    init_db,
    seed_db,
    get_user_by_email,
    create_user,
    get_user_by_id,
    get_summary_stats,
    get_recent_transactions,
    get_category_breakdown,
)

app = Flask(__name__)
# Dev-only fallback secret; override with the SECRET_KEY env var in real deployments.
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-secret-change-me")

with app.app_context():
    init_db()
    seed_db()


# ------------------------------------------------------------------ #
# Auth helpers                                                        #
# ------------------------------------------------------------------ #

# Reusable guard for future protected routes (profile, expenses).
def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view


# ------------------------------------------------------------------ #
# Routes                                                              #
# ------------------------------------------------------------------ #

@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("landing"))

    if request.method == "GET":
        return render_template("register.html")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not name or not email or not password:
        return render_template("register.html", error="All fields are required.")

    if len(password) < 8:
        return render_template(
            "register.html", error="Password must be at least 8 characters."
        )

    if get_user_by_email(email):
        return render_template(
            "register.html", error="An account with that email already exists."
        )

    password_hash = generate_password_hash(password)
    try:
        create_user(name, email, password_hash)
    except sqlite3.IntegrityError:
        return render_template(
            "register.html", error="An account with that email already exists."
        )

    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if "user_id" in session:
            return redirect(url_for("profile"))
        return render_template("login.html")

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    user = get_user_by_email(email)
    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Invalid email or password")

    session.clear()
    session["user_id"] = user["id"]
    session["name"] = user["name"]
    return redirect(url_for("profile"))


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


def _initials(name):
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (parts[0][:2] if parts else "?").upper()


def _format_member_since(created_at):
    return datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%B %Y")


def _parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _shift_months(d, months_back):
    # year*12 + (month-1) turns a (year, month) pair into a zero-based month
    # count, so subtracting months_back and re-splitting with divmod lands on
    # the first of the target month without a calendar library.
    total = d.year * 12 + (d.month - 1) - months_back
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, 1)


def _build_presets(today):
    return [
        {"key": "this_month", "label": "This Month",
         "date_from": today.replace(day=1), "date_to": today},
        {"key": "last_3_months", "label": "Last 3 Months",
         "date_from": _shift_months(today, 2), "date_to": today},
        {"key": "last_6_months", "label": "Last 6 Months",
         "date_from": _shift_months(today, 5), "date_to": today},
        {"key": "all_time", "label": "All Time",
         "date_from": None, "date_to": None},
    ]


def _match_preset(presets, date_from_str, date_to_str):
    for preset in presets:
        preset_from = preset["date_from"].isoformat() if preset["date_from"] else None
        preset_to = preset["date_to"].isoformat() if preset["date_to"] else None
        if preset_from == date_from_str and preset_to == date_to_str:
            return preset["key"]
    return "custom" if date_from_str else "all_time"


def _resolve_date_filter(args, presets):
    """Reads date_from/date_to from request.args and returns
    (date_from_str, date_to_str, active_preset, raw_from, raw_to, error)."""
    raw_from = args.get("date_from", "").strip()
    raw_to = args.get("date_to", "").strip()
    parsed_from = _parse_iso_date(raw_from)
    parsed_to = _parse_iso_date(raw_to)

    error = None
    if parsed_from and parsed_to:
        if parsed_from > parsed_to:
            error = "Start date must be before end date."
            active_from, active_to = None, None
        else:
            active_from, active_to = parsed_from, parsed_to
    else:
        active_from, active_to = None, None

    date_from_str = active_from.isoformat() if active_from else None
    date_to_str = active_to.isoformat() if active_to else None
    active_preset = _match_preset(presets, date_from_str, date_to_str)

    return date_from_str, date_to_str, active_preset, raw_from, raw_to, error


@app.route("/profile")
@login_required
def profile():
    user_row = get_user_by_id(session["user_id"])
    user = {
        "name": user_row["name"],
        "email": user_row["email"],
        "initials": _initials(user_row["name"]),
        "member_since": _format_member_since(user_row["created_at"]),
    }

    presets = _build_presets(date.today())
    date_from_str, date_to_str, active_preset, raw_from, raw_to, error = (
        _resolve_date_filter(request.args, presets)
    )

    stats = get_summary_stats(session["user_id"], date_from_str, date_to_str)
    transactions = get_recent_transactions(
        session["user_id"], date_from=date_from_str, date_to=date_to_str
    )
    categories = get_category_breakdown(session["user_id"], date_from_str, date_to_str)

    return render_template(
        "profile.html", user=user, stats=stats,
        transactions=transactions, categories=categories,
        presets=presets, active_preset=active_preset,
        filter_from=raw_from, filter_to=raw_to, error=error,
    )


@app.route("/analytics")
@login_required
def analytics():
    return render_template("analytics.html")


# ------------------------------------------------------------------ #
# Placeholder routes — students will implement these                  #
# ------------------------------------------------------------------ #

@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/expenses/add")
def add_expense():
    return "Add expense — coming in Step 7"


@app.route("/expenses/<int:id>/edit")
def edit_expense(id):
    return "Edit expense — coming in Step 8"


@app.route("/expenses/<int:id>/delete")
def delete_expense(id):
    return "Delete expense — coming in Step 9"


if __name__ == "__main__":
    app.run(debug=True, port=5001)
