#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
BIN="${RETAIL_PG_BIN:-$(pg_config --bindir 2>/dev/null || echo /nix/store/lhil0kwh7czaa9wx0h5q7gi7xynn8kqy-postgresql-16.15/bin)}"
ROOT="$(mktemp -d /tmp/tivoli-real-navigation.XXXXXX)"
PORT=55162
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi
  "$BIN/pg_ctl" -D "$ROOT/data" -m fast stop >/tmp/tivoli-real-navigation-stop.log 2>&1 || true
  rm -rf "$ROOT"
}
trap cleanup EXIT
"$BIN/initdb" -D "$ROOT/data" -U "$(id -un)" --auth-local=trust --auth-host=trust --encoding=UTF8 --no-locale > /tmp/tivoli-real-navigation-init.log
"$BIN/pg_ctl" -D "$ROOT/data" -l "$ROOT/postgres.log" -o "-h 127.0.0.1 -p $PORT -c unix_socket_directories=$ROOT" start
export PGHOST=127.0.0.1 PGPORT="$PORT" PGUSER="$(id -un)" PGDATABASE=tivoli_erp_demo
export APP_ENV=demo DB_HOST=127.0.0.1 DB_PORT="$PORT" DB_USER="$PGUSER" DB_NAME="$PGDATABASE" DB_PASSWORD=''
unset DATABASE_URL PGPASSWORD PGSERVICE DJANGO_SECRET_KEY SESSION_SECRET
export DJANGO_COOKIE_SECURE=false DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS=127.0.0.1
export EXPECTED_DISPOSABLE_DATA="$ROOT/data" ACCEPTANCE_ROOT="$ROOT"
export REAL_BACKEND_ORIGIN=http://127.0.0.1:18061
"$BIN/createdb" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE"
"$BIN/psql" -X -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Atc \
 "select current_setting('server_version'),current_setting('data_directory'),inet_server_port(),current_database()"
# Current fixture ORM includes quotation metadata. Prepare it at the current
# schema, then rehearse legacy Favorites from 0018 using the same records.
python manage.py migrate --noinput > /tmp/tivoli-real-navigation-migrations.log 2>&1
ACCEPTANCE_PHASE=prepare python manage.py shell < scripts/real_navigation_fixture.py
python manage.py migrate foundation 0018 --noinput >> /tmp/tivoli-real-navigation-migrations.log 2>&1
python manage.py migrate --noinput >> /tmp/tivoli-real-navigation-migrations.log 2>&1
ACCEPTANCE_PHASE=verify python manage.py shell < scripts/real_navigation_fixture.py
"$BIN/createdb" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" test_tivoli_erp_demo_fresh
DB_NAME=test_tivoli_erp_demo_fresh python manage.py migrate --noinput > /tmp/tivoli-real-navigation-fresh.log 2>&1
DB_NAME=test_tivoli_erp_demo_fresh python manage.py migrate --check >> /tmp/tivoli-real-navigation-fresh.log 2>&1
python manage.py runserver 127.0.0.1:18061 --noreload > /tmp/tivoli-real-navigation-server.log 2>&1 &
SERVER_PID=$!
for i in {1..80}; do
  if curl --silent --fail http://127.0.0.1:18061/api/auth/csrf/ >/dev/null; then break; fi
  sleep .2
done
node --test --test-concurrency=1 --test-name-pattern="${NAVIGATION_BROWSER_PATTERN:-.}" scripts/real-navigation-browser.test.mjs
# The SQL journey outcome is meaningful only when the delivery scenario ran.
if [[ -z "${NAVIGATION_BROWSER_PATTERN:-}" || "${VERIFY_NAVIGATION_JOURNEY_SQL:-0}" == "1" ]]; then
  python manage.py shell < scripts/verify_real_navigation_outcome.py
fi
python manage.py check --database default
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
if [[ "${RUN_FULL_REGRESSION:-0}" == "1" ]]; then
  python manage.py test foundation.test_function_favorites foundation.test_favorites foundation.test_purchasing_workflows foundation.test_delivery_operations --verbosity 1 > /tmp/tivoli-real-navigation-pg-focused.log 2>&1
  python manage.py test foundation --verbosity 1 > /tmp/tivoli-real-navigation-pg-full.log 2>&1
fi
