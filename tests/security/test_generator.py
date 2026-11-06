import re
import uuid

import pytest

try:
    from tests.security.helpers import PROJECT_DIR, TEST_PASSWORD, WEBAPP_URL, compose_services, run_python
except ImportError:
    from helpers import PROJECT_DIR, TEST_PASSWORD, WEBAPP_URL, compose_services, run_python

pytestmark = pytest.mark.usefixtures("clean_rate_limits")

GENERATION_SETUP = """
import json
import os
import tempfile
from unittest import mock

import run_system

workdir = tempfile.mkdtemp()
os.chdir(workdir)

run_system.SPIDERS_OUTPUT_DIR = os.path.join(workdir, "spiders")
client_dir = os.path.join(run_system.SPIDERS_OUTPUT_DIR, "lambfield_com")
os.makedirs(client_dir)
existing_spider = os.path.join(client_dir, "calvado_spider.py")
with open(existing_spider, "w") as f:
    f.write("ORYGINAL")


def fake_dataset(urls, path):
    with open(path, "w") as f:
        f.write("<html></html>")


def fake_llm(dataset_path, map_path):
    with open(map_path, "w") as f:
        json.dump({"name": "h1"}, f)


def generate(request_id, store_slug, url, cur=None, llm=fake_llm, dataset=fake_dataset):
    with mock.patch.object(run_system, "find_sitemap", return_value=["https://evil.example/s.xml"]), \\
         mock.patch.object(run_system, "get_product_links", return_value={"https://evil.example/p"}), \\
         mock.patch.object(run_system, "build_llm_dataset", dataset), \\
         mock.patch.object(run_system, "generate_scraper_from_html", llm), \\
         mock.patch.object(run_system, "register_generated_spider") as register:
        run_system.uruchom_autonomiczny_potok(cur or mock.MagicMock(), request_id, 1, None, store_slug, url)
    return register.call_args.kwargs if register.call_args else None
"""


def test_registration_cannot_overwrite_spider_of_existing_client():
    result = run_python("ai_generator", GENERATION_SETUP + """
generate(77, "lambfield_com", "https://calvado.evil.example/")
print(json.dumps(open(existing_spider).read()))
""")
    assert result == "ORYGINAL"


def test_generated_spiders_have_names_unique_per_request():
    result = run_python("ai_generator", GENERATION_SETUP + """
first = generate(77, "sklep_a", "https://calvado.evil.example/")
second = generate(78, "sklep_b", "https://calvado.evil.example/")
print(json.dumps([first["spider_name"], second["spider_name"], first["spider_path"], second["spider_path"]]))
""")
    first_name, second_name, first_path, second_path = result

    assert first_name != second_name
    assert first_name != "calvado_spider"
    assert first_path != second_path


def test_spider_name_in_file_matches_registry():
    result = run_python("ai_generator", GENERATION_SETUP + """
registered = generate(77, "sklep_a", "https://calvado.evil.example/")
print(json.dumps([registered["spider_name"], open(registered["spider_path"]).read()]))
""")
    spider_name, source = result

    assert f'name = "{spider_name}"' in source


@pytest.mark.parametrize("url", ["example.com", "https://a-very-long-domain-name-used-by-an-attacker-to-break-limits-of-columns.example.com/"])
def test_spider_name_is_valid_for_database_columns(url):
    result = run_python("ai_generator", GENERATION_SETUP + f"""
registered = generate(77, "sklep_a", {url!r})
print(json.dumps(registered["spider_name"]))
""")

    assert len(result) <= 50
    assert result.replace("_", "").isalnum()
    assert result != "r77__spider"


def test_generator_writes_nothing_for_request_that_no_longer_exists():
    result = run_python("ai_generator", GENERATION_SETUP + """
cur = mock.MagicMock()
cur.fetchone.return_value = None
registered = generate(77, "sklep_a", "https://calvado.evil.example/", cur=cur)
print(json.dumps([registered, os.path.exists(os.path.join(run_system.SPIDERS_OUTPUT_DIR, "sklep_a"))]))
""")
    assert result == [None, False]


def test_generator_starts_when_competitor_links_are_sent_at_registration(db_conn):
    email = f"sec_{uuid.uuid4().hex[:10]}@example.invalid"
    try:
        result = run_python("webapp", f"""
import json
from unittest import mock
from app import app
import routes.auth as auth

payload = {{
    "first_name": "Jan", "last_name": "Test", "email": {email!r}, "password": {TEST_PASSWORD!r},
    "company_domain": "sklep.example.com", "urls": ["https://example.com"],
}}
with mock.patch.object(auth, "_trigger_scraper_generator_async") as trigger:
    response = app.test_client().post("/api/register", json=payload)
print(json.dumps({{"status": response.status_code, "kwargs": trigger.call_args.kwargs if trigger.call_args else None}}))
""")
        assert result["status"] == 200, result
        assert result["kwargs"]["urls"] == ["https://example.com"]
        assert result["kwargs"]["store_slug"] == "sklep_example_com"
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email = %s", (email,))


