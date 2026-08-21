import uuid


def test_profile_requires_login_redirects_to_login(client):
    response = client.get("/profile")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_profile_shows_demo_user_data(client, login):
    login()  # defaults: demo@spendly.com / demo123

    response = client.get("/profile")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Demo User" in body
    assert "demo@spendly.com" in body
    assert "390.25" in body
    assert "Bills" in body


def test_profile_for_new_user_with_no_expenses(client, login):
    unique_email = f"newuser-{uuid.uuid4().hex[:10]}@example.com"
    password = "password123"

    register_response = client.post(
        "/register",
        data={"name": "Zero Expenses User", "email": unique_email, "password": password},
        follow_redirects=True,
    )
    assert register_response.status_code == 200

    login(email=unique_email, password=password)

    response = client.get("/profile")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "0.00" in body
    assert "—" in body  # em-dash sentinel for top_category with no expenses
