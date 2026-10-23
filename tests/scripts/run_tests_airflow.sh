#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Uruchamianie testów integralności i odporności potoku Airflow ==="
docker compose exec airflow-scheduler python -m pytest /opt/airflow/tests/airflow_tests -v "$@"
