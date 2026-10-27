#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
BIN="${RETAIL_PG_BIN:-$(pg_config --bindir 2>/dev/null || echo /nix/store/lhil0kwh7czaa9wx0h5q7gi7xynn8kqy-postgresql-16.15/bin)}"
ROOT="$(mktemp -d /tmp/tivoli-retail-acceptance.XXXXXX)"
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi
  "$BIN/pg_ctl" -D "$ROOT/data" -m fast stop >/dev/null 2>&1 || true
  rm -rf "$ROOT"
}
trap cleanup EXIT
"$BIN/initdb" -D "$ROOT/data" -U "$(id -un)" --auth-local=trust --auth-host=trust --encoding=UTF8 --no-locale > "$ROOT/init.log"
"$BIN/pg_ctl" -D "$ROOT/data" -l "$ROOT/postgres.log" -o "-h 127.0.0.1 -p 55165 -c unix_socket_directories=$ROOT" start
export PGHOST=127.0.0.1 PGPORT=55165 PGUSER="$(id -un)" PGDATABASE=tivoli_erp_demo
export APP_ENV=demo DB_HOST="$PGHOST" DB_PORT="$PGPORT" DB_USER="$PGUSER" DB_NAME="$PGDATABASE" DB_PASSWORD=''
unset DATABASE_URL PGPASSWORD PGSERVICE PGOPTIONS DJANGO_SECRET_KEY SESSION_SECRET
export DJANGO_COOKIE_SECURE=false DJANGO_DEBUG=true DJANGO_ALLOWED_HOSTS=127.0.0.1
export EXPECTED_DISPOSABLE_DATA="$ROOT/data" ACCEPTANCE_ROOT="$ROOT"
export REAL_BACKEND_ORIGIN=http://127.0.0.1:18064
ACTUAL_DATA="$("$BIN/psql" -X -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -Atc "select current_setting('data_directory')")"
[[ "$ACTUAL_DATA" == "$EXPECTED_DISPOSABLE_DATA" ]] || { echo "Isolation check failed; refusing setup."; exit 1; }
"$BIN/createdb" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE"
"$BIN/psql" -X -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Atc \
  "select current_setting('server_version'),current_setting('data_directory'),inet_server_port(),current_database()"
python manage.py shell --no-imports -c "from scripts.retail_acceptance_fixture import assert_isolated; assert_isolated(); print('Django connection isolation verified before migrations.')"
python manage.py migrate --noinput > "$ROOT/migrations.log" 2>&1
RETAIL_ACCEPTANCE_PHASE=prepare python manage.py shell --no-imports < scripts/retail_acceptance_fixture.py
python manage.py runserver 127.0.0.1:18064 --noreload > "$ROOT/server.log" 2>&1 &
SERVER_PID=$!
for i in {1..80}; do
  if curl --silent --fail "$REAL_BACKEND_ORIGIN/api/auth/csrf/" >/dev/null; then break; fi
  sleep .2
done
curl --silent --fail "$REAL_BACKEND_ORIGIN/api/auth/csrf/" >/dev/null
python - "$SERVER_PID" <<'PY'
import os
import sys
from pathlib import Path
pid = int(sys.argv[1])
os.kill(pid, 0)
inodes = set()
for fd in Path(f"/proc/{pid}/fd").iterdir():
    try:
        target = os.readlink(fd)
    except FileNotFoundError:
        continue
    if target.startswith("socket:["):
        inodes.add(target[8:-1])
rows = [line.split() for line in Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]]
assert any(row[1] == f"0100007F:{18064:04X}" and row[3] == "0A" and row[9] in inodes for row in rows), \
    "Refusing browser startup: HTTP port is not owned by this isolated Django process"
print("Isolated Django HTTP listener ownership verified.")
PY
if [[ -z "${RETAIL_BACKEND_TEST:-}" ]]; then npm run build; fi
if [[ -n "${RETAIL_BACKEND_TEST:-}" ]]; then
  [[ "$RETAIL_BACKEND_TEST" == foundation || "$RETAIL_BACKEND_TEST" == foundation.* ]] || { echo "Use a foundation test label."; exit 2; }
  python manage.py test "$RETAIL_BACKEND_TEST" --verbosity 2
  python manage.py check --database default
  python manage.py migrate --check
  python manage.py makemigrations --check --dry-run
elif [[ "${RETAIL_TEAM_REVIEW:-0}" == 1 ]]; then
  node scripts/retail-team-review.mjs
else
  if [[ "${RETAIL_SKIP_BROWSER:-0}" != "1" ]]; then
    BROWSER_FILE="${RETAIL_BROWSER_FILE:-scripts/retail-real-browser.test.mjs}"
    [[ "$BROWSER_FILE" == scripts/retail-real-browser.test.mjs || "$BROWSER_FILE" == scripts/capacity-real-browser.test.mjs || "$BROWSER_FILE" == scripts/inventory-register-real-browser.test.mjs ]] || { echo "Use an approved isolated browser suite."; exit 2; }
    node --test --test-concurrency=1 --test-name-pattern="${RETAIL_BROWSER_PATTERN:-.}" "$BROWSER_FILE"
  fi
  if [[ "${RETAIL_BROWSER_ONLY:-0}" == "1" ]]; then exit 0; fi
  python manage.py test foundation.test_delivery_preparation --verbosity 2
  python manage.py test foundation.test_retail_operations foundation.test_purchasing_workflows --verbosity 1
  python manage.py test foundation --verbosity 1
  python manage.py check --database default
  python manage.py migrate --check
  python manage.py makemigrations --check --dry-run
fi
