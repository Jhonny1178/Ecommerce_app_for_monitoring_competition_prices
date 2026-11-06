import json
import re
import socket
import subprocess

import psycopg2
import psycopg2.errors
import pytest
import requests

try:
    from tests.security.helpers import (
        AIRFLOW_DB_PORT, AIRFLOW_URL, DB_HOST, DB_PORT, ENV, PROJECT_DIR, compose, compose_services,
    )
except ImportError:
    from helpers import (
        AIRFLOW_DB_PORT, AIRFLOW_URL, DB_HOST, DB_PORT, ENV, PROJECT_DIR, compose, compose_services,
    )

pytestmark = pytest.mark.usefixtures("running_stack")

NON_ROOT_SERVICES = ["webapp", "ai_generator", "recommendation_service", "airflow-worker"]
READ_ONLY_SERVICES = ["webapp", "recommendation_service"]
APP_SERVICES = ["webapp", "ai_generator", "recommendation_service", "airflow-worker"]
AUDITED_SERVICES = ["webapp", "ai_generator", "recommendation_service"]
FORBIDDEN_SQL = [
    "CREATE ROLE sec_test_role",
    "CREATE DATABASE sec_test_database",
    "COPY users TO PROGRAM 'cat'",
    "COPY users FROM '/etc/passwd'",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_ls_dir('/')",
    "ALTER SYSTEM SET log_statement = 'none'",
]
DEFAULT_DB_LOGINS = [
    (DB_PORT, "postgres", "eroch"),
    (DB_PORT, "postgres", "postgres"),
    (DB_PORT, "postgres", "password"),
    (AIRFLOW_DB_PORT, "airflow", "airflow"),
]


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]


def airflow_login(username, password):
    session = requests.Session()
    page = session.get(f"{AIRFLOW_URL}/login/", timeout=20).text
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
    response = session.post(
        f"{AIRFLOW_URL}/login/",
        data={"username": username, "password": password, "csrf_token": token},
        timeout=20,
    )
    return "login" not in response.url


def test_internal_ports_are_closed_from_lan():
    ip = lan_ip()
    if ip.startswith("127."):
        pytest.skip("Komputer nie ma adresu w sieci lokalnej")

    ports = {
        int(port["published"])
        for name, service in compose_services().items()
        if name != "frontend"
        for port in service.get("ports", [])
    }

    open_ports = []
    for port in sorted(ports):
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex((ip, port)) == 0:
                open_ports.append(port)

    assert not open_ports, f"Otwarte z sieci lokalnej: {open_ports}"


def test_redis_requires_password():
    result = compose("exec", "-T", "redis", "redis-cli", "ping")
    assert "NOAUTH" in result.stdout + result.stderr


@pytest.mark.parametrize("service", NON_ROOT_SERVICES)
def test_container_does_not_run_as_root(service):
    result = compose("exec", "-T", service, "id", "-u")
    assert result.returncode == 0 and result.stdout.strip() != "0"


@pytest.mark.parametrize("service", READ_ONLY_SERVICES)
def test_application_code_cannot_be_changed_from_container(service):
    result = compose("exec", "-T", service, "touch", "/app/.security_test")
    assert result.returncode != 0


@pytest.mark.parametrize("service", APP_SERVICES)
def test_container_has_no_superuser_password(service):
    result = compose("exec", "-T", service, "sh", "-c", 'test -z "$DB_SUPERUSER_PASSWORD"')
    assert result.returncode == 0


@pytest.mark.parametrize("service", APP_SERVICES)
def test_container_connects_with_limited_role(service):
    code = """
import os
import psycopg2
conn = psycopg2.connect(
    host=os.environ["APP_DB_HOST"], port=os.environ["APP_DB_PORT"], dbname=os.environ["APP_DB_NAME"],
    user=os.environ["APP_DB_USER"], password=os.environ["APP_DB_PASSWORD"],
)
cur = conn.cursor()
cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
print(cur.fetchone()[0])
"""
    result = compose("exec", "-T", service, "python", "-", input_text=code)
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_application_role_has_no_admin_attributes(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
            FROM pg_roles WHERE rolname = current_user
        """)
        assert not any(cur.fetchone())


def test_application_role_owns_its_tables(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tableowner <> current_user
        """)
        assert cur.fetchall() == []


@pytest.mark.parametrize("statement", FORBIDDEN_SQL)
def test_application_role_cannot_run_admin_operations(db_conn, statement):
    with db_conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(statement)


@pytest.mark.parametrize("port,user,password", DEFAULT_DB_LOGINS)
def test_default_database_passwords_do_not_work(port, user, password):
    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(host=DB_HOST, port=port, dbname="postgres", user=user, password=password)


def test_airflow_panel_rejects_default_passwords():
    username = ENV["_AIRFLOW_WWW_USER_USERNAME"]
    assert not airflow_login(username, "airflow")
    assert not airflow_login(username, "admin")
    assert not airflow_login("admin", "admin")


def test_airflow_panel_accepts_password_from_env():
    assert airflow_login(ENV["_AIRFLOW_WWW_USER_USERNAME"], ENV["_AIRFLOW_WWW_USER_PASSWORD"])


def test_airflow_does_not_expose_configuration():
    result = compose(
        "exec", "-T", "airflow-webserver", "sh", "-c", "airflow config get-value webserver expose_config 2>/dev/null",
    )
    assert result.stdout.strip().splitlines()[-1] == "False"


@pytest.mark.slow
def test_application_dependencies_have_no_known_vulnerabilities():
    script = "pip install -q pip-audit 2>/dev/null\n"
    for service in AUDITED_SERVICES:
        frozen = compose("exec", "-T", service, "pip", "freeze").stdout
        requirements = "\n".join(line for line in frozen.splitlines() if "==" in line)
        script += (
            f"cat > /tmp/{service}.txt <<'REQUIREMENTS'\n{requirements}\nREQUIREMENTS\n"
            f"echo '###{service}'\n"
            f"pip-audit --no-deps --disable-pip -r /tmp/{service}.txt --format json 2>/dev/null\n"
        )

    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "python:3.11-slim", "sh"],
        input=script, capture_output=True, text=True, timeout=400, cwd=PROJECT_DIR,
    )

    sections = re.findall(r"###([a-z_]+)\n(\{.*\})", result.stdout)
    if len(sections) != len(AUDITED_SERVICES):
        pytest.skip("Audyt zależności niedostępny (brak internetu lub obrazu python:3.11-slim)")

    vulnerable = [
        f"{service}: {package['name']} {package['version']} ({vuln['id']})"
        for service, report in sections
        for package in json.loads(report)["dependencies"]
        for vuln in package.get("vulns", [])
    ]
    assert not vulnerable, f"Podatne zależności: {vulnerable}"
