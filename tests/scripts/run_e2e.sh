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

echo "[1/3] Sprawdzam kontenery..."
docker compose ps --format "table {{.Name}}\t{{.Status}}" | head -20

echo ""
echo "[2/3] Instaluję/sprawdzam zależności testowe..."
$PYTHON_CMD -m pip install -q pytest requests psycopg2-binary 2>/dev/null || true

echo ""
echo "[3/3] Uruchamiam testy E2E ($PYTHON_CMD)..."
$PYTHON_CMD -m pytest tests/e2e/ -v --tb=short "$@"
