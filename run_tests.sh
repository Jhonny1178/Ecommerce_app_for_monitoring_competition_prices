#!/usr/bin/env bash
set -e

echo "=== Uruchamianie testów integralności i odporności potoku Airflow ==="
docker compose exec airflow-scheduler python -m pytest /opt/airflow/tests -v "$@"
