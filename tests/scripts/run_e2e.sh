#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Testy End-to-End — E-Commerce Price Analysis ==="
echo ""

if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "BŁĄD: Nie znaleziono interpretera Python (ani python3, ani python)!"
    exit 1
fi

if [ -f .env ]; then
    export APP_DB_USER="${APP_DB_USER:-$(grep '^APP_DB_USER=' .env | cut -d= -f2-)}"
    export APP_DB_PASSWORD="${APP_DB_PASSWORD:-$(grep '^APP_DB_PASSWORD=' .env | cut -d= -f2-)}"
    export E2E_ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-$(grep "^ADMIN_PASSWORD=" .env | cut -d= -f2-)}"
fi
if [ -z "$E2E_ADMIN_PASSWORD" ]; then
    echo "UWAGA: brak E2E_ADMIN_PASSWORD (w .env lub w zmiennej) — testy logowania admina nie przejdą."
fi

echo "[1/3] Sprawdzam kontenery..."
docker compose ps --format "table {{.Name}}\t{{.Status}}" | head -20

echo ""
echo "[2/3] Instaluję/sprawdzam zależności testowe..."
if $PYTHON_CMD -c "import pytest, requests, psycopg2" 2>/dev/null; then
    echo "Zależności już zainstalowane."
else
    $PYTHON_CMD -m pip install --timeout 30 pytest requests psycopg2-binary || true
fi

echo ""
echo "[3/3] Uruchamiam testy E2E ($PYTHON_CMD)..."
$PYTHON_CMD -m pytest tests/e2e/ -v --tb=short "$@"
