import os
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="warranty-test-"))
os.environ["DATABASE_URL"] = "sqlite:///" + _root.joinpath("test.db").as_posix()
os.environ["STORAGE_DIR"] = str(_root / "storage")
os.environ["APP_SECRET"] = "test-secret-key-for-warranty-scanner"
os.environ["SEED_DEMO_USERS"] = "1"
os.environ["AUTO_SCAN"] = "0"
os.environ["WORKER_INLINE"] = "1"
os.environ["JWT_EXP_MINUTES"] = "60"
os.environ["RENDER_DPI"] = "120"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_headers(client):
    response = client.post("/api/auth/login", json={"email": "admin@ekk.local", "password": "Admin@12345"})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def operator_headers(client):
    response = client.post("/api/auth/login", json={"email": "operator@ekk.local", "password": "Oper@12345"})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