def test_register_accepts_maximum_number_of_competitor_urls(db_conn):
    email = f"sec_{uuid.uuid4().hex[:10]}@example.invalid"
    urls = [f"https://example.com/{number}" for number in range(5)]
    try:
        result = run_python("webapp", f"""
import json
from unittest import mock
from app import app
import routes.auth as auth

payload = {{
    "first_name": "Jan", "last_name": "Test", "email": {email!r}, "password": {TEST_PASSWORD!r},
    "company_domain": "sklep.example.com", "urls": {urls!r},
}}
with mock.patch.object(auth, "_trigger_scraper_generator_async") as trigger:
    response = app.test_client().post("/api/register", json=payload)
print(json.dumps({{"status": response.status_code, "urls": trigger.call_args.kwargs["urls"] if trigger.call_args else None}}))
""")
        assert result == {"status": 200, "urls": urls}
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email = %s", (email,))


def test_onboarding_form_cannot_change_competitor_urls(make_user, login, db_conn):
    links = '["https://example.com"]'
    user = make_user(status="onboarding_required")
    with db_conn.cursor() as cur:
        cur.execute("UPDATE users SET competitor_urls = %s::jsonb WHERE id = %s", (links, user["id"]))
        cur.execute(
            """
            INSERT INTO onboarding_requests (user_id, requested_store_name, status, competitor_urls)
            VALUES (%s, 'Sklep', 'onboarding_required', %s::jsonb)
            """,
            (user["id"], links),
        )

    response = login(user).post(
        f"{WEBAPP_URL}/api/onboarding/submit",
        data={
            "source_type": "url", "file_format": "csv", "source_url": "https://example.com/a.csv",
            "competitor_urls": '["https://www.iana.org"]', "urls": "https://www.iana.org",
        },
        timeout=30,
    )

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.competitor_urls::text, r.competitor_urls::text
            FROM users u
            JOIN onboarding_requests r ON r.user_id = u.id
            WHERE u.id = %s
            """,
            (user["id"],),
        )
        stored = cur.fetchone()

    assert response.status_code == 200, response.text
    assert stored == (links, links)


def test_onboarding_form_does_not_start_generator_again(make_user, db_conn):
    user = make_user(status="onboarding_required")
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO onboarding_requests (user_id, requested_store_name, status) VALUES (%s, 'Sklep', 'onboarding_required')",
            (user["id"],),
        )

    result = run_python("webapp", f"""
import json
from unittest import mock
from app import app
import routes.auth as auth

client = app.test_client()
client.post("/api/login", json={{"username": {user["username"]!r}, "password": {user["password"]!r}}})
form = {{
    "source_type": "url", "file_format": "csv", "source_url": "https://example.com/a.csv",
    "competitor_urls": json.dumps(["https://example.com"]),
}}
with mock.patch.object(auth, "_trigger_scraper_generator_async") as trigger:
    response = client.post("/api/onboarding/submit", data=form)
print(json.dumps({{"status": response.status_code, "called": trigger.called}}))
""")
    assert result == {"status": 200, "called": False}


def test_generator_does_not_use_old_map_when_llm_returns_nothing():
    result = run_python("ai_generator", GENERATION_SETUP + """
os.makedirs("data_output")
with open("data_output/selectors_map.json", "w") as f:
    json.dump({"name": "stara-mapa"}, f)

registered = generate(77, "sklep_a", "https://calvado.evil.example/", llm=lambda dataset, map_path: None)
print(json.dumps([registered, os.path.exists(os.path.join(run_system.SPIDERS_OUTPUT_DIR, "sklep_a"))]))
""")
    assert result == [None, False]


def test_llm_error_stops_generation_and_writes_no_spider():
    result = run_python("ai_generator", GENERATION_SETUP + """
def broken_llm(dataset, map_path):
    raise RuntimeError("Error code: 404 - model not found")

try:
    generate(77, "sklep_a", "https://calvado.evil.example/", llm=broken_llm)
    raised = False
except RuntimeError:
    raised = True
print(json.dumps([raised, os.path.exists(os.path.join(run_system.SPIDERS_OUTPUT_DIR, "sklep_a"))]))
""")
    assert result == [True, False]


def test_llm_is_not_called_without_html_samples():
    result = run_python("ai_generator", GENERATION_SETUP + """
