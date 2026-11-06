import json
import uuid

import pytest
import requests

try:
    from tests.security.helpers import WEBAPP_URL, run_python
except ImportError:
    from helpers import WEBAPP_URL, run_python

pytestmark = pytest.mark.usefixtures("clean_rate_limits")

BLOCKED_URLS = [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://0.0.0.0/",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://2130706433/",
    "http://0x7f.0.0.1/",
    "http://webapp:6767/",
    "http://ecommerce-db:5432/",
    "http://redis:6379/",
    "http://user:haslo@127.0.0.1/",
    "http://[::1",
    "http://exa\u0000mple.com/",
    "file:///etc/passwd",
    "ftp://example.com/plik",
    "gopher://127.0.0.1:6379/",
]
SCHEMELESS_BLOCKED_URLS = ["127.0.0.1:8080", "localhost", "webapp:6767", "10.0.0.1/x"]
ALLOWED_URLS = ["https://example.com/feed.csv", "http://example.com/", "example.com", "www.example.com"]
NOT_WEBSITES = [
    "to nie jest link", "abc", "sklep", "http://abc", "https://", "sklep.p", "sklep,pl",
    "nie-ma-takiej-strony-xyz123.invalid", "https://nie-ma-takiej-strony-xyz123.invalid/sklep",
]
IMAGE_PROXY_BLOCKED_URLS = [url for url in BLOCKED_URLS if url.startswith("http://") and "\u0000" not in url]

URL_SAFETY_MODULES = {
    "ai_generator": ("core.url_safety", {}),
    "airflow-worker": ("ecommerce_price_comparer.utilities.url_safety", {"PYTHONPATH": "/opt/airflow/dags"}),
}
WORKER_ENV = {"PYTHONPATH": "/opt/airflow/dags"}
MAX_COMPETITOR_URLS = 5


def upload_form(session, **fields):
    return session.post(
        f"{WEBAPP_URL}/api/upload_onboarding_file",
        data={"source_type": "url", "file_format": "csv", "source_url": "https://example.com/a.csv", **fields},
        timeout=30,
    )


@pytest.mark.parametrize("url", BLOCKED_URLS + SCHEMELESS_BLOCKED_URLS)
def test_register_rejects_internal_url(db_conn, url):
    email = f"sec_{uuid.uuid4().hex[:10]}@example.invalid"
    try:
        response = requests.post(
            f"{WEBAPP_URL}/api/register",
            json={
                "first_name": "Jan", "last_name": "Test", "email": email, "password": "TajneHaslo123456",
                "company_domain": "example.invalid", "urls": [url],
            },
            timeout=30,
        )
        assert response.status_code == 400
        assert "Niedozwolony adres" in response.json()["error"]
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM onboarding_requests WHERE user_id IN (SELECT id FROM users WHERE email = %s)", (email,))
            cur.execute("DELETE FROM users WHERE email = %s", (email,))


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_onboarding_rejects_internal_source_url(make_user, login, url):
    session = login(make_user(status="onboarding_required"))
    response = upload_form(session, source_url=url)

    assert response.status_code == 400
    assert "Niedozwolony adres" in response.json()["error"]


@pytest.mark.parametrize("url", SCHEMELESS_BLOCKED_URLS)
def test_onboarding_rejects_internal_website_url(make_user, login, url):
    session = login(make_user(status="onboarding_required"))
    response = upload_form(session, website_url=url)

    assert response.status_code == 400
    assert "Niedozwolony adres" in response.json()["error"]


@pytest.mark.parametrize("url", ALLOWED_URLS)
def test_onboarding_accepts_public_url(make_user, login, url):
    session = login(make_user(status="onboarding_required"))
    response = upload_form(session, website_url=url)

    assert "Niedozwolony adres" not in response.text
    assert "Nie znaleziono strony" not in response.text


@pytest.mark.parametrize("url", NOT_WEBSITES)
def test_onboarding_rejects_text_that_is_not_a_website(make_user, login, url):
    session = login(make_user(status="onboarding_required"))
    response = upload_form(session, website_url=url)

    assert response.status_code == 400


@pytest.mark.parametrize("url", NOT_WEBSITES)
def test_register_rejects_text_that_is_not_a_website(db_conn, url):
    email = f"sec_{uuid.uuid4().hex[:10]}@example.invalid"
    try:
        response = requests.post(
            f"{WEBAPP_URL}/api/register",
            json={
                "first_name": "Jan", "last_name": "Test", "email": email, "password": "TajneHaslo123456",
                "company_domain": "example.invalid", "urls": [url],
            },
            timeout=30,
        )
        assert response.status_code == 400
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM onboarding_requests WHERE user_id IN (SELECT id FROM users WHERE email = %s)", (email,))
            cur.execute("DELETE FROM users WHERE email = %s", (email,))


def competitor_urls(count):
    return [f"https://sklep{number}.example.invalid" for number in range(count)]


