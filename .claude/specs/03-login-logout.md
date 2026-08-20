# Spec: Login and Logout

## Overview
This feature implements real authentication for Spendly: verifying credentials on the existing `login.html` form, establishing a logged-in session, protecting routes that require a signed-in user, and letting a user sign out. Up to this point, `GET /login` only renders a static form — no `POST` handler exists, no session is ever created, and `GET /logout` is a placeholder string. This step wires those pieces together using Flask's built-in signed-cookie sessions, so that later steps (profile, expense CRUD) have a real `user_id` to key off of.

## Depends on
- Step 1 — database setup (`users` table, `get_db()`, password hashing via werkzeug) must be complete. It is.

## Routes
- `POST /login` — authenticate a user against `users.email` / `users.password_hash`, start a session, redirect to `/profile` on success or re-render `login.html` with an error on failure — public
- `GET /logout` — clear the session and redirect to `/login` — logged-in

`GET /login` already exists and renders the template; it is not being changed except to accept a redirect-if-already-logged-in check.

## Database changes
No database changes. `users` table (`id`, `name`, `email`, `password_hash`, `created_at`) already supports this feature as defined in `database/db.py`.

## Templates
- **Create:** none
- **Modify:** `templates/login.html` — no structural change needed; it already posts to `/login` and renders `{{ error }}`. Update the hardcoded `action="/login"` to `action="{{ url_for('login') }}"` to comply with the no-hardcoded-URLs rule.

## Files to change
- `app.py` — add `app.secret_key` (from env var, fallback for dev), add `POST` method to the `/login` route with authentication logic, implement `/logout` to clear the session, add a `login_required` check pattern for future protected routes
- `database/db.py` — add a `get_user_by_email(email)` helper used by the login route (DB logic must live here, not inline in `app.py`)
- `templates/login.html` — fix hardcoded form action to use `url_for()`

## Files to create
- None

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs
- Parameterised queries only
- Passwords hashed with werkzeug (`check_password_hash` against the stored `password_hash`)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- Session must store only `user_id` (and optionally `name`) — never the password hash
- `app.secret_key` must be set or Flask sessions will silently fail
- Do not implement `/profile`, `/register` POST handling, or any expense routes — those are separate steps
- Failed login must re-render `login.html` with a generic error (e.g. "Invalid email or password") — never reveal whether the email exists

## Definition of done
- [ ] Submitting valid demo credentials (`demo@spendly.com` / `demo123`) on `/login` redirects to `/profile`
- [ ] A Flask session cookie is set after successful login
- [ ] Submitting an unknown email or wrong password re-renders `login.html` with an error message, no redirect
- [ ] Visiting `/logout` while logged in clears the session and redirects to `/login`
- [ ] After `/logout`, the session no longer contains `user_id`
- [ ] `templates/login.html` posts to `url_for('login')` instead of a hardcoded `/login`
- [ ] No inline SQL queries were added to `app.py` — all DB access goes through `database/db.py`
- [ ] App still starts cleanly on port 5001 with no errors
