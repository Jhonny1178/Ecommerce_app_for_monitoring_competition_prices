import hashlib
import re
import uuid

import pytest
import requests

try:
    from tests.security.helpers import (
        FRONTEND_URL, PROJECT_DIR, SESSION_COOKIE, WEBAPP_URL, compose, run_python,
    )
except ImportError:
    from helpers import (
        FRONTEND_URL, PROJECT_DIR, SESSION_COOKIE, WEBAPP_URL, compose, run_python,
    )

pytestmark = pytest.mark.usefixtures("clean_rate_limits")

NEW_PASSWORD = "NoweTajneHaslo456789"
PUBLIC_ROUTES = {
    "/api/login", "/api/logout", "/api/register", "/api/forgot_password",
    "/api/reset_password", "/api/send_message", "/api/report_bug",
}
DATA_ENDPOINTS = [
    "/api/products", "/api/stats", "/api/filters", "/api/categories", "/api/stores",
    "/api/manufacturers", "/api/comparison", "/api/comparison/summary", "/api/report",
    "/api/products/1", "/api/products/1/price-history",
    "/api/image-proxy?url=https://example.com/a.png",
]
INACTIVE_STATUSES = ["onboarding_required", "awaiting_payment", "pending_admin", "rejected"]
DEFAULT_CREDENTIALS = [("admin", "admin123"), ("nasz_klient", "klient123"), ("admin", "admin")]
WEAK_PASSWORDS = ["Krotkie1", "Abcdefghij1", "Abcdefghijkl"]
INVALID_EMAILS = [
    "bez-malpy", "a b@example.invalid", "a@example.invalid\nBcc: x@example.invalid",
    "a@example.invalid,b@example.invalid", "a@example.invalid;b@example.invalid", "@example.invalid",
]
UPLOAD_DIR = PROJECT_DIR / "storage" / "file_folder"

RATE_LIMITS = [
    ("/api/login", {"username": "nie-ma-takiego", "password": "zle-haslo"}, 10, False),
    ("/api/register", {}, 5, False),
    ("/api/forgot_password", {"email": "limit@example.invalid"}, 3, False),
    ("/api/reset_password", {"email": "limit@example.invalid", "code": "0", "new_password": "x"}, 5, False),
    ("/api/send_message", {}, 5, False),
    ("/api/report_bug", {}, 10, False),
    ("/api/change_password", {}, 10, True),
    ("/api/onboarding/submit", {}, 10, True),
    ("/api/upload_onboarding_file", {}, 10, True),
    ("/api/products/999999999/recommend", {}, 30, True),
]


def fill_route(rule):
    return re.sub(r"<[^>]+>", "999999999", rule)


def add_reset_code(db_conn, user, code, minutes=15):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO password_resets (user_id, reset_token_hash, expires_at)
            VALUES (%s, %s, NOW() + make_interval(mins => %s))
            """,
            (user["id"], hashlib.sha256(code.encode()).hexdigest(), minutes),
        )


def reset_password(user, code):
    return requests.post(
        f"{WEBAPP_URL}/api/reset_password",
        json={"email": user["email"], "code": code, "new_password": NEW_PASSWORD},
        timeout=15,
    )


def current_status(db_conn, user):
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM users WHERE id = %s", (user["id"],))
        return cur.fetchone()[0]


def forge_cookie(payload, secret_key):
    return run_python("webapp", f"""
