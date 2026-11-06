import shutil
import uuid

import pytest
import requests

try:
    from tests.security.helpers import PROJECT_DIR, WEBAPP_URL, run_python
except ImportError:
    from helpers import PROJECT_DIR, WEBAPP_URL, run_python

pytestmark = pytest.mark.usefixtures("clean_rate_limits")

SPIDERS_DIR = PROJECT_DIR / "ecommerce_price_comparer" / "spiders"
UPLOAD_DIR = PROJECT_DIR / "storage" / "file_folder"
GENERATOR_URL = "http://localhost:8080"


def reject(admin, user_id):
    return run_python("webapp", f"""
import json
from unittest import mock
from app import app
import routes.auth as auth

client = app.test_client()
client.post("/api/login", json={{"username": {admin["username"]!r}, "password": {admin["password"]!r}}})
with mock.patch.object(auth, "send_email") as send:
    response = client.post("/api/admin/reject_user", json={{"user_id": {user_id}}})
print(json.dumps({{
    "status": response.status_code,
    "body": response.get_json(),
    "mail_to": send.call_args.kwargs.get("to_email") if send.call_args else None,
}}))
""")


def count(db_conn, query, value):
    with db_conn.cursor() as cur:
        cur.execute(query, (value,))
        return cur.fetchone()[0]


@pytest.fixture
def admin(make_user):
    return make_user(is_admin=True)


