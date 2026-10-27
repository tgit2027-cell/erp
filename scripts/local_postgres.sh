# Shared local PostgreSQL bootstrap for the development and synthetic-load runners.
# Source this file, then call ensure_local_postgres from a bash script.

ensure_local_postgres() {
  local root="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
  export PGDATA="${PGDATA:-$root/.runtime/postgres}"
  export LOCAL_PGPORT="${LOCAL_PGPORT:-55432}"
  export PGUSER="${PGUSER:-$(id -un)}"
  mkdir -p "$root/.runtime"

  if [[ -s "$PGDATA/PG_VERSION" ]]; then
    local data_major ctl_version ctl_major bin_dir
    data_major="$(cat "$PGDATA/PG_VERSION")"
    ctl_version="$(pg_ctl --version | awk '{print $3}')"
    ctl_major="${ctl_version%%.*}"
    if [[ "$ctl_major" != "$data_major" ]]; then
      bin_dir="${POSTGRES_BIN_DIR:-}"
      if [[ -z "$bin_dir" && -d /nix/store ]]; then
        bin_dir="$(
          for candidate in /nix/store/*-postgresql-"$data_major".*/bin; do
            if [[ -x "$candidate/pg_ctl" && -x "$candidate/postgres" ]]; then
              local version
              version="$("$candidate/pg_ctl" --version | awk '{print $3}')"
              printf '%s %s\n' "$version" "$candidate"
            fi
          done | sort -V | tail -n 1 | awk '{print $2}'
        )"
      fi
      if [[ -z "$bin_dir" || ! -x "$bin_dir/pg_ctl" || ! -x "$bin_dir/postgres" ]]; then
        echo "PostgreSQL $data_major tools are required for the existing local data. Set POSTGRES_BIN_DIR to matching tools; refusing to reinitialize or upgrade it." >&2
        return 1
      fi
      export PATH="$bin_dir:$PATH"
      echo "Using PostgreSQL $("$bin_dir/pg_ctl" --version | awk '{print $3}') for the existing local data."
    fi
  elif [[ -d "$PGDATA" ]] && find "$PGDATA" -mindepth 1 -print -quit | grep -q .; then
    echo "PGDATA is non-empty but has no PG_VERSION; refusing to initialize over it: $PGDATA" >&2
    return 1
  else
    mkdir -p "$PGDATA"
    initdb -D "$PGDATA" -U "$PGUSER" --auth-local=trust --auth-host=trust >/dev/null
  fi

  if ! pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    pg_ctl -D "$PGDATA" \
      -o "-h 127.0.0.1 -p $LOCAL_PGPORT -c unix_socket_directories=$PGDATA" \
      -l "$PGDATA/server.log" start
  fi
}