llm = mock.MagicMock()
registered = generate(77, "sklep_a", "https://calvado.evil.example/", llm=llm, dataset=lambda urls, path: open(path, "w").close())
print(json.dumps([registered, llm.called]))
""")
    assert result == [None, False]


def test_each_generation_uses_own_working_files():
    result = run_python("ai_generator", GENERATION_SETUP + """
paths = []

def recording_llm(dataset, map_path):
    paths.append(map_path)
    fake_llm(dataset, map_path)

generate(77, "sklep_a", "https://calvado.evil.example/", llm=recording_llm)
generate(77, "sklep_a", "https://calvado.evil.example/", llm=recording_llm)
print(json.dumps([paths[0] != paths[1], os.path.exists(paths[0])]))
""")
    assert result == [True, False]


GROQ_MANAGERS = [("ai_generator", "core.groq_manager"), ("recommendation_service", "groq_manager")]
GROQ_LIMIT_CHECK = """
import json
from unittest import mock
import {module} as module

manager = module.GroqManager()
calls = []

class LimitError(Exception):
    response = type("Response", (), {{"headers": {{"retry-after": "7"}}}})()

def call(client):
    calls.append(1)
    if len(calls) == 1:
        raise LimitError({message!r})
    return object()

with mock.patch.object(manager, "get_client", return_value=(None, "KLUCZ")), \\
     mock.patch.object(manager, "mark_exhausted") as exhausted, \\
     mock.patch.object(module.time, "sleep") as sleep:
    try:
        manager.execute_with_fallback(call)
        failed = False
    except LimitError:
        failed = True

print(json.dumps({{"exhausted": exhausted.called, "slept": sleep.called, "failed": failed}}))
"""


@pytest.mark.parametrize("service,module", GROQ_MANAGERS)
def test_minute_limit_waits_and_does_not_block_key(service, module):
    result = run_python(service, GROQ_LIMIT_CHECK.format(
        module=module,
        message="Error code: 429 - Rate limit reached on tokens per minute (TPM): Limit 8000",
    ))
    assert result == {"exhausted": False, "slept": True, "failed": False}


@pytest.mark.parametrize("service,module", GROQ_MANAGERS)
def test_daily_limit_blocks_key(service, module):
    result = run_python(service, GROQ_LIMIT_CHECK.format(
        module=module,
        message="Error code: 429 - Rate limit reached on tokens per day (TPD): Limit 200000",
    ))
    assert result == {"exhausted": True, "slept": False, "failed": False}


@pytest.mark.parametrize("service,module", GROQ_MANAGERS)
def test_too_large_request_fails_without_waiting(service, module):
    result = run_python(service, GROQ_LIMIT_CHECK.format(
        module=module,
        message="Error code: 413 - Request too large for model on tokens per minute (TPM): Limit 8000",
    ))
    assert result == {"exhausted": False, "slept": False, "failed": True}


def configured_model(path):
    source = (PROJECT_DIR / path).read_text(encoding="utf-8")
    return re.search(r'model="([^"]+)"', source).group(1)


@pytest.mark.slow
@pytest.mark.parametrize("service,path", [
    ("ai_generator", "autonomiczny_scraper/core/prompt.py"),
    ("recommendation_service", "price-recomendation/main.py"),
])
def test_configured_llm_model_is_available(service, path):
    model = configured_model(path)
    result = run_python(service, """
import json
import os
from groq import Groq

try:
    print(json.dumps([m.id for m in Groq(api_key=os.environ["GROQ_API_KEY_1"]).models.list().data]))
except Exception as e:
    print(json.dumps({"error": type(e).__name__}))
""")

    if isinstance(result, dict):
        pytest.skip(f"Nie udało się pobrać listy modeli: {result['error']}")

    assert model in result, f"Modelu {model} nie ma już w Groq. Dostępne: {result}"


def test_sitemap_processing_stops_at_limit():
    result = run_python("ai_generator", """
import json
from unittest import mock
import core.sitemap_processor as processor

calls = []

class Response:
    def __init__(self, url):
        number = len(calls)
        self.content = f"<loc>https://evil.example/{number}.xml</loc>".encode()
    def raise_for_status(self):
        pass

def endless(url, **kwargs):
    calls.append(url)
    return Response(url)

with mock.patch.object(processor, "safe_get", endless):
    processor.get_product_links(["https://evil.example/0.xml"])

print(json.dumps(len(calls)))
""")
    assert result <= 500


def test_generator_memory_is_limited():
    assert compose_services()["ai_generator"].get("mem_limit")


def test_dag_quotes_values_passed_to_shell():
    source = (PROJECT_DIR / "airflow" / "dags" / "Dag.py").read_text(encoding="utf-8")

    assert "scrapy crawl {shlex.quote(spider_name)}" in source
    assert "-a target_table={shlex.quote(target_table)}" in source
    assert "-a store_prefix={shlex.quote(prefix)}" in source