def test_register_rejects_too_many_competitor_urls(db_conn):
    email = f"sec_{uuid.uuid4().hex[:10]}@example.invalid"
    try:
        response = requests.post(
            f"{WEBAPP_URL}/api/register",
            json={
                "first_name": "Jan", "last_name": "Test", "email": email, "password": "TajneHaslo123456",
                "company_domain": "example.invalid", "urls": competitor_urls(MAX_COMPETITOR_URLS + 1),
            },
            timeout=30,
        )
        assert response.status_code == 400
        assert "maksymalnie" in response.json()["error"]
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM onboarding_requests WHERE user_id IN (SELECT id FROM users WHERE email = %s)", (email,))
            cur.execute("DELETE FROM users WHERE email = %s", (email,))


@pytest.mark.parametrize("url", IMAGE_PROXY_BLOCKED_URLS)
def test_image_proxy_rejects_internal_url(make_user, login, url):
    session = login(make_user())
    response = session.get(f"{WEBAPP_URL}/api/image-proxy", params={"url": url}, timeout=30)

    assert response.status_code == 400


@pytest.mark.parametrize("service", URL_SAFETY_MODULES)
def test_url_helper_blocks_internal_addresses(service):
    module, env = URL_SAFETY_MODULES[service]
    results = run_python(service, f"""
import json
from {module} import is_public_url
print(json.dumps({{url: is_public_url(url) for url in {BLOCKED_URLS!r}}}))
""", env=env)

    allowed = [url for url, is_public in results.items() if is_public]
    assert not allowed, f"{service} nie blokuje: {allowed}"


@pytest.mark.parametrize("service", URL_SAFETY_MODULES)
def test_url_helper_allows_public_addresses(service):
    module, env = URL_SAFETY_MODULES[service]
    urls = ["https://example.com/feed.csv", "http://example.com/"]
    results = run_python(service, f"""
import json
from {module} import is_public_url
print(json.dumps({{url: is_public_url(url) for url in {urls!r}}}))
""", env=env)

    assert all(results.values())


@pytest.mark.parametrize("service", URL_SAFETY_MODULES)
def test_redirect_to_internal_address_is_blocked(service):
    module, env = URL_SAFETY_MODULES[service]
    result = run_python(service, f"""
import json
import {module} as safety

calls = []

class Redirect:
    is_redirect = True
    headers = {{"Location": "http://127.0.0.1/sekret"}}
    def close(self):
        pass

def fake_request(method, url, **kwargs):
    calls.append(url)
    return Redirect()

safety.requests.request = fake_request

try:
    safety.safe_get("https://example.com/")
    blocked = False
except safety.BlockedUrlError:
    blocked = True

print(json.dumps({{"blocked": blocked, "calls": calls}}))
""", env=env)

    assert result["blocked"] is True
    assert result["calls"] == ["https://example.com/"]


def test_redirect_loop_is_stopped():
    result = run_python("ai_generator", """
import json
import core.url_safety as safety

calls = []

class Redirect:
    is_redirect = True
    headers = {"Location": "https://example.com/dalej"}
    def close(self):
        pass

def fake_request(method, url, **kwargs):
    calls.append(url)
    return Redirect()

safety.requests.request = fake_request

try:
    safety.safe_get("https://example.com/")
    blocked = False
except safety.BlockedUrlError:
    blocked = True

print(json.dumps({"blocked": blocked, "calls": len(calls)}))
""")

    assert result["blocked"] is True
    assert result["calls"] <= 6


def test_sitemap_finder_does_not_read_internal_addresses():
    result = run_python("ai_generator", """
import json
from core.sitemap_finder import is_valid_sitemap
print(json.dumps(is_valid_sitemap("http://webapp:6767/api/me")))
""")

    assert result is False


def test_data_ingestor_does_not_read_internal_addresses():
    result = run_python("airflow-worker", """
import json
from ecommerce_price_comparer.utilities.url_safety import BlockedUrlError
from processors.data_ingestor import DataExtractor

config = {"store_name": "test", "source_type": "url", "source_path": "http://webapp:6767/x.csv", "file_format": "csv"}
try:
    list(DataExtractor(config).extract())
    blocked = False
except BlockedUrlError:
    blocked = True

print(json.dumps(blocked))
""", env=WORKER_ENV)

    assert result is True


def test_scrapy_middleware_blocks_internal_requests():
    result = run_python("airflow-worker", """
import json
from scrapy import Request
from scrapy.exceptions import IgnoreRequest
from ecommerce_price_comparer.middlewares import BlockInternalRequestsMiddleware

middleware = BlockInternalRequestsMiddleware()

def is_blocked(url):
    try:
        middleware.process_request(Request(url), None)
        return False
    except IgnoreRequest:
        return True

print(json.dumps({"internal": is_blocked("http://ai_generator:8080/"), "public": is_blocked("https://example.com/")}))
""", env=WORKER_ENV)

    assert result == {"internal": True, "public": False}