import json
from flask import Flask
from flask.sessions import SecureCookieSessionInterface
app = Flask("forge")
app.secret_key = {secret_key}
print(json.dumps(SecureCookieSessionInterface().get_signing_serializer(app).dumps({payload!r})))
""")


@pytest.mark.parametrize("username,password", DEFAULT_CREDENTIALS)
def test_default_credentials_do_not_work(username, password):
    response = requests.post(
        f"{WEBAPP_URL}/api/login", json={"username": username, "password": password}, timeout=15,
    )
    assert response.status_code == 401


@pytest.mark.parametrize("password", ["admin123", "klient123"])
def test_database_has_no_default_password_hash(db_conn, password):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT username FROM users WHERE password_hash = %s",
            (hashlib.sha256(password.encode()).hexdigest(),),
        )
        assert cur.fetchall() == []


def test_locked_seed_account_cannot_log_in(make_user):
    user = make_user(password_hash="!")
    response = requests.post(
        f"{WEBAPP_URL}/api/login", json={"username": user["username"], "password": "!"}, timeout=15,
    )
    assert response.status_code == 401


def test_login_does_not_reveal_whether_account_exists(make_user):
    user = make_user()
    wrong_password = requests.post(
        f"{WEBAPP_URL}/api/login", json={"username": user["username"], "password": "zle-haslo"}, timeout=15,
    )
    unknown_user = requests.post(
        f"{WEBAPP_URL}/api/login", json={"username": "nie-ma-takiego", "password": "zle-haslo"}, timeout=15,
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_legacy_password_hash_is_upgraded_on_login(make_user, login, db_conn):
    user = make_user()
    login(user)

    with db_conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],))
        assert cur.fetchone()[0].startswith("scrypt:")


def test_new_passwords_are_salted(make_user, login, db_conn):
    first, second = make_user(), make_user()
    login(first)
    login(second)

    with db_conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = ANY(%s)", ([first["id"], second["id"]],))
        hashes = [row[0] for row in cur.fetchall()]

    assert hashes[0] != hashes[1]


@pytest.mark.parametrize("password", WEAK_PASSWORDS)
def test_change_password_rejects_weak_password(make_user, login, password):
    user = make_user()
    response = login(user).post(
        f"{WEBAPP_URL}/api/change_password",
        json={"current_password": user["password"], "new_password": password},
        timeout=15,
    )
    assert response.status_code == 400


@pytest.mark.parametrize("password", WEAK_PASSWORDS)
def test_reset_password_rejects_weak_password(make_user, password):
    user = make_user()
    response = requests.post(
        f"{WEBAPP_URL}/api/reset_password",
        json={"email": user["email"], "code": "123456", "new_password": password},
        timeout=15,
    )
    assert response.status_code == 400


@pytest.mark.parametrize("password", WEAK_PASSWORDS)
def test_register_rejects_weak_password(db_conn, password):
    email = f"sec_{uuid.uuid4().hex[:10]}@example.invalid"
    try:
        response = requests.post(
            f"{WEBAPP_URL}/api/register",
            json={
                "first_name": "Jan", "last_name": "Test", "email": email, "password": password,
                "company_domain": "example.invalid", "urls": ["https://example.invalid"],
            },
            timeout=15,
        )
        assert response.status_code == 400
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email = %s", (email,))


@pytest.mark.parametrize("email", INVALID_EMAILS)
def test_register_rejects_invalid_email(db_conn, email):
    try:
        response = requests.post(
            f"{WEBAPP_URL}/api/register",
            json={
                "first_name": "Jan", "last_name": "Test", "email": email, "password": "TajneHaslo123456",
                "company_domain": "example.invalid", "urls": ["https://example.invalid"],
            },
            timeout=15,
        )
        assert response.status_code == 400
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM onboarding_requests WHERE user_id IN (SELECT id FROM users WHERE email = %s)", (email,))
            cur.execute("DELETE FROM users WHERE email = %s", (email,))


def test_change_password_requires_current_password(make_user, login):
    user = make_user()
    response = login(user).post(
        f"{WEBAPP_URL}/api/change_password",
        json={"current_password": "zle-haslo", "new_password": NEW_PASSWORD},
        timeout=15,
    )
    assert response.status_code == 400


def test_every_private_route_rejects_anonymous_user(api_routes):
    exposed = []
    for rule, methods in api_routes:
        if rule in PUBLIC_ROUTES:
            continue
        for method in methods:
            response = requests.request(method, f"{WEBAPP_URL}{fill_route(rule)}", json={}, timeout=15)
            if response.status_code not in (401, 403):
                exposed.append(f"{method} {rule} -> {response.status_code}")

    assert not exposed, f"Trasy dostępne bez logowania: {exposed}. Publiczne dopisz do PUBLIC_ROUTES"


def test_every_admin_route_rejects_regular_user(api_routes, make_user, login):
    session = login(make_user(status="active"))
    exposed = []
    for rule, methods in api_routes:
        if not rule.startswith("/api/admin"):
            continue
        for method in methods:
            response = session.request(method, f"{WEBAPP_URL}{fill_route(rule)}", json={}, timeout=15)
            if response.status_code != 403:
                exposed.append(f"{method} {rule} -> {response.status_code}")

    assert not exposed, f"Trasy admina dostępne dla zwykłego użytkownika: {exposed}"


def test_admin_can_reach_admin_route(make_user, login):
    session = login(make_user(is_admin=True))
    assert session.get(f"{WEBAPP_URL}/api/admin/pending_users", timeout=15).status_code == 200


@pytest.mark.parametrize("status", INACTIVE_STATUSES)
def test_inactive_user_has_no_access_to_data(make_client, make_user, login, status):
    session = login(make_user(status=status, client_id=make_client()["id"]))

    reachable = [
        path for path in DATA_ENDPOINTS
        if session.get(f"{WEBAPP_URL}{path}", timeout=15).status_code != 403
    ]
    assert not reachable, f"Status {status} ma dostęp do: {reachable}"


def test_active_user_passes_status_check(make_client, make_user, login):
    session = login(make_user(status="active", client_id=make_client()["id"]))
    assert session.get(f"{WEBAPP_URL}/api/products", timeout=15).status_code != 403


def test_status_change_in_database_works_immediately(make_client, make_user, login, db_conn):
    user = make_user(status="active", client_id=make_client()["id"])
    session = login(user)

    with db_conn.cursor() as cur:
        cur.execute("UPDATE users SET status = 'rejected' WHERE id = %s", (user["id"],))

    assert session.get(f"{WEBAPP_URL}/api/products", timeout=15).status_code == 403


def test_cancel_blocks_data_and_simulated_purchase_restores_access(make_client, make_user, login):
    session = login(make_user(status="active", client_id=make_client()["id"]))

    assert session.post(f"{WEBAPP_URL}/api/subscription/cancel", timeout=15).status_code == 200
    assert session.get(f"{WEBAPP_URL}/api/products", timeout=15).status_code == 403

    bought = session.post(f"{WEBAPP_URL}/api/subscription/buy", json={"package": "Podstawowy"}, timeout=15)
    assert bought.status_code == 200
    assert session.get(f"{WEBAPP_URL}/api/products", timeout=15).status_code != 403


@pytest.mark.parametrize("status", ["rejected", "onboarding_required", "pending_admin"])
def test_purchase_is_not_allowed_from_other_statuses(make_user, login, db_conn, status):
    user = make_user(status=status)
    response = login(user).post(f"{WEBAPP_URL}/api/subscription/buy", json={"package": "Podstawowy"}, timeout=15)

    assert response.status_code == 409
    assert current_status(db_conn, user) == status


def test_rejected_user_cannot_cancel_to_return_to_queue(make_user, login, db_conn):
    user = make_user(status="rejected")
    response = login(user).post(f"{WEBAPP_URL}/api/subscription/cancel", timeout=15)

    assert response.status_code == 409
    assert current_status(db_conn, user) == "rejected"


@pytest.mark.parametrize("status", ["onboarding_required", "pending_admin", "awaiting_payment"])
def test_only_active_subscription_can_be_cancelled(make_user, login, db_conn, status):
    user = make_user(status=status)
    response = login(user).post(f"{WEBAPP_URL}/api/subscription/cancel", timeout=15)

    assert response.status_code == 409
    assert current_status(db_conn, user) == status


def upload_file(session, name):
    return session.post(
        f"{WEBAPP_URL}/api/upload_onboarding_file",
        data={"source_type": "local", "file_format": "csv"},
        files={"file": (name, b"sku,name\n1,test\n", "text/csv")},
        timeout=15,
    )


@pytest.mark.parametrize("status", ["active", "rejected", "pending_admin", "awaiting_payment"])
def test_file_is_not_saved_without_open_onboarding_request(make_user, login, status):
    user = make_user(status=status)
    response = upload_file(login(user), "plik.csv")
    saved = list(UPLOAD_DIR.glob(f"user_{user['id']}_*"))

    try:
        assert response.status_code == 403
        assert saved == []
    finally:
        for path in saved:
            path.unlink()


def test_file_is_saved_for_open_onboarding_request(make_user, login, db_conn):
    user = make_user(status="onboarding_required")
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO onboarding_requests (user_id, requested_store_name, status) VALUES (%s, 'test', 'onboarding_required')",
            (user["id"],),
        )

    response = upload_file(login(user), "../../plik.csv")
    saved = list(UPLOAD_DIR.glob(f"user_{user['id']}_*"))

    try:
        assert response.status_code == 200
        assert [path.name for path in saved] == [f"user_{user['id']}_plik.csv"]
    finally:
        for path in saved:
            path.unlink()


def test_removing_admin_rights_works_immediately(make_user, login, db_conn):
    user = make_user(is_admin=True)
    session = login(user)
    assert session.get(f"{WEBAPP_URL}/api/admin/pending_users", timeout=15).status_code == 200

    with db_conn.cursor() as cur:
        cur.execute("UPDATE users SET is_admin = FALSE WHERE id = %s", (user["id"],))

    assert session.get(f"{WEBAPP_URL}/api/admin/pending_users", timeout=15).status_code == 403


def test_deleted_user_loses_session(make_user, login, db_conn):
    user = make_user()
    session = login(user)

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user["id"],))

    assert session.get(f"{WEBAPP_URL}/api/me", timeout=15).status_code == 401


def test_password_change_logs_out_other_sessions_only(make_user, login):
    user = make_user()
    current, other = login(user), login(user)
    current.get(f"{WEBAPP_URL}/api/me", timeout=15)

    response = current.post(
        f"{WEBAPP_URL}/api/change_password",
        json={"current_password": user["password"], "new_password": NEW_PASSWORD},
        timeout=15,
    )

    assert response.status_code == 200
    assert current.get(f"{WEBAPP_URL}/api/me", timeout=15).status_code == 200
    assert other.get(f"{WEBAPP_URL}/api/me", timeout=15).status_code == 401


def test_password_reset_logs_out_all_sessions(make_user, login, db_conn):
    user = make_user()
    session = login(user)
    add_reset_code(db_conn, user, "123456")

    assert reset_password(user, "123456").status_code == 200
    assert session.get(f"{WEBAPP_URL}/api/me", timeout=15).status_code == 401


def test_reset_code_works_only_once(make_user, db_conn):
    user = make_user()
    add_reset_code(db_conn, user, "123456")

    assert reset_password(user, "123456").status_code == 200
    assert reset_password(user, "123456").status_code == 400


def test_expired_reset_code_is_rejected(make_user, db_conn):
    user = make_user()
    add_reset_code(db_conn, user, "123456", minutes=-1)

    assert reset_password(user, "123456").status_code == 400


def test_wrong_reset_code_is_rejected(make_user, db_conn):
    user = make_user()
    add_reset_code(db_conn, user, "123456")

    assert reset_password(user, "654321").status_code == 400


def test_reset_code_of_other_user_is_rejected(make_user, db_conn):
    owner, attacker = make_user(), make_user()
    add_reset_code(db_conn, owner, "123456")

    assert reset_password(attacker, "123456").status_code == 400


def test_reset_password_does_not_reveal_whether_account_exists(make_user):
    user = make_user()
    existing = reset_password(user, "000000")
    unknown = reset_password({"email": "nie-ma-takiego@example.invalid"}, "000000")

    assert existing.status_code == unknown.status_code == 400
    assert existing.json() == unknown.json()


def test_session_signed_with_old_key_is_rejected(make_user):
    user = make_user(is_admin=True)
    cookie = forge_cookie({"user_id": user["id"], "is_admin": True, "status": "active"}, '"super-tajny-klucz"')

    response = requests.get(
        f"{WEBAPP_URL}/api/admin/pending_users", cookies={SESSION_COOKIE: cookie}, timeout=15,
    )
    assert response.status_code in (401, 403)


def test_session_without_password_fingerprint_is_rejected(make_user):
    user = make_user()
    cookie = forge_cookie({"user_id": user["id"], "status": "active"}, '__import__("os").environ["SECRET_KEY"]')

    response = requests.get(f"{WEBAPP_URL}/api/me", cookies={SESSION_COOKIE: cookie}, timeout=15)
    assert response.status_code == 401


def test_tampered_session_cookie_is_rejected(make_user, login):
    session = login(make_user())
    cookie = session.cookies.get(SESSION_COOKIE)
    tampered = cookie[:-2] + ("aa" if not cookie.endswith("aa") else "bb")

    response = requests.get(f"{WEBAPP_URL}/api/me", cookies={SESSION_COOKIE: tampered}, timeout=15)
    assert response.status_code == 401


@pytest.mark.parametrize("value", ["", "krotki-klucz"])
def test_application_refuses_to_start_without_strong_secret_key(value):
    result = compose("exec", "-T", "-e", f"SECRET_KEY={value}", "webapp", "python", "-c", "import app")

    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_user_sees_only_products_of_own_store(make_client, make_user, login, db_conn):
    own, other = make_client(create_tables=True), make_client(create_tables=True)
    session = login(make_user(status="active", client_id=own["id"]))

    with db_conn.cursor() as cur:
        cur.execute(f"INSERT INTO {own['prefix']}_products (sku, name) VALUES ('WLASNY', 'Moj produkt')")
        cur.execute(f"INSERT INTO {other['prefix']}_products (sku, name) VALUES ('OBCY-1', 'Obcy'), ('OBCY-2', 'Obcy')")

    response = session.get(f"{WEBAPP_URL}/api/products", params={"store_prefix": other["prefix"]}, timeout=15)
    skus = [product["sku"] for product in response.json()["data"]]

    assert skus == ["WLASNY"]
    assert session.get(f"{WEBAPP_URL}/api/products/2", timeout=15).status_code == 404


@pytest.mark.parametrize("sort", ["name; DROP TABLE users", "id) OR (1=1", "password_hash"])
def test_sort_parameter_cannot_inject_sql(make_client, make_user, login, db_conn, sort):
    client = make_client(create_tables=True)
    session = login(make_user(status="active", client_id=client["id"]))

    response = session.get(
        f"{WEBAPP_URL}/api/products", params={"sort": sort, "order": "ASC; DROP TABLE users"}, timeout=15,
    )

    assert response.status_code == 200
    with db_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.users')")
        assert cur.fetchone()[0] == "users"


@pytest.mark.parametrize("path,payload,allowed,needs_login", RATE_LIMITS)
def test_rate_limit_is_enforced(make_user, login, path, payload, allowed, needs_login):
    client = login(make_user()) if needs_login else requests.Session()

    for attempt in range(allowed):
        response = client.post(f"{WEBAPP_URL}{path}", json=payload, timeout=15)
        assert response.status_code != 429, f"Limit zadziałał za wcześnie, przy żądaniu nr {attempt + 1}"

    blocked = client.post(f"{WEBAPP_URL}{path}", json=payload, timeout=15)
    assert blocked.status_code == 429
    assert blocked.json()["ok"] is False


def test_spoofed_forwarded_header_does_not_bypass_rate_limit():
    responses = [
        requests.post(
            f"{FRONTEND_URL}/api/login",
            json={"username": "nie-ma-takiego", "password": "zle-haslo"},
            headers={"X-Forwarded-For": f"203.0.113.{number}"},
            timeout=15,
        ).status_code
        for number in range(1, 12)
    ]
    assert responses[-1] == 429
