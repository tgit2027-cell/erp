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
  echo "Backups from this runner are restricted to APP_ENV=demo." >&2
  exit 1
fi
export DB_NAME="${DB_NAME:-tivoli_erp_demo}"
if [[ "$DB_NAME" != "tivoli_erp_demo" ]]; then
  echo "This command only backs up the local tivoli_erp_demo database." >&2
  exit 1
fi
export DB_HOST="${DB_HOST:-127.0.0.1}"
if [[ "$DB_HOST" != "127.0.0.1" && "$DB_HOST" != "localhost" && "$DB_HOST" != "::1" ]]; then
  echo "Refusing to back up a non-local database." >&2
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
export DB_USER="${DB_USER:-${PGUSER:-$(id -un)}}"
export DB_PASSWORD="${DB_PASSWORD:-}"
if [[ -n "${DB_PASSWORD:-}" ]]; then
  export PGPASSWORD="$DB_PASSWORD"
fi
export PGHOST="$DB_HOST"
export PGPORT="$DB_PORT"
export PGUSER="$DB_USER"

SERVER_MAJOR="$(psql -d "$DB_NAME" -Atc 'SHOW server_version_num' | awk '{print int($1/10000)}')"
DUMP_VERSION="$(pg_dump --version | awk '{print $3}')"
DUMP_MAJOR="${DUMP_VERSION%%.*}"
if [[ "$SERVER_MAJOR" != "$DUMP_MAJOR" ]]; then
  echo "pg_dump major $DUMP_MAJOR does not match PostgreSQL server major $SERVER_MAJOR." >&2
  exit 1
fi

BACKUP_ROOT="${1:-$HOME/tivoli-erp-backups}"
umask 077
mkdir -p "$BACKUP_ROOT"
BACKUP_ROOT="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$BACKUP_ROOT")"
case "$BACKUP_ROOT/" in
  "$ROOT/"*)
    echo "Choose a backup location outside the project directory." >&2
    exit 1
    ;;
esac

BACKUP_DIR="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir "$BACKUP_DIR"
trap 'status=$?; if [[ $status -ne 0 ]]; then rm -rf "$BACKUP_DIR"; fi; exit $status' EXIT

pg_dump --format=custom --no-owner --no-acl --dbname="$DB_NAME" --file="$BACKUP_DIR/database.dump"
pg_restore --list "$BACKUP_DIR/database.dump" >/dev/null
python manage.py snapshot_integrity --output "$BACKUP_DIR/database-manifest.json"
MEDIA_ROOT="${DJANGO_MEDIA_ROOT:-$ROOT/media}"
python3 scripts/backup_artifacts.py media-write --root "$MEDIA_ROOT" --bundle "$BACKUP_DIR"
python3 scripts/backup_artifacts.py checksums-write --bundle "$BACKUP_DIR"
chmod 600 "$BACKUP_DIR"/*
chmod 700 "$BACKUP_DIR"
trap - EXIT

echo "Local synthetic database backup created: $BACKUP_DIR"
echo "The bundle is not encrypted and includes Django account password hashes; keep it private and encrypt it with an operator-approved method before off-site storage."
