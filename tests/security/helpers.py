import json
import os
import re
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]

WEBAPP_URL = os.getenv("SECURITY_WEBAPP_URL", "http://localhost:6767")
FRONTEND_URL = os.getenv("SECURITY_FRONTEND_URL", "http://localhost")
AIRFLOW_URL = os.getenv("SECURITY_AIRFLOW_URL", "http://localhost:8081")
DB_HOST = os.getenv("SECURITY_DB_HOST", "localhost")
DB_PORT = int(os.getenv("SECURITY_DB_PORT", 5434))
AIRFLOW_DB_PORT = int(os.getenv("SECURITY_AIRFLOW_DB_PORT", 5433))

TEST_PASSWORD = "TajneHaslo123456"
SESSION_COOKIE = "eroch_session"


def load_env_file(path):
    content = Path(path).read_text(encoding="utf-8")
    pairs = re.findall(r"^([A-Za-z_0-9]+)=(.*)$", content, re.M)
    return {key: value.strip().strip("\"'") for key, value in pairs}


ENV = load_env_file(PROJECT_DIR / ".env")
EXAMPLE_ENV = load_env_file(PROJECT_DIR / ".env.example")


def compose(*args, input_text=None, timeout=180):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=PROJECT_DIR,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def compose_services():
    result = compose("--profile", "*", "config", "--format", "json")
    assert result.returncode == 0, "docker compose config nie działa"
    return json.loads(result.stdout)["services"]


def run_python(service, code, env=None):
    args = ["exec", "-T"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]

    result = compose(*args, service, "python", "-", input_text=code)
    assert result.returncode == 0, f"Błąd w kontenerze {service}: {result.stderr[-800:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def reset_rate_limits():
    result = compose(
        "exec", "-T", "redis", "sh", "-c",
        'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning -n 1 FLUSHDB',
    )
    assert "OK" in result.stdout, "Nie udało się wyczyścić limitów żądań w Redisie"
