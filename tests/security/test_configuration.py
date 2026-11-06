import ast
import importlib.util
import json
import re
import subprocess

import pytest

try:
    from tests.security.helpers import ENV, EXAMPLE_ENV, PROJECT_DIR, compose_services
except ImportError:
    from helpers import ENV, EXAMPLE_ENV, PROJECT_DIR, compose_services

COMPOSE_TEXT = (PROJECT_DIR / "docker-compose.yaml").read_text(encoding="utf-8")

PUBLIC_PORTS = {"frontend": {"80", "443"}}
NON_ROOT_SERVICES = ["webapp", "ai_generator", "recommendation_service"]
READ_ONLY_SOURCE_SERVICES = ["webapp", "recommendation_service"]
AIRFLOW_SERVICES = [
    "airflow-webserver", "airflow-scheduler", "airflow-worker",
    "airflow-triggerer", "airflow-dag-processor",
]
SECRETS_HIDDEN_FROM_AIRFLOW = [
    "SECRET_KEY", "ADMIN_PASSWORD", "CLIENT_PASSWORD", "DB_SUPERUSER_PASSWORD",
    "GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3", "EROCH_GMAIL",
]
INSECURE_COMPOSE_FRAGMENTS = [
    "airflow:airflow@",
    "POSTGRES_PASSWORD: airflow",
    "airflow_jwt_secret",
    "_AIRFLOW_WWW_USER_PASSWORD:-",
    "redis://:@",
    "redis://redis:",
    "env_file:",
]
LONG_SECRETS = [
    "SECRET_KEY", "APP_DB_PASSWORD", "DB_SUPERUSER_PASSWORD", "AIRFLOW_DB_PASSWORD",
    "AIRFLOW__API_AUTH__JWT_SECRET", "REDIS_PASSWORD",
]
PASSWORDS = ["ADMIN_PASSWORD", "CLIENT_PASSWORD", "_AIRFLOW_WWW_USER_PASSWORD"]
UNIQUE_SECRETS = LONG_SECRETS + PASSWORDS
EXAMPLE_BLANK_KEYS = UNIQUE_SECRETS + [
    "FERNET_KEY", "GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3", "EROCH_GMAIL", "GMAIL_USER",
]
WEAK_VALUES = {"admin", "admin123", "airflow", "password", "postgres", "eroch", "klient123"}
SECRET_PATTERNS = [
    r"gsk_[A-Za-z0-9]{20}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY",
    r"admin123",
    r"klient123",
    r"super-tajny-klucz",
    r"[\"']eroch[\"']",
]
FILES_FETCHING_USER_URLS = [
    "autonomiczny_scraper/core/sitemap_finder.py",
    "autonomiczny_scraper/core/sitemap_processor.py",
    "autonomiczny_scraper/core/odchudzacz.py",
    "processors/data_ingestor.py",
]


def repo_files():
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=PROJECT_DIR, capture_output=True, check=True,
    ).stdout.decode()

    for name in filter(None, output.split("\0")):
        path = PROJECT_DIR / name
        if path.is_file() and not name.startswith("tests/security/") and path.stat().st_size < 5_000_000:
            yield name, path


@pytest.fixture(scope="module")
def services():
    return compose_services()


def test_only_public_services_listen_on_all_interfaces(services):
    exposed = []
    for name, service in services.items():
        for port in service.get("ports", []):
            is_public = port.get("host_ip") != "127.0.0.1"
            if is_public and str(port.get("published")) not in PUBLIC_PORTS.get(name, set()):
                exposed.append(f"{name}:{port.get('published')}")

    assert not exposed, f"Porty dostępne z sieci: {exposed}. Użyj 127.0.0.1:PORT:PORT"


@pytest.mark.parametrize("fragment", INSECURE_COMPOSE_FRAGMENTS)
def test_compose_has_no_insecure_defaults(fragment):
    assert fragment not in COMPOSE_TEXT


def test_airflow_examples_are_disabled(services):
    env = services["airflow-worker"]["environment"]
    assert env["AIRFLOW__CORE__LOAD_EXAMPLES"] == "false"


@pytest.mark.parametrize("service", AIRFLOW_SERVICES)
@pytest.mark.parametrize("secret", SECRETS_HIDDEN_FROM_AIRFLOW)
def test_airflow_does_not_receive_application_secrets(services, service, secret):
    assert secret not in (services[service].get("environment") or {})


def test_superuser_password_is_only_in_database_container(services):
    secret = ENV["DB_SUPERUSER_PASSWORD"]
    leaking = [
        name for name, service in services.items()
        if name != "ecommerce-db" and secret in json.dumps(service.get("environment") or {})
    ]
    assert not leaking, f"Hasło superużytkownika trafia do: {leaking}"


def test_application_does_not_connect_as_superuser():
    assert ENV["APP_DB_USER"] != ENV["DB_SUPERUSER"]
    assert ENV["APP_DB_PASSWORD"] != ENV["DB_SUPERUSER_PASSWORD"]


@pytest.mark.parametrize("service", NON_ROOT_SERVICES)
def test_services_are_configured_without_root(services, service):
    user = services[service].get("user", "")
    assert user and not user.startswith("0:") and user != "root"


