#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: scripts/test_backup_restore.sh BACKUP_DIRECTORY" >&2
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
export DB_HOST="${DB_HOST:-127.0.0.1}"
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
if [[ -n "$DB_PASSWORD" ]]; then
  export PGPASSWORD="$DB_PASSWORD"
fi
export PGHOST="$DB_HOST"
export PGPORT="$DB_PORT"
export PGUSER="$DB_USER"
TARGET_DB="tivoli_erp_restore_test_$(date -u +%Y%m%d%H%M%S)_$$"
KEEP_RESTORE_DB="${KEEP_RESTORE_DB:-0}"

cleanup_test_db() {
  local status=$?
  if [[ "$KEEP_RESTORE_DB" != "1" ]]; then
    dropdb --if-exists "$TARGET_DB" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup_test_db EXIT
"$ROOT/scripts/restore_local_demo.sh" "$1" "$TARGET_DB"
if [[ "$KEEP_RESTORE_DB" == "1" ]]; then
  echo "Clean restore test passed; database retained as $TARGET_DB."
  trap - EXIT
else
  echo "Clean restore test passed; temporary database will be removed."
fi
