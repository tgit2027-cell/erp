#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
BIN="${RETAIL_PG_BIN:-$(pg_config --bindir 2>/dev/null || echo /nix/store/lhil0kwh7czaa9wx0h5q7gi7xynn8kqy-postgresql-16.15/bin)}"
ROOT="$(mktemp -d /tmp/tivoli-procurement-acceptance.XXXXXX)"
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi
  "$BIN/pg_ctl" -D "$ROOT/data" -m fast stop >/dev/null 2>&1 || true
  rm -rf "$ROOT"
}
trap cleanup EXIT
"$BIN/initdb" -D "$ROOT/data" -U "$(id -un)" --auth-local=trust --auth-host=trust --encoding=UTF8 --no-locale > /tmp/procurement-acceptance-init.log
"$BIN/pg_ctl" -D "$ROOT/data" -l "$ROOT/postgres.log" -o "-h 127.0.0.1 -p 55163 -c unix_socket_directories=$ROOT" start
export PGHOST=127.0.0.1 PGPORT=55163 PGUSER="$(id -un)" PGDATABASE=tivoli_erp_demo
export APP_ENV=demo DB_HOST="$PGHOST" DB_PORT="$PGPORT" DB_USER="$PGUSER" DB_NAME="$PGDATABASE" DB_PASSWORD=''
unset DATABASE_URL PGPASSWORD PGSERVICE PGOPTIONS DJANGO_SECRET_KEY SESSION_SECRET
export DJANGO_COOKIE_SECURE=false DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS=127.0.0.1
export EXPECTED_DISPOSABLE_DATA="$ROOT/data" ACCEPTANCE_ROOT="$ROOT"
export REAL_BACKEND_ORIGIN=http://127.0.0.1:18062
ACTUAL_DATA="$("$BIN/psql" -X -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -Atc "select current_setting('data_directory')")"
[[ "$ACTUAL_DATA" == "$EXPECTED_DISPOSABLE_DATA" ]] || { echo "Isolation check failed; refusing database setup."; exit 1; }
"$BIN/createdb" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE"
"$BIN/psql" -X -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Atc \
  "select current_setting('server_version'),current_setting('data_directory'),inet_server_port(),current_database()"
python manage.py migrate --noinput >/tmp/procurement-acceptance-migrations.log 2>&1
PROCUREMENT_ACCEPTANCE_PHASE=prepare python manage.py shell --no-imports < scripts/procurement_acceptance_fixture.py
python manage.py runserver 127.0.0.1:18062 --noreload >/tmp/procurement-acceptance-server.log 2>&1 &
SERVER_PID=$!
for i in {1..80}; do
  if curl --silent --fail "$REAL_BACKEND_ORIGIN/api/auth/csrf/" >/dev/null; then break; fi
  sleep .2
done
curl --silent --fail "$REAL_BACKEND_ORIGIN/api/auth/csrf/" >/dev/null
npm run build
node --test --test-concurrency=1 scripts/procurement-real-browser.test.mjs
python manage.py test foundation.test_procurement_workspace foundation.test_packing_receipt_candidates foundation.test_purchasing_workflows --verbosity 1
python manage.py check --database default
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
