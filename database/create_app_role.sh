#!/bin/bash
set -euo pipefail

if [ "$APP_DB_USER" = "$POSTGRES_USER" ]; then
    echo "APP_DB_USER nie może być tym samym kontem co superużytkownik ($POSTGRES_USER)." >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user') \gexec

SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
              :'app_user', :'app_password') \gexec

SELECT format('ALTER SCHEMA public OWNER TO %I', :'app_user') \gexec

SELECT format('ALTER TABLE %s OWNER TO %I', c.oid::regclass, :'app_user')
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e') \gexec

SELECT format('ALTER SEQUENCE %s OWNER TO %I', c.oid::regclass, :'app_user')
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'S'
  AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i', 'e')) \gexec

SELECT format('ALTER ROUTINE %s OWNER TO %I', p.oid::regprocedure, :'app_user')
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') \gexec
SQL
