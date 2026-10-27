#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

ITEMS=8000
SESSIONS=40
WORKERS=40
REQUESTS_PER_SESSION=1
WARMUPS=1
SEED=20261017
OUTPUT="$ROOT/generated-artifacts/synthetic-load-baseline.json"
KEEP_DB=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --items) ITEMS="$2"; shift 2 ;;
    --sessions) SESSIONS="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --requests-per-session) REQUESTS_PER_SESSION="$2"; shift 2 ;;
    --warmups) WARMUPS="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --keep-db) KEEP_DB=1; shift ;;
    -h|--help)
      echo "Usage: scripts/run_synthetic_load.sh [--items 8000] [--sessions 40 (max 100)] [--workers 40 (max 100)] [--requests-per-session 1] [--warmups 1] [--seed 20261017] [--output PATH] [--keep-db]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

export APP_ENV="${APP_ENV:-demo}"
if [[ "$APP_ENV" != "demo" ]]; then
  echo "The synthetic load runner requires APP_ENV=demo." >&2
  exit 1
fi
export DB_HOST="${DB_HOST:-127.0.0.1}"
if [[ "$DB_HOST" != "127.0.0.1" && "$DB_HOST" != "localhost" && "$DB_HOST" != "::1" ]]; then
  echo "The synthetic load runner refuses non-local PostgreSQL." >&2
  exit 1
fi
USE_BUNDLED_POSTGRES="${USE_BUNDLED_POSTGRES:-1}"
if [[ "$USE_BUNDLED_POSTGRES" == "1" ]]; then
  export PGDATA="${PGDATA:-$ROOT/.runtime/postgres}"
  export LOCAL_PGPORT="${LOCAL_PGPORT:-${DB_PORT:-55432}}"
  # shellcheck source=scripts/local_postgres.sh
  source "$ROOT/scripts/local_postgres.sh"
  ensure_local_postgres
  export DB_PORT="$LOCAL_PGPORT"
else
  export DB_PORT="${DB_PORT:-}"
  if [[ -z "$DB_PORT" || -z "${DB_USER:-}" ]]; then
    echo "Set DB_USER and DB_PORT for an operator-managed local PostgreSQL server." >&2
    exit 1
  fi
fi
export DB_HOST=127.0.0.1
export DB_USER="${DB_USER:-${PGUSER:-$(id -un)}}"
export DB_PASSWORD="${DB_PASSWORD:-}"
if [[ -n "${DB_PASSWORD:-}" ]]; then
  export PGPASSWORD="$DB_PASSWORD"
fi
export PGHOST="$DB_HOST"
export PGPORT="$DB_PORT"
export PGUSER="$DB_USER"

DB_NAME="tivoli_erp_loadtest_$(date -u +%Y%m%d%H%M%S)_$$"
export DB_NAME
createdb "$DB_NAME"
KEEP_DATABASE="$KEEP_DB"
cleanup_load_db() {
  local status=$?
  if [[ "$KEEP_DATABASE" != "1" ]]; then
    dropdb --if-exists "$DB_NAME" >/dev/null 2>&1 || true
  else
    echo "Synthetic load database retained for inspection: $DB_NAME"
  fi
  exit "$status"
}
trap cleanup_load_db EXIT

python manage.py migrate --noinput
python manage.py seed_demo
python manage.py synthetic_load_baseline \
  --items "$ITEMS" \
  --sessions "$SESSIONS" \
  --workers "$WORKERS" \
  --requests-per-session "$REQUESTS_PER_SESSION" \
  --warmups "$WARMUPS" \
  --seed "$SEED" \
  --output "$OUTPUT"
