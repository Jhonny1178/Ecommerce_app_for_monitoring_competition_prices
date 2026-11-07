#!/usr/bin/env bash
# Raport przebiegów (tylko odczyt): tests/scripts/raport_pipeline.sh [prefix_klienta] [liczba_dni]
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_DIR"

PREFIX="${1:-nasz_klient}"
DAYS="${2:-3}"

if [[ ! "$PREFIX" =~ ^[a-z0-9_]+$ ]]; then
    echo "BŁĄD: prefix klienta może zawierać tylko małe litery, cyfry i _" >&2
    exit 1
fi
if [[ ! "$DAYS" =~ ^[0-9]+$ ]]; then
    echo "BŁĄD: liczba dni musi być liczbą" >&2
    exit 1
fi

echo "=== Raport pipeline'u: klient=$PREFIX, ostatnie $DAYS dni ==="
echo ""
echo "--- Airflow ---"
docker compose exec -T airflow-scheduler airflow dags list 2>/dev/null \
    | grep -E "^dag_id|multi_client_pipeline" | awk -F'|' '{print $1 "|" $4}'
docker compose exec -T airflow-scheduler airflow dags list-runs -d multi_client_pipeline -o plain 2>/dev/null \
    | awk 'NR==1 || /scheduled__/' | awk '{print $2, $3, $5}' | head -8 | column -t

docker compose exec -T ecommerce-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -v ON_ERROR_STOP=1' <<SQL
\pset pager off
\pset footer off
\echo ''
\echo '--- Przebiegi (pipeline_runs) ---'
SELECT pr.id AS run, pr.status, pr.started_at::timestamp(0) AS start,
       (pr.finished_at - pr.started_at)::interval(0) AS czas,
       left(coalesce(pr.error_msg, ''), 90) AS blad
FROM pipeline_runs pr JOIN clients c ON c.id = pr.client_id
WHERE c.store_prefix = '$PREFIX' AND pr.started_at > NOW() - INTERVAL '$DAYS days'
ORDER BY pr.id DESC;

\echo ''
\echo '--- Scrapery w kolejnych przebiegach ---'
SELECT t.pipeline_run_id AS run, replace(t.task_id, 'scraper_', '') AS scraper, t.status,
       (t.finished_at - t.started_at)::interval(0) AS czas,
       substring(t.log_excerpt from 'scraped_items=(\d+)') AS produkty,
       left(regexp_replace(coalesce(t.error_msg, ''), '\s+', ' ', 'g'), 110) AS blad
FROM pipeline_task_runs t JOIN clients c ON c.id = t.client_id
WHERE c.store_prefix = '$PREFIX' AND t.task_id LIKE 'scraper_%'
  AND t.started_at > NOW() - INTERVAL '$DAYS days'
ORDER BY t.pipeline_run_id DESC, t.task_id;

\echo ''
\echo '--- Błędy i ostrzeżenia (error_logs) ---'
SELECT e.created_at::timestamp(0) AS kiedy, e.category AS kategoria, e.error_type AS typ,
       e.error_code AS kod, left(regexp_replace(e.message, '\s+', ' ', 'g'), 110) AS opis
FROM error_logs e JOIN clients c ON c.id = e.client_id
WHERE c.store_prefix = '$PREFIX' AND e.created_at > NOW() - INTERVAL '$DAYS days'
ORDER BY e.id DESC
LIMIT 30;

\echo ''
\echo '--- Dane w tabelach klienta (stan obecny) ---'
SELECT 'produkty klienta' AS tabela, count(*) AS wiersze FROM ${PREFIX}_products
UNION ALL SELECT 'konkurencja', count(*) FROM ${PREFIX}_competitors
UNION ALL SELECT 'dopasowania', count(*) FROM ${PREFIX}_matches;

\echo ''
\echo '--- Konkurencja według sklepu ---'
SELECT store AS sklep, count(*) AS produkty, count(price_normal) AS z_cena,
       max(date_of_download)::timestamp(0) AS ostatnie_pobranie
FROM ${PREFIX}_competitors GROUP BY store ORDER BY store;
SQL
