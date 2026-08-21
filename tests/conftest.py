import sys
import pytest

@pytest.fixture
def app(tmp_path, monkeypatch):
    sys.modules.pop("app", None)
    sys.modules.pop("database.db", None)
    sys.modules.pop("database", None)
    from database import db as db_module
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "test.db"))
    import app as app_module
    app_module.app.config.update(TESTING=True)
    yield app_module.app
    sys.modules.pop("app", None)

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def login(client):
    def _login(email="demo@spendly.com", password="demo123"):
        return client.post("/login", data={"email": email, "password": password},
                            follow_redirects=True)
    return _login
