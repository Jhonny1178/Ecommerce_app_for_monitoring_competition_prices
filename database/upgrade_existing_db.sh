#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

info() { echo "==> $*"; }
fail() { echo "BŁĄD: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "Nie znaleziono polecenia docker."
[ -f .env ] || fail "Brak pliku .env."
docker compose config -q || fail "Konfiguracja docker compose jest niepełna — uzupełnij .env."

wait_for_db() {
    local service=$1
    for _ in $(seq 1 60); do
        if docker compose exec -T "$service" pg_isready -h 127.0.0.1 -q >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    fail "Baza '$service' nie wystartowała w ciągu 2 minut. Sprawdź: docker compose logs $service"
}

info "Uruchamiam bazy danych z nową konfiguracją..."
docker compose up -d postgres ecommerce-db
wait_for_db ecommerce-db
wait_for_db postgres

info "Aktualizuję bazę aplikacji (ecommerce-db)..."
docker compose exec -T -e OLD_DB_SUPERUSER="${OLD_DB_SUPERUSER:-}" ecommerce-db bash -s <<'EOF'
set -euo pipefail
fail() { echo "BŁĄD: $*" >&2; exit 1; }

[ -n "${APP_DB_USER:-}" ]     || fail "APP_DB_USER jest puste w .env."
[ -n "${APP_DB_PASSWORD:-}" ] || fail "APP_DB_PASSWORD jest puste w .env."
[ -f /docker-entrypoint-initdb.d/02-create_app_role.sh ] \
    || fail "Kontener nie widzi create_app_role.sh — czy docker-compose.yaml jest aktualny?"

NEW_SU="$POSTGRES_USER"

is_superuser() {
    [ "$(psql -U "$1" -d postgres -tAc 'SELECT rolsuper FROM pg_roles WHERE rolname = current_user' 2>/dev/null)" = "t" ]
}

OLD_SU=""
for candidate in "$OLD_DB_SUPERUSER" "$NEW_SU" postgres; do
    if [ -n "$candidate" ] && is_superuser "$candidate"; then
        OLD_SU="$candidate"
        break
    fi
done

[ -n "$OLD_SU" ] || fail "Nie znalazłem superużytkownika bazy. Uruchom ponownie, podając starą wartość APP_DB_USER:
    OLD_DB_SUPERUSER=stara_nazwa ./database/upgrade_existing_db.sh"

if [ "$OLD_SU" = "$APP_DB_USER" ]; then
    fail "Stara baza ma superużytkownika o nazwie '$APP_DB_USER', tej samej co nowe APP_DB_USER.
Zmień APP_DB_USER w .env na inną nazwę (np. ecommerce_app) i uruchom skrypt ponownie."
fi

psql -U "$OLD_SU" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_DB'" | grep -q 1 \
    || fail "Baza '$POSTGRES_DB' nie istnieje. Czy APP_DB_NAME w .env jest takie samo jak wcześniej?"

echo "    superużytkownik w starej bazie: $OLD_SU"

psql -v ON_ERROR_STOP=1 -q -U "$OLD_SU" -d postgres \
     -v new_su="$NEW_SU" -v su_password="$POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I SUPERUSER LOGIN', :'new_su')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'new_su') \gexec
SELECT format('ALTER ROLE %I SUPERUSER LOGIN PASSWORD %L', :'new_su', :'su_password') \gexec
SQL
echo "    ustawiono hasło superużytkownika '$NEW_SU'"

if [ "$OLD_SU" != "$NEW_SU" ]; then
    psql -v ON_ERROR_STOP=1 -q -U "$NEW_SU" -d postgres -v old_su="$OLD_SU" <<'SQL'
SELECT format('ALTER ROLE %I NOLOGIN', :'old_su') \gexec
SQL
    echo "    zablokowano logowanie starego konta '$OLD_SU'"
fi

bash /docker-entrypoint-initdb.d/02-create_app_role.sh >/dev/null
echo "    rola aplikacji '$APP_DB_USER' gotowa"

LOCKED=$(psql -v ON_ERROR_STOP=1 -U "$NEW_SU" -d "$POSTGRES_DB" -tAc "
    WITH locked AS (
        UPDATE users SET password_hash = '!'
        WHERE password_hash IN (
            '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9',
            'b2f56063f86e328691c6b8b5d5e4c27a2b393929c795fa8dd9c142d3f53542d2'
        )
        RETURNING 1
    ) SELECT count(*) FROM locked")
echo "    zablokowano kont z domyślnym hasłem: $LOCKED"

NET_IP=$(hostname -i | awk '{print $1}')
PGPASSWORD="$APP_DB_PASSWORD" psql -h "$NET_IP" -U "$APP_DB_USER" -d "$POSTGRES_DB" \
    -tAc 'SELECT count(*) FROM users' >/dev/null \
    || fail "Rola '$APP_DB_USER' nie może się zalogować lub nie widzi tabel."
PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$NET_IP" -U "$NEW_SU" -d postgres -tAc 'SELECT 1' >/dev/null \
    || fail "Superużytkownik '$NEW_SU' nie może się zalogować nowym hasłem."
if PGPASSWORD=wrong-password psql -h "$NET_IP" -U "$APP_DB_USER" -d postgres -tAc 'SELECT 1' >/dev/null 2>&1; then
    fail "Baza przyjmuje błędne hasła — sprawdź pg_hba.conf."
fi
echo "    test logowania: OK"
EOF

info "Aktualizuję bazę Airflow (postgres)..."
docker compose exec -T postgres bash -s <<'EOF'
set -euo pipefail
fail() { echo "BŁĄD: $*" >&2; exit 1; }

psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$POSTGRES_PASSWORD" <<'SQL'
SELECT format('ALTER ROLE %I PASSWORD %L', current_user, :'pw') \gexec
SQL

NET_IP=$(hostname -i | awk '{print $1}')
PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$NET_IP" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc 'SELECT 1' >/dev/null \
    || fail "Użytkownik '$POSTGRES_USER' nie może się zalogować nowym hasłem."
if PGPASSWORD=airflow psql -h "$NET_IP" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc 'SELECT 1' >/dev/null 2>&1; then
    fail "Stare hasło 'airflow' nadal działa."
fi
echo "    ustawiono nowe hasło użytkownika '$POSTGRES_USER', test logowania: OK"
EOF

echo
info "Proces ukończono. Teraz uruchom cały projekt:"
echo "    docker compose up -d --build"
