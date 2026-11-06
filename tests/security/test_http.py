import http.client
from urllib.parse import urlparse

import pytest
import requests

try:
    from tests.security.helpers import ENV, FRONTEND_URL, SESSION_COOKIE, WEBAPP_URL, run_python
except ImportError:
    from helpers import ENV, FRONTEND_URL, SESSION_COOKIE, WEBAPP_URL, run_python

pytestmark = pytest.mark.usefixtures("clean_rate_limits")

BASE_URLS = [WEBAPP_URL, FRONTEND_URL]
FOREIGN_ORIGINS = [
    "https://evil.example",
    "https://inny-projekt.uczelnia.pl",
    "http://localhost.evil.example",
    "null",
]
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "frame-ancestors 'none'",
}
OVERSIZED_BODY = 51 * 1024 * 1024
SENSITIVE_PATHS = ["/.env", "/.git/config", "/docker-compose.yaml", "/.env.example", "/api/../.env", "/webapp/app.py"]
SECRET_KEYS = [
    "SECRET_KEY", "APP_DB_PASSWORD", "DB_SUPERUSER_PASSWORD", "AIRFLOW_DB_PASSWORD",
    "REDIS_PASSWORD", "ADMIN_PASSWORD", "CLIENT_PASSWORD", "FERNET_KEY", "GROQ_API_KEY_1",
]


@pytest.mark.parametrize("base_url", BASE_URLS)
@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
def test_cors_does_not_allow_foreign_origin(base_url, origin):
    response = requests.get(f"{base_url}/api/me", headers={"Origin": origin}, timeout=10)
    preflight = requests.options(
        f"{base_url}/api/login",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        timeout=10,
    )

    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Origin" not in preflight.headers


@pytest.mark.parametrize("base_url", BASE_URLS)
@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
def test_foreign_origin_cannot_send_write_requests(base_url, origin):
    response = requests.post(f"{base_url}/api/logout", headers={"Origin": origin}, timeout=10)
    assert response.status_code == 403


@pytest.mark.parametrize("base_url", BASE_URLS)
def test_same_origin_write_requests_work(base_url):
    response = requests.post(
        f"{base_url}/api/login",
        json={"username": "nie-ma-takiego", "password": "zle-haslo"},
        headers={"Origin": base_url},
        timeout=10,
    )
    assert response.status_code == 401


def test_foreign_origin_cannot_change_logged_in_user(make_user, login, db_conn):
    user = make_user(status="active")
    session = login(user)

    response = session.post(
        f"{WEBAPP_URL}/api/subscription/cancel",
        headers={"Origin": "https://inny-projekt.uczelnia.pl"},
        timeout=10,
    )

    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM users WHERE id = %s", (user["id"],))
        status = cur.fetchone()[0]

    assert response.status_code == 403
    assert status == "active"


@pytest.mark.parametrize("path", ["/", "/api/me"])
@pytest.mark.parametrize("header,value", SECURITY_HEADERS.items())
def test_nginx_sends_security_headers(path, header, value):
    response = requests.get(f"{FRONTEND_URL}{path}", timeout=10)
    assert response.headers.get(header) == value


def test_nginx_hides_version():
    response = requests.get(f"{FRONTEND_URL}/", timeout=10)
    assert response.headers.get("Server") == "nginx"


@pytest.mark.parametrize("path", SENSITIVE_PATHS)
def test_sensitive_files_are_not_served(path):
    session = requests.Session()
    request = requests.Request("GET", f"{FRONTEND_URL}{path}").prepare()
    request.url = f"{FRONTEND_URL}{path}"
    text = session.send(request, timeout=10).text

    assert "APP_DB_PASSWORD" not in text
    assert "services:" not in text
    assert not any(ENV[key] in text for key in SECRET_KEYS if ENV.get(key))


def test_api_is_reachable_through_nginx():
    response = requests.get(f"{FRONTEND_URL}/api/me", timeout=10)
    assert response.status_code == 401


def test_session_cookie_has_safe_flags(make_user):
    user = make_user()
    response = requests.post(
        f"{WEBAPP_URL}/api/login",
        json={"username": user["username"], "password": user["password"]},
        timeout=15,
    )
    cookie = response.headers["Set-Cookie"]

    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_session_cookie_is_secure_after_https_is_enabled(make_user):
    if ENV.get("SESSION_COOKIE_SECURE", "false").lower() != "true":
        pytest.skip("Do zrobienia przy HTTPS: ustaw SESSION_COOKIE_SECURE=true w .env")

    user = make_user()
    response = requests.post(
        f"{WEBAPP_URL}/api/login",
        json={"username": user["username"], "password": user["password"]},
        timeout=15,
    )
    assert "Secure" in response.headers["Set-Cookie"]


@pytest.mark.parametrize("base_url", BASE_URLS)
def test_oversized_upload_is_rejected(base_url):
    parsed = urlparse(base_url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=15)
    conn.putrequest("POST", "/api/upload_onboarding_file")
    conn.putheader("Content-Type", "multipart/form-data; boundary=x")
    conn.putheader("Content-Length", str(OVERSIZED_BODY))
    conn.endheaders()

    status = conn.getresponse().status
    conn.close()

    assert status in (401, 413)


def test_oversized_upload_is_rejected_for_logged_in_user(make_user, login):
    session = login(make_user(status="onboarding_required"))
    parsed = urlparse(WEBAPP_URL)
    cookie = session.cookies.get(SESSION_COOKIE)

    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=15)
    conn.putrequest("POST", "/api/upload_onboarding_file")
    conn.putheader("Cookie", f"{SESSION_COOKIE}={cookie}")
    conn.putheader("Content-Type", "multipart/form-data; boundary=x")
    conn.putheader("Content-Length", str(OVERSIZED_BODY))
    conn.endheaders()

    status = conn.getresponse().status
    conn.close()

    assert status == 413


def test_internal_error_details_are_hidden():
    result = run_python("webapp", """
import json
from flask import jsonify
from app import app

@app.route("/__test_error")
def _test_error():
    return jsonify({"ok": False, "error": "SELECT secret FROM users"}), 500

print(json.dumps(app.test_client().get("/__test_error").get_json()))
""")

    assert "SELECT" not in result["error"]
    assert "błąd serwera" in result["error"]


@pytest.mark.parametrize("payload", [{"username": 123}, {"username": ["a"], "password": {}}, []])
def test_malformed_login_does_not_expose_traceback(payload):
    response = requests.post(f"{WEBAPP_URL}/api/login", json=payload, timeout=10)

    assert "Traceback" not in response.text
    assert "psycopg2" not in response.text
    assert 'File "' not in response.text


def test_gunicorn_serves_webapp():
    response = requests.get(f"{WEBAPP_URL}/api/me", timeout=10)
    assert response.headers.get("Server", "").startswith("gunicorn")
