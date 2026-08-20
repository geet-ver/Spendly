import os
from functools import wraps

import sqlite3

from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import check_password_hash, generate_password_hash

from database.db import init_db, seed_db, get_user_by_email, create_user

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


@app.route("/profile")
@login_required
def profile():
    user = {
        "name": "Demo User",
        "email": "demo@spendly.com",
        "initials": "DU",
        "member_since": "August 2026",
    }
    stats = {
        "total_spent": 390.25,
        "transaction_count": 8,
        "top_category": "Bills",
    }
    transactions = [
        {"date": "2026-08-16", "description": "Groceries", "category": "Food", "amount": 32.75},
        {"date": "2026-08-14", "description": "Miscellaneous", "category": "Other", "amount": 15.00},
        {"date": "2026-08-12", "description": "New shoes", "category": "Shopping", "amount": 80.00},
        {"date": "2026-08-10", "description": "Movie tickets", "category": "Entertainment", "amount": 25.00},
        {"date": "2026-08-07", "description": "Pharmacy", "category": "Health", "amount": 60.00},
    ]
    categories = [
        {"name": "Bills", "amount": 120.00, "percent": 31},
        {"name": "Shopping", "amount": 80.00, "percent": 21},
        {"name": "Health", "amount": 60.00, "percent": 15},
        {"name": "Food", "amount": 45.25, "percent": 12},
        {"name": "Transport", "amount": 45.00, "percent": 11},
        {"name": "Entertainment", "amount": 25.00, "percent": 6},
        {"name": "Other", "amount": 15.00, "percent": 4},
    ]
    return render_template(
        "profile.html", user=user, stats=stats,
        transactions=transactions, categories=categories,
    )


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
