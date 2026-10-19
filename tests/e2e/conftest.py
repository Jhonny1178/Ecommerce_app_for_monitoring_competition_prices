import os
import pytest
import requests
import psycopg2
import psycopg2.extras

WEBAPP_URL = os.getenv("E2E_WEBAPP_URL", "http://localhost:6767")
FRONTEND_URL = os.getenv("E2E_FRONTEND_URL", "http://localhost:80")

ADMIN_CREDENTIALS = {"username": "admin", "password": "admin123"}

DB_CONFIG = {
    "host": os.getenv("E2E_DB_HOST", "localhost"),
    "port": int(os.getenv("E2E_DB_PORT", 5434)),
    "dbname": os.getenv("E2E_DB_NAME", "ecommerce_data"),
    "user": os.getenv("E2E_DB_USER", "postgres"),
    "password": os.getenv("E2E_DB_PASSWORD", "eroch"),
}


@pytest.fixture(scope="session")
def db_conn():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    resp = s.post(f"{WEBAPP_URL}/api/login", json=ADMIN_CREDENTIALS)
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    data = resp.json()
    assert data.get("ok"), f"Admin login not ok: {data}"
    yield s
    s.close()
