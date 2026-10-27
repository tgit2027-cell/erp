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

export APP_ENV="${APP_ENV:-demo}"
if [[ "$APP_ENV" != "demo" ]]; then
  echo "The bundled development runner only starts in APP_ENV=demo." >&2
  exit 1
fi
# Debug pages stay off unless a developer explicitly sets DJANGO_DEBUG=true.
export DJANGO_DEBUG="${DJANGO_DEBUG:-false}"
if [[ -n "${REPLIT_DEV_DOMAIN:-}" ]]; then
  if [[ ! "$REPLIT_DEV_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "REPLIT_DEV_DOMAIN must contain only a hostname, without scheme or port." >&2
    exit 1
  fi
  export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1,$REPLIT_DEV_DOMAIN}"
  export VITE_ALLOWED_HOSTS="${VITE_ALLOWED_HOSTS:-$REPLIT_DEV_DOMAIN}"
  export DEV_BIND_HOST="${DEV_BIND_HOST:-0.0.0.0}"
  export DJANGO_CSRF_TRUSTED_ORIGINS="https://${REPLIT_DEV_DOMAIN}:5000"
  echo "Django CSRF trusted development origin: $DJANGO_CSRF_TRUSTED_ORIGINS"
  export DJANGO_COOKIE_SECURE="${DJANGO_COOKIE_SECURE:-true}"
else
  export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1}"
  export DJANGO_CSRF_TRUSTED_ORIGINS="${DJANGO_CSRF_TRUSTED_ORIGINS:-http://localhost:5000,http://127.0.0.1:5000}"
  # Plain-HTTP localhost development needs non-Secure cookies.
  export DJANGO_COOKIE_SECURE="${DJANGO_COOKIE_SECURE:-false}"
fi
if [[ "$DJANGO_ALLOWED_HOSTS" == *"*"* ]]; then
  echo "DJANGO_ALLOWED_HOSTS must list exact hostnames; wildcards are refused." >&2
  exit 1
fi

PGDATA="${PGDATA:-$ROOT/.runtime/postgres}"
DB_NAME="${DB_NAME:-tivoli_erp_demo}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-55432}"
USE_BUNDLED_POSTGRES="${USE_BUNDLED_POSTGRES:-1}"
if [[ "$DB_HOST" != "127.0.0.1" && "$DB_HOST" != "localhost" && "$DB_HOST" != "::1" ]]; then
  echo "The synthetic development runner refuses remote PostgreSQL hosts; use a local development database." >&2
  exit 1
fi
if [[ "$USE_BUNDLED_POSTGRES" == "1" ]]; then
  export PGDATA="${PGDATA:-$ROOT/.runtime/postgres}"
  if [[ ! -s "$PGDATA/PG_VERSION" ]]; then
    echo "Existing demo PostgreSQL data is required. Startup will not initialize, reset or seed a database; use the documented explicit setup separately." >&2
    exit 1
  fi
  export LOCAL_PGPORT="${LOCAL_PGPORT:-$DB_PORT}"
  # shellcheck source=scripts/local_postgres.sh
  source "$ROOT/scripts/local_postgres.sh"
  ensure_local_postgres
  export DB_NAME
  export DB_USER="${DB_USER:-${PGUSER:-$(id -un)}}"
  export DB_PASSWORD="${DB_PASSWORD:-}"
  export DB_HOST
  export DB_PORT="$LOCAL_PGPORT"
  if [[ -n "${DB_PASSWORD:-}" ]]; then
    export PGPASSWORD="$DB_PASSWORD"
  fi
  if ! psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -tAc 'SELECT datname FROM pg_database' | grep -Fqx "$DB_NAME"; then
    echo "The configured demo database does not exist. Refusing to create or seed it during startup." >&2
    exit 1
  fi
else
  if [[ -z "${DB_USER:-}" || -z "${DB_PORT:-}" ]]; then
    echo "For operator-managed local PostgreSQL, set DB_NAME, DB_USER, and DB_PORT." >&2
    exit 1
  fi
  export DB_NAME DB_USER DB_HOST DB_PORT
  export DB_PASSWORD="${DB_PASSWORD:-}"
  if [[ -n "${DB_PASSWORD:-}" ]]; then
    export PGPASSWORD="$DB_PASSWORD"
  fi
  if ! psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc 'SELECT 1' >/dev/null; then
    echo "Cannot connect to the configured PostgreSQL database; create it and verify operator-supplied connection settings." >&2
    exit 1
  fi
fi

# Keep one signing key across restarts so sessions survive them. The key is
# generated once into the git-ignored runtime folder unless one is supplied.
if [[ -z "${DJANGO_SECRET_KEY:-}" && -z "${SESSION_SECRET:-}" ]]; then
  SECRET_FILE="$ROOT/.runtime/django-secret-key"
  if [[ ! -s "$SECRET_FILE" ]]; then
    mkdir -p "$ROOT/.runtime"
    (umask 077 && head -c 48 /dev/urandom | base64 | tr -d '\n' > "$SECRET_FILE")
  fi
  DJANGO_SECRET_KEY="$(cat "$SECRET_FILE")"
  export DJANGO_SECRET_KEY
fi

# Startup never migrates or seeds. A pending schema change requires a separate
# reviewed backup/migration operation, not an incidental workspace wakeup.
if ! PGOPTIONS="-c default_transaction_read_only=on" python manage.py migrate --check; then
  echo "Development startup stopped at read-only schema readiness. No migration or seed was performed." >&2
  echo "Read-only migration status follows; review pending changes separately before any shared upgrade." >&2
  PGOPTIONS="-c default_transaction_read_only=on" python manage.py showmigrations foundation --plan >&2 || true
  exit 1
fi
PGOPTIONS="-c default_transaction_read_only=on" python manage.py shell < scripts/demo_startup_check.py
exec python scripts/dev_services.py