@pytest.mark.parametrize("service", READ_ONLY_SOURCE_SERVICES)
def test_application_source_is_mounted_read_only(services, service):
    mounts = [v for v in services[service]["volumes"] if v.get("target") == "/app"]
    assert mounts and all(v.get("read_only") for v in mounts)


def test_env_is_not_tracked_by_git():
    tracked = subprocess.run(
        ["git", "ls-files", ".env"], cwd=PROJECT_DIR, capture_output=True, text=True,
    ).stdout
    ignored = subprocess.run(["git", "check-ignore", ".env"], cwd=PROJECT_DIR, capture_output=True)

    assert not tracked.strip()
    assert ignored.returncode == 0


def test_env_and_example_have_the_same_variables():
    assert set(ENV) == set(EXAMPLE_ENV)


def test_compose_variables_are_documented_in_example():
    used = set(re.findall(r"(?<!\$)\$\{([A-Za-z_0-9]+)", COMPOSE_TEXT))
    optional = {"_AIRFLOW_WWW_USER_EMAIL", "AIRFLOW__API_AUTH__JWT_ISSUER"}
    assert not (used - optional - set(EXAMPLE_ENV))


@pytest.mark.parametrize("key", EXAMPLE_BLANK_KEYS)
def test_example_contains_no_real_secret(key):
    assert EXAMPLE_ENV[key] == ""


@pytest.mark.parametrize("key", LONG_SECRETS)
def test_long_secrets_are_strong(key):
    assert len(ENV[key]) >= 32 and ENV[key].lower() not in WEAK_VALUES


@pytest.mark.parametrize("key", PASSWORDS)
def test_passwords_are_strong(key):
    assert len(ENV[key]) >= 12 and ENV[key].lower() not in WEAK_VALUES


def test_secrets_are_unique():
    duplicates = [
        key for key in UNIQUE_SECRETS
        if sum(ENV[other] == ENV[key] for other in UNIQUE_SECRETS) > 1
    ]
    assert not duplicates, f"Te zmienne mają tę samą wartość: {duplicates}"


@pytest.mark.parametrize("pattern", SECRET_PATTERNS)
def test_repo_contains_no_known_secret_pattern(pattern):
    regex = re.compile(pattern.encode())
    found = [name for name, path in repo_files() if regex.search(path.read_bytes())]
    assert not found, f"Podejrzany sekret w plikach: {found}"


def test_repo_contains_no_value_from_env():
    secrets = [ENV[key].encode() for key in EXAMPLE_BLANK_KEYS + ["FERNET_KEY"] if ENV.get(key)]
    found = [
        name for name, path in repo_files()
        if any(secret in path.read_bytes() for secret in secrets)
    ]
    assert not found, f"Wartość z .env znaleziona w plikach: {found}"


def test_schema_seeds_accounts_without_passwords():
    schema = (PROJECT_DIR / "database" / "schema.sql").read_text(encoding="utf-8")
    assert "digest(" not in schema


def test_webapp_runs_production_server_without_debug():
    dockerfile = (PROJECT_DIR / "webapp" / "Dockerfile").read_text(encoding="utf-8")
    app_source = (PROJECT_DIR / "webapp" / "app.py").read_text(encoding="utf-8")

    assert 'CMD ["gunicorn"' in dockerfile
    assert "debug=True" not in app_source


def test_nginx_resolves_webapp_on_every_request():
    nginx = (PROJECT_DIR / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "resolver 127.0.0.11" in nginx
    assert "proxy_pass http://webapp" not in nginx


@pytest.mark.parametrize("path", FILES_FETCHING_USER_URLS)
def test_user_urls_are_fetched_only_through_safe_helper(path):
    source = (PROJECT_DIR / path).read_text(encoding="utf-8")
    assert not re.search(r"requests\.(get|head|post|put|request)\(", source)


def test_frontend_opens_only_http_links():
    source = (
        PROJECT_DIR / "frontend" / "lib" / "features" / "products" / "screens" / "product_details_screen.dart"
    ).read_text(encoding="utf-8")

    assert "url.isScheme('http')" in source


def test_scrapy_blocks_internal_requests():
    settings = (PROJECT_DIR / "ecommerce_price_comparer" / "settings.py").read_text(encoding="utf-8")
    assert '"ecommerce_price_comparer.middlewares.BlockInternalRequestsMiddleware"' in settings


def test_generated_spider_cannot_contain_injected_code(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "spider_builder", PROJECT_DIR / "autonomiczny_scraper" / "core" / "spider_builder.py",
    )
    spider_builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spider_builder)

    malicious_map = {
        "name": 'h1"; __import__("os").system("touch /tmp/hacked"); x = "',
        'price"]; import os; x = ["': "span\n__import__('os')",
    }
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(malicious_map), encoding="utf-8")
    output = tmp_path / "spider.py"

    spider_builder.generate_spider_file(str(map_path), str(output), 'sklep"; import os #')

    tree = ast.parse(output.read_text(encoding="utf-8"))
    spider_class = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assigned = {
        node.targets[0].id: node.value
        for node in spider_class.body
        if isinstance(node, ast.Assign)
    }

    assert ast.literal_eval(assigned["css_map"]) == malicious_map
    assert re.fullmatch(r"[a-z0-9_]+_spider", ast.literal_eval(assigned["name"]))
    assert all(isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef)) for node in tree.body)
