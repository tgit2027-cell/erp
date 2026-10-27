#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: scripts/restore_local_demo.sh BACKUP_DIRECTORY tivoli_erp_restore_NAME [MEDIA_DESTINATION]" >&2
  exit 2
fi

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
  echo "Restore is restricted to APP_ENV=demo." >&2
  exit 1
fi

BUNDLE="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$1")"
TARGET_DB="$2"
if [[ "$TARGET_DB" != tivoli_erp_restore_* || "$TARGET_DB" == *[!A-Za-z0-9_]* || ${#TARGET_DB} -gt 63 ]]; then
  echo "Target database must use the safe tivoli_erp_restore_ prefix and contain only letters, digits, and underscores." >&2
  exit 1
fi

export DB_HOST="${DB_HOST:-127.0.0.1}"
if [[ "$DB_HOST" != "127.0.0.1" && "$DB_HOST" != "localhost" && "$DB_HOST" != "::1" ]]; then
  echo "Refusing to restore to a non-local database." >&2
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

python3 scripts/backup_artifacts.py checksums-verify --bundle "$BUNDLE"
pg_restore --list "$BUNDLE/database.dump" >/dev/null
SERVER_MAJOR="$(psql -d postgres -Atc 'SHOW server_version_num' | awk '{print int($1/10000)}')"
DUMP_VERSION="$(pg_restore --version | awk '{print $3}')"
DUMP_MAJOR="${DUMP_VERSION%%.*}"
if [[ "$SERVER_MAJOR" != "$DUMP_MAJOR" ]]; then
  echo "pg_restore major $DUMP_MAJOR does not match PostgreSQL server major $SERVER_MAJOR." >&2
  exit 1
fi
if psql -d postgres -Atc 'SELECT datname FROM pg_database' | grep -Fqx "$TARGET_DB"; then
  echo "Target database already exists; refusing to overwrite it." >&2
  exit 1
fi

MEDIA_MANIFEST="$BUNDLE/media-manifest.json"
MEDIA_INCLUDED="$(python3 -c 'import json,sys; print("yes" if json.load(open(sys.argv[1])).get("included") else "no")' "$MEDIA_MANIFEST")"
MEDIA_DEST=""
MEDIA_CREATED=0
if [[ "$MEDIA_INCLUDED" == "yes" ]]; then
  MEDIA_DEST="${3:-$HOME/tivoli-erp-restored-media/$TARGET_DB}"
  MEDIA_DEST="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$MEDIA_DEST")"
  case "$MEDIA_DEST/" in
    "$ROOT/"*)
      echo "Restore media destination must be outside the project directory." >&2
      exit 1
      ;;
  esac
  if [[ -e "$MEDIA_DEST" ]]; then
    echo "Media destination already exists; refusing to overwrite it." >&2
    exit 1
  fi
fi

CREATED_DB=0
SUCCESS=0
cleanup_restore() {
  local status=$?
  if [[ "$SUCCESS" != "1" ]]; then
    if [[ "$MEDIA_CREATED" == "1" && -n "$MEDIA_DEST" ]]; then
      rm -rf "$MEDIA_DEST"
    fi
    if [[ "$CREATED_DB" == "1" ]]; then
      dropdb --if-exists "$TARGET_DB" || true
    fi
  fi
  exit "$status"
}
trap cleanup_restore EXIT

createdb "$TARGET_DB"
CREATED_DB=1
pg_restore --exit-on-error --no-owner --no-acl --dbname="$TARGET_DB" "$BUNDLE/database.dump"
export DB_NAME="$TARGET_DB"
python manage.py snapshot_integrity --compare "$BUNDLE/database-manifest.json"
python manage.py migrate --check
python manage.py check --database default
if [[ "$MEDIA_INCLUDED" == "yes" ]]; then
  mkdir -p "$(dirname "$MEDIA_DEST")"
  MEDIA_CREATED=1
  python3 scripts/backup_artifacts.py media-extract --bundle "$BUNDLE" --destination "$MEDIA_DEST"
else
  python3 scripts/backup_artifacts.py media-verify --bundle "$BUNDLE"
fi

SUCCESS=1
trap - EXIT
echo "Restore verified and retained as $TARGET_DB."
if [[ "$MEDIA_CREATED" == "1" ]]; then
  echo "Media restored to $MEDIA_DEST. Point DJANGO_MEDIA_ROOT there only after operator review."
fi