@pytest.fixture
def spider_dir():
    path = SPIDERS_DIR / f"sec_{uuid.uuid4().hex[:10]}"
    (path / "__pycache__").mkdir(parents=True)
    (path / "__init__.py").touch()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def application(make_user, db_conn, spider_dir):
    user = make_user(status="pending_admin")
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO onboarding_requests (user_id, requested_store_name, status, competitor_urls)
            VALUES (%s, 'Sklep', 'pending_admin', '["https://example.com"]'::jsonb)
            RETURNING id
            """,
            (user["id"],),
        )
        request_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO onboarding_sources (request_id, source_kind, source_url) VALUES (%s, 'url', 'https://example.com/a.csv')",
            (request_id,),
        )
        cur.execute(
            "INSERT INTO onboarding_field_mappings (request_id, external_field, internal_field) VALUES (%s, 'NAZWA', 'name')",
            (request_id,),
        )
        spider_name = f"r{request_id}_sklep_spider"
        cur.execute(
            """
            INSERT INTO scraper_registry (request_id, store_slug, competitor_url, competitor_name, spider_name, spider_module, spider_path, status)
            VALUES (%s, %s, 'https://example.com', 'sklep', %s, %s, %s, 'generated')
            """,
            (request_id, spider_dir.name, spider_name, f"{spider_dir.name}.{spider_name}", str(spider_dir / f"{spider_name}.py")),
        )
        cur.execute(
            "INSERT INTO scraper_logs (user_id, request_id, url, step, status, message) VALUES (%s, %s, NULL, 'AI', 'info', 'log')",
            (user["id"], request_id),
        )

    for name in (f"r{request_id}_sklep_spider.py", f"r{request_id}_sklep_links.txt", f"__pycache__/r{request_id}_sklep_spider.cpython-312.pyc"):
        (spider_dir / name).touch()

    upload = UPLOAD_DIR / f"user_{user['id']}_produkty.csv"
    upload.touch()

    yield {"user": user, "request_id": request_id, "dir": spider_dir, "upload": upload}

    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM scraper_registry WHERE store_slug = %s", (spider_dir.name,))

    if upload.exists():
        upload.unlink()


def test_rejection_removes_all_traces_of_application(admin, application, db_conn):
    user, request_id = application["user"], application["request_id"]

    result = reject(admin, user["id"])

    assert result["status"] == 200, result
    assert "warning" not in result["body"], result
    assert result["mail_to"] == user["email"]

    assert not application["dir"].exists()
    assert not application["upload"].exists()
    assert count(db_conn, "SELECT COUNT(*) FROM onboarding_requests WHERE user_id = %s", user["id"]) == 0
    assert count(db_conn, "SELECT COUNT(*) FROM onboarding_sources WHERE request_id = %s", request_id) == 0
    assert count(db_conn, "SELECT COUNT(*) FROM onboarding_field_mappings WHERE request_id = %s", request_id) == 0
    assert count(db_conn, "SELECT COUNT(*) FROM scraper_registry WHERE spider_name = %s", f"r{request_id}_sklep_spider") == 0
    assert count(db_conn, "SELECT COUNT(*) FROM scraper_logs WHERE user_id = %s", user["id"]) == 0


def test_rejected_account_stays_without_application_data(admin, application, db_conn):
    user = application["user"]

    assert reject(admin, user["id"])["status"] == 200

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, company_domain, competitor_urls::text FROM users WHERE id = %s", (user["id"],))
        assert cur.fetchone() == ("rejected", None, "[]")


def test_rejected_user_can_log_in_and_write_to_us(admin, application, login, db_conn):
    user = application["user"]
    assert reject(admin, user["id"])["status"] == 200

    session = login(user)
    me = session.get(f"{WEBAPP_URL}/api/me", timeout=15).json()
    message = session.post(f"{WEBAPP_URL}/api/send_message", json={"message": "Dlaczego?"}, timeout=15)

    try:
        assert me["user"]["status"] == "rejected"
        assert session.get(f"{WEBAPP_URL}/api/products", timeout=15).status_code == 403
        assert message.status_code == 200
        with db_conn.cursor() as cur:
            cur.execute("SELECT message FROM error_logs WHERE user_id = %s", (user["id"],))
            stored = cur.fetchone()[0]
        assert user["email"] in stored and "Dlaczego?" in stored
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM error_logs WHERE user_id = %s", (user["id"],))


def test_rejection_does_not_touch_other_files(admin, application):
    user, request_id, spider_dir = application["user"], application["request_id"], application["dir"]
    other_spider = spider_dir / f"r{request_id}1_inny_spider.py"
    other_upload = UPLOAD_DIR / f"user_{user['id']}1_inny.csv"
    other_spider.touch()
    other_upload.touch()

    try:
        assert reject(admin, user["id"])["status"] == 200
        assert other_spider.exists()
        assert other_upload.exists()
        assert not (spider_dir / f"r{request_id}_sklep_spider.py").exists()
    finally:
        other_upload.unlink()


def test_approved_client_cannot_be_rejected(admin, make_user, make_client, db_conn):
    client = make_client()
    user = make_user(status="active", client_id=client["id"])
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO onboarding_requests (user_id, client_id, requested_store_name, status) VALUES (%s, %s, 'Sklep', 'active')",
            (user["id"], client["id"]),
        )

    result = reject(admin, user["id"])

    assert result["status"] == 409
    with db_conn.cursor() as cur:
        cur.execute("SELECT status FROM users WHERE id = %s", (user["id"],))
        assert cur.fetchone()[0] == "active"


def test_regular_user_cannot_reject_application(application, make_user, login, db_conn):
    session = login(make_user(status="active"))
    response = session.post(f"{WEBAPP_URL}/api/admin/reject_user", json={"user_id": application["user"]["id"]}, timeout=15)

    assert response.status_code == 403
    assert application["dir"].exists()
    assert count(db_conn, "SELECT COUNT(*) FROM onboarding_requests WHERE id = %s", application["request_id"]) == 1


@pytest.mark.parametrize("request_id", ["abc", "1;rm", "*", "..%2F.."])
def test_generator_cleanup_accepts_only_numeric_request_id(request_id):
    response = requests.delete(f"{GENERATOR_URL}/api/requests/{request_id}/spiders", timeout=10)
    assert response.status_code in (404, 422)
