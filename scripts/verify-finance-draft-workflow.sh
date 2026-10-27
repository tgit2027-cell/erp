#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
BIN="${RETAIL_PG_BIN:-$(pg_config --bindir 2>/dev/null || echo /nix/store/lhil0kwh7czaa9wx0h5q7gi7xynn8kqy-postgresql-16.15/bin)}"
ROOT="$(mktemp -d /tmp/tivoli-finance-lab.XXXXXX)"
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi
  "$BIN/pg_ctl" -D "$ROOT/data" -m fast stop >/dev/null 2>&1 || true
  rm -rf "$ROOT"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
"$BIN/initdb" -D "$ROOT/data" -U "$(id -un)" --auth-local=trust --auth-host=trust --encoding=UTF8 --no-locale > "$ROOT/init.log"
"$BIN/pg_ctl" -D "$ROOT/data" -l "$ROOT/postgres.log" -o "-h 127.0.0.1 -p 55167 -c unix_socket_directories=$ROOT" start
export PGHOST=127.0.0.1 PGPORT=55167 PGUSER="$(id -un)" PGDATABASE=test_finance_lab
export APP_ENV=demo DB_HOST="$PGHOST" DB_PORT="$PGPORT" DB_USER="$PGUSER" DB_NAME="$PGDATABASE" DB_PASSWORD=''
unset DATABASE_URL PGPASSWORD PGSERVICE PGOPTIONS DJANGO_SECRET_KEY SESSION_SECRET
export DJANGO_SETTINGS_MODULE=config.finance_lab_settings EXPECTED_DISPOSABLE_DATA="$ROOT/data"
export FINANCE_LAB_ORIGIN=http://127.0.0.1:18069
ACTUAL="$("$BIN/psql" -X -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres -Atc "SELECT current_setting('data_directory')")"
[[ "$ACTUAL" == "$EXPECTED_DISPOSABLE_DATA" ]] || { echo "Refusing unverified database."; exit 1; }
"$BIN/createdb" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE"
"$BIN/psql" -X -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Atc \
  "SELECT current_setting('server_version'),current_setting('data_directory'),current_database()"
python manage.py migrate --noinput > "$ROOT/migrations.log" 2>&1
if [[ "${FINANCE_LAB_SKIP_BACKEND:-0}" != 1 ]]; then
if [[ "${FINANCE_LAB_REVERSAL_ONLY:-0}" == 1 ]]; then
  python manage.py test finance_allocation_lab.test_reversals finance_allocation_lab.test_recovery --verbosity 2
elif [[ "${FINANCE_LAB_RECOVERY_ONLY:-0}" == 1 ]]; then
  python manage.py test finance_allocation_lab.test_recovery --verbosity 2
elif [[ "${FINANCE_LAB_ADVANCE_ONLY:-0}" == 1 ]]; then
  python manage.py test finance_allocation_lab.test_supplier_advances finance_allocation_lab.test_invoices --verbosity 2
elif [[ "${FINANCE_LAB_CUSTOMER_CREDIT_ONLY:-0}" == 1 ]]; then
  python manage.py test finance_allocation_lab.test_customer_credits finance_allocation_lab.test_customer_invoices --verbosity 2
