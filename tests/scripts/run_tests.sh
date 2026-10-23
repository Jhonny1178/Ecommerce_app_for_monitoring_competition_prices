#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

MODE="${1:-all}"
shift || true

run_airflow() {
    echo "=========================================================="
    echo " [1] TESTY AIRFLOW: Integralność i Odporność DAG-a"
    echo "=========================================================="
    "$SCRIPT_DIR/run_tests_airflow.sh" "$@"
}

run_e2e() {
    echo ""
    echo "=========================================================="
    echo " [2] TESTY E2E: Przepływ End-to-End aplikacji"
    echo "=========================================================="
    "$SCRIPT_DIR/run_e2e.sh" "$@"
}

case "$MODE" in
    airflow)
        run_airflow "$@"
        ;;
    e2e)
        run_e2e "$@"
        ;;
    all)
        run_airflow "$@"
        run_e2e "$@"
        ;;
    *)
        echo "Użycie: $0 [airflow|e2e|all] [dodatkowe flagi pytest]"
        exit 1
        ;;
esac
