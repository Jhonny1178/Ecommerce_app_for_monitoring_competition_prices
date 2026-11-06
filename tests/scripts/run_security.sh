#!/usr/bin/env bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Testy bezpieczeństwa — E-Commerce Price Analysis ==="
echo ""

if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "BŁĄD: Nie znaleziono interpretera Python (ani python3, ani python)!"
    exit 1
fi

if [ ! -f .env ]; then
    echo "BŁĄD: Brak pliku .env (wzór: .env.example)."
    exit 1
fi

if $PYTHON_CMD -c "import pytest, requests, psycopg2" 2>/dev/null; then
    echo "Zależności już zainstalowane."
else
    $PYTHON_CMD -m pip install --timeout 30 pytest requests psycopg2-binary || true
fi

$PYTHON_CMD -m pytest tests/security/ --tb=short "$@"