elif [[ "${FINANCE_LAB_CUSTOMER_INVOICE_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab.test_customer_invoices finance_allocation_lab.test_invoices --verbosity 2
  elif [[ "${FINANCE_LAB_SUPPLIER_ADJUSTMENT_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab.test_supplier_adjustments finance_allocation_lab.test_invoices --verbosity 2
  elif [[ "${FINANCE_LAB_INVOICE_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab.test_invoices finance_allocation_lab.test_forecasts --verbosity 2
  elif [[ "${FINANCE_LAB_FORECAST_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab.test_forecasts finance_allocation_lab.test_assets --verbosity 2
  elif [[ "${FINANCE_LAB_ASSET_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab.test_assets --verbosity 2
  elif [[ "${FINANCE_LAB_EXPENSE_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab.test_expenses --verbosity 2
  elif [[ "${FINANCE_LAB_RECONCILIATION_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab.test_reconciliation --verbosity 2
  elif [[ "${FINANCE_LAB_BUDGET_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab.test_budgets --verbosity 2
  elif [[ "${FINANCE_LAB_ALLOCATION_ONLY:-0}" == 1 ]]; then
    python manage.py test finance_allocation_lab --verbosity 2
  else
    python manage.py test finance_allocation_lab foundation.test_finance_drafts foundation.test_finance_draft_workflow foundation.test_finance_recurrence foundation.test_finance_opening foundation.test_finance_adjustments --verbosity 2
  fi
fi
if [[ "${FINANCE_LAB_BACKEND_ONLY:-0}" == 1 ]]; then
  python manage.py check --database default
  python manage.py migrate --check
  python manage.py makemigrations --check --dry-run
  exit 0
fi
python manage.py shell --no-imports -c "from scripts.finance_lab_fixture import prepare; prepare()"
BEFORE="$(python manage.py shell --no-imports -c "from scripts.finance_lab_fixture import operational_digest; print(operational_digest())")"
python manage.py runserver 127.0.0.1:18069 --noreload > "$ROOT/server.log" 2>&1 &
SERVER_PID=$!
for i in {1..80}; do
  if curl --silent --fail "$FINANCE_LAB_ORIGIN/lab/session/" >/dev/null; then break; fi
  sleep .2
done
curl --silent --fail "$FINANCE_LAB_ORIGIN/lab/session/" >/dev/null
# A pre-existing listener must never be mistaken for this isolated server.
python - "$SERVER_PID" <<'PY'
import os, sys
from pathlib import Path
pid = int(sys.argv[1])
os.kill(pid, 0)
inodes = set()
for fd in Path(f"/proc/{pid}/fd").iterdir():
    try:
        value = fd.readlink().as_posix()
        if value.startswith("socket:["): inodes.add(value[8:-1])
    except OSError:
        pass
rows = [line.split() for line in Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]]
assert any(row[1] == f"0100007F:{18069:04X}" and row[3] == "0A" and row[9] in inodes for row in rows)
print("Dedicated Finance lab HTTP listener and disposable PG16.15 ownership verified.")
PY
npm run build:finance-draft
curl --silent --fail "$FINANCE_LAB_ORIGIN/lab-ui/" > /dev/null
if [[ "${FINANCE_LAB_REVIEW:-0}" == 1 ]]; then
  echo "DISPOSABLE SYNTHETIC REVIEW ONLY: $FINANCE_LAB_ORIGIN/lab-ui/ — explicit test-session button, no credentials; Ctrl-C deletes this database."
  wait "$SERVER_PID"
  exit 0
fi
if [[ "${FINANCE_LAB_LOADING_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-editor-load-real-browser.test.mjs scripts/finance-reversal-real-browser.test.mjs
elif [[ "${FINANCE_LAB_REVERSAL_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-reversal-real-browser.test.mjs
elif [[ "${FINANCE_LAB_RECOVERY_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-recovery-real-browser.test.mjs
elif [[ "${FINANCE_LAB_ADVANCE_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-supplier-advance-real-browser.test.mjs
elif [[ "${FINANCE_LAB_CUSTOMER_CREDIT_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-customer-credit-real-browser.test.mjs
elif [[ "${FINANCE_LAB_CUSTOMER_INVOICE_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-customer-invoice-real-browser.test.mjs
elif [[ "${FINANCE_LAB_SUPPLIER_ADJUSTMENT_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-supplier-adjustment-real-browser.test.mjs
elif [[ "${FINANCE_LAB_INVOICE_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-invoice-real-browser.test.mjs
elif [[ "${FINANCE_LAB_FORECAST_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-forecast-real-browser.test.mjs
elif [[ "${FINANCE_LAB_ASSET_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-asset-real-browser.test.mjs
elif [[ "${FINANCE_LAB_EXPENSE_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-expense-real-browser.test.mjs
elif [[ "${FINANCE_LAB_RECONCILIATION_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-reconciliation-real-browser.test.mjs
elif [[ "${FINANCE_LAB_BUDGET_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-budget-real-browser.test.mjs
elif [[ "${FINANCE_LAB_ALLOCATION_BROWSER_ONLY:-0}" == 1 ]]; then
  node --test --test-concurrency=1 scripts/finance-allocation-real-browser.test.mjs
else
  node --test --test-concurrency=1 scripts/finance-draft-real-browser.test.mjs scripts/finance-allocation-real-browser.test.mjs scripts/finance-budget-real-browser.test.mjs scripts/finance-reconciliation-real-browser.test.mjs scripts/finance-expense-real-browser.test.mjs scripts/finance-asset-real-browser.test.mjs scripts/finance-forecast-real-browser.test.mjs scripts/finance-invoice-real-browser.test.mjs scripts/finance-supplier-adjustment-real-browser.test.mjs scripts/finance-customer-invoice-real-browser.test.mjs scripts/finance-customer-credit-real-browser.test.mjs scripts/finance-supplier-advance-real-browser.test.mjs scripts/finance-recovery-real-browser.test.mjs scripts/finance-reversal-real-browser.test.mjs
fi
AFTER="$(python manage.py shell --no-imports -c "from scripts.finance_lab_fixture import operational_digest; print(operational_digest())")"
[[ "$BEFORE" == "$AFTER" ]] || { echo "Operational/grant digest changed unexpectedly."; exit 1; }
echo "All operational foundation rows and test-actor authority unchanged; only draft/session/login bookkeeping was exercised."
python manage.py check --database default
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
