# Spec: Delete Expense

## Overview
Step 9 lets a logged-in user permanently remove one of their own expenses.
The existing stub route `GET /expenses/<id>/delete` is replaced with a
POST-only route that deletes the row and redirects back to `/profile`.
Because deletion is destructive and irreversible, it must not be triggered
by a plain GET link (bots/prefetching could trigger it); it is submitted via
a small `<form method="POST">` in the transactions table, guarded by a
native JS confirmation dialog. Ownership is enforced the same way as the
edit feature: a user can only delete expenses that belong to them. One new
query helper, `delete_expense`, is added to `database/db.py`.

## Depends on
- Step 1: Database setup (`expenses` table exists)
- Step 3: Login / Logout (`session["user_id"]` is set and enforced)
- Step 5: Profile page renders transactions (the delete action lives there)
- Step 8: Edit Expense (establishes the ownership-scoped mutation pattern
  this step follows — `get_expense_by_id` already exists for 404 checks)

## Routes
- `POST /expenses/<int:expense_id>/delete` — delete the expense if it
  belongs to the current user, then redirect to `/profile` — logged-in only

The existing `GET /expenses/<int:id>/delete` stub is removed. Deletion is
POST-only; there is no confirmation page/GET view for this route.

## Database changes
No new tables or columns. All required columns already exist in `expenses`.

## Templates
- **Create:** none
- **Modify:** `templates/profile.html`
  - In the "Actions" cell per transaction row (`templates/profile.html:89`),
    add a second action: a small `<form method="POST" action="{{ url_for('delete_expense', expense_id=txn.id) }}">`
    containing a single submit button styled as a text link/button
    (e.g. `class="link-danger"` or similar existing utility — no new hex
    colors), alongside the existing "Edit" link
  - Give the delete form/button a `data-confirm-delete` hook (or similar
    attribute) that `static/js/main.js` binds a `confirm()` dialog to

## Files to change
- `database/db.py`
  - Add `delete_expense(expense_id, user_id)` — issues a parameterised
    `DELETE FROM expenses WHERE id = ? AND user_id = ?` for ownership safety
- `app.py`
  - Import `delete_expense` from `database.db`
  - Replace the stub route:
    ```python
    @app.route("/expenses/<int:id>/delete")
    def delete_expense(id):
        return "Delete expense — coming in Step 9"
    ```
    with:
    ```python
    @app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
    @login_required
    def delete_expense(expense_id):
        expense = get_expense_by_id(expense_id, session["user_id"])
        if expense is None:
            abort(404)
        delete_expense(expense_id, session["user_id"])
        return redirect(url_for("profile"))
    ```
    (renaming the URL parameter from `id` to `expense_id` for consistency
    with `edit_expense`)
- `templates/profile.html` — add the delete form/button per row
- `static/js/main.js` — add a small vanilla-JS handler that shows a
  `confirm("Delete this expense?")` dialog before the delete form submits,
  preventing submission if the user cancels

## Files to create
No new files.

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` only via `get_db()`
- Parameterised queries only — never string-format values into SQL
- Passwords hashed with werkzeug (not touched by this feature, kept for
  consistency with project-wide rules)
- Foreign keys PRAGMA must be enabled on every connection (already done in
  `get_db()`)
- `delete_expense` must scope its `DELETE` to `id = ? AND user_id = ?` so
  one user can never delete another user's expense
- Reuse `get_expense_by_id` (already in `database/db.py`) to check
  existence and ownership before deleting, so a missing or foreign expense
  returns a 404 instead of silently no-op deleting
- The route must be POST-only — do not accept GET for the delete action
- Unauthenticated POSTs must redirect to `/login` (via `login_required`)
- If the expense does not exist or belongs to another user, return a 404
- After a successful delete, redirect to `url_for("profile")`
- Deleting must ask for confirmation client-side (vanilla JS `confirm()`)
  before the form submits — no browser alert()/confirm() substitutes, no
  new JS frameworks
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- Currency must always display as ₹ — never £ or $ (unaffected by this
  feature, but any re-rendered rows must keep this)

## Definition of done
- [ ] Visiting `/expenses/<id>/delete` with GET no longer works (405 or
      route not found for GET)
- [ ] Submitting the delete form while logged out redirects to `/login`
- [ ] Submitting the delete form for a non-existent or other user's expense
      returns 404
- [ ] Submitting the delete form for your own expense removes it from the
      database and redirects to `/profile`
- [ ] The deleted expense no longer appears in the "Recent transactions"
      table, and stats/category breakdown on `/profile` reflect the removal
- [ ] Each row in the profile transaction table has a "Delete" action next
      to "Edit", guarded by a JS confirmation dialog before it submits
- [ ] Cancelling the confirmation dialog does not delete the expense
