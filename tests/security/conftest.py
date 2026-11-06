import hashlib
import uuid

import psycopg2
import psycopg2.extras
import pytest
import requests

try:
    from tests.security.helpers import (
        DB_HOST, DB_PORT, ENV, TEST_PASSWORD, WEBAPP_URL, reset_rate_limits, run_python,
    )
except ImportError:
    from helpers import (
        DB_HOST, DB_PORT, ENV, TEST_PASSWORD, WEBAPP_URL, reset_rate_limits, run_python,
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: testy trwające dłużej lub wymagające internetu")


@pytest.fixture(scope="session")
def running_stack():
    try:
        requests.get(f"{WEBAPP_URL}/api/me", timeout=5)
    except requests.RequestException:
        pytest.exit("Aplikacja nie działa. Uruchom najpierw: docker compose up -d", returncode=1)

    yield
    reset_rate_limits()


@pytest.fixture
def clean_rate_limits(running_stack):
    reset_rate_limits()


@pytest.fixture(scope="session")
def db_conn(running_stack):
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=ENV["APP_DB_NAME"],
        user=ENV["APP_DB_USER"],
        password=ENV["APP_DB_PASSWORD"],
    )
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture
def make_client(db_conn):
    prefixes = []

    def _make_client(create_tables=False):
        prefix = f"sec_{uuid.uuid4().hex[:10]}"
        prefixes.append(prefix)

        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clients (name, slug, store_prefix) VALUES (%s, %s, %s) RETURNING id",
                (prefix, prefix, prefix),
            )
            client_id = cur.fetchone()[0]

        if create_tables:
            run_python("webapp", f"""
import json
from utils import get_db_transaction
from services.provisioning import create_client_tables
conn = get_db_transaction()
create_client_tables(conn.cursor(), {prefix!r})
conn.commit()
print(json.dumps(True))
""")

        return {"id": client_id, "prefix": prefix}

    yield _make_client

    with db_conn.cursor() as cur:
        for prefix in prefixes:
            for suffix in ("products", "competitors", "matches"):
                cur.execute(f"DROP TABLE IF EXISTS {prefix}_{suffix} CASCADE")
            cur.execute("DELETE FROM clients WHERE store_prefix = %s", (prefix,))


@pytest.fixture
def make_user(db_conn):
    user_ids = []

    def _make_user(status="active", is_admin=False, client_id=None, password_hash=None):
        email = f"sec_{uuid.uuid4().hex[:10]}@example.invalid"
        stored_hash = password_hash or hashlib.sha256(TEST_PASSWORD.encode()).hexdigest()

        with db_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (username, email, password_hash, status, is_admin, client_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (email, email, stored_hash, status, is_admin, client_id),
            )
            user_id = cur.fetchone()[0]

        user_ids.append(user_id)
        return {"id": user_id, "username": email, "email": email, "password": TEST_PASSWORD}

    yield _make_user

    with db_conn.cursor() as cur:
        for user_id in user_ids:
            cur.execute("DELETE FROM onboarding_requests WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


@pytest.fixture
def login():
    def _login(user, base_url=WEBAPP_URL):
        session = requests.Session()
        response = session.post(
            f"{base_url}/api/login",
            json={"username": user["username"], "password": user["password"]},
            timeout=15,
        )
        assert response.status_code == 200, f"Logowanie nie powiodło się: {response.text}"
        return session

    return _login


@pytest.fixture(scope="session")
def api_routes(running_stack):
    return run_python("webapp", """
import json
from app import app
routes = sorted(
    (rule.rule, sorted(rule.methods - {"HEAD", "OPTIONS"}))
    for rule in app.url_map.iter_rules()
    if rule.rule.startswith("/api/")
)
print(json.dumps(routes))
""")
