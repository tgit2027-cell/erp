# Tivoli ERP — synthetic prototype

This is an independent, synthetic-data-only ERP prototype. It is not a production ERP: every service refuses to run outside `APP_ENV=demo`, and it cannot process a real transaction.

Built and tested on synthetic data: organization and scoped access, stock ledger and reservations, procurement receiving with landed charges, retail quotes through delivery, test-only QAR journals, return intake, reports and XLSX/DOCX exports, and warehouse capacity. Finance drafts run only in the disposable Finance lab. HR, operational Finance, projects, factory and fit-out are not built. See [MILESTONE_ROADMAP.md](MILESTONE_ROADMAP.md) for current status.

The source of truth is the private GitHub repository, edited locally. Replit was used only for the initial build and is no longer the editor.

## Stack

- Django 5.2.11 and Django REST Framework 3.16.1
- PostgreSQL 16 for local development and Django tests
- React 19 and TypeScript 7, built with Vite 8 (Node.js 24 required; see `.nvmrc`)

Python dependencies are pinned in `requirements.txt`, `pyproject.toml`, and `uv.lock`. Frontend dependencies are pinned by `package-lock.json`.

## Development runner

`scripts/dev.sh` opens only the **existing** local synthetic demo, checks migrations and demo provenance read-only, then supervises Django and Vite. It never initializes or creates a database, migrates or runs `seed_demo`. Missing data, pending migrations or uncertain demo provenance fail closed for separate operator review. Either HTTP child exiting stops the other.

Exposure defaults are deliberately narrow:

- Django listens on `127.0.0.1:8000` only and is reached through the Vite proxy on port 5000.
- Vite listens on `127.0.0.1` unless `DEV_BIND_HOST` is set (for example `0.0.0.0` inside a container).
- `DJANGO_ALLOWED_HOSTS` defaults to `localhost,127.0.0.1`; wildcards are refused. Vite accepts only loopback names plus any exact hostnames in `VITE_ALLOWED_HOSTS`.
- `DJANGO_DEBUG` defaults to `false`. Set it to `true` only for a private local run.
- Without `DJANGO_SECRET_KEY`, the runner generates one into the git-ignored `.runtime/django-secret-key` (mode 600) and reuses it, so restarts do not sign everyone out.
- Every sign-in, failed or refused sign-in, sign-out, account change, data change and refused request is written to an append-only audit log (Reports → Audit log, superusers only). Set `AUDIT_TRUST_X_FORWARDED_FOR=true` only behind a proxy that sets that header, so the log records the real client address.
- Sessions last 12 hours (`DJANGO_SESSION_COOKIE_AGE` overrides). Ten failed sign-ins for one username lock it for 15 minutes; the counter uses Django's per-process memory cache, so a shared cache is needed once several processes serve logins.

If `REPLIT_DEV_DOMAIN` is set, the runner still supports the old Replit preview: it trusts exactly `https://<that-domain>:5000` for CSRF, adds that hostname to the allowed hosts, uses Secure cookies and binds Vite to `0.0.0.0`. `.replit` no longer publishes Django's port 8000 or the old port-80 mapping.

Production does not use the development runner. Set `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` there to the exact production hostnames and origins.

## Run locally (Linux/macOS)

Prerequisites: Python 3.13, Node.js 24/npm, and PostgreSQL 16 command-line tools (`initdb`, `pg_ctl`, `createdb`, `psql`).

1. Install Python dependencies: `uv sync --locked` (or `python -m pip install -r requirements.txt`)
2. Install frontend dependencies: `npm ci`
3. Optionally copy `.env.example` to `.env` and set local overrides. The bundled PostgreSQL runner uses your OS username by default.
   For a **new, empty synthetic installation only**, explicitly initialize and
   seed before the first run (never use this block for existing-preview recovery):

   ```bash
   export APP_ENV=demo DB_NAME=tivoli_erp_demo DB_HOST=127.0.0.1 DB_PORT=55432
   export DB_USER="${PGUSER:-$(id -un)}" PGDATA="$PWD/.runtime/postgres"
   export ROOT="$PWD" LOCAL_PGPORT="$DB_PORT"
   source scripts/local_postgres.sh
   ensure_local_postgres
   # createdb must succeed; an existing database stops the && chain.
   createdb -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" &&
     python manage.py migrate --noinput &&
     python manage.py seed_demo
   ```

   Use the active local Python described above. For an existing database, take
   a private backup and review/apply any pending migration separately; normal
   startup deliberately does not perform schema or demo-data maintenance.
4. Run `uv run bash scripts/dev.sh` when using uv (or activate the environment created by `uv sync` before running the script). If dependencies were installed into the active system Python, use `bash scripts/dev.sh`.
5. Open the local preview at `http://127.0.0.1:5000`.
6. In a separate terminal, create credentials for one synthetic user (inside Replit, substitute `python` for `uv run python`):
   `APP_ENV=demo DB_NAME=tivoli_erp_demo DB_USER="${PGUSER:-$(id -un)}" DB_HOST=127.0.0.1 DB_PORT=55432 uv run python manage.py set_demo_password --username demo_admin`
7. Optional: load the first full proof into the demo, so there is something to click through. It buys 6 chairs abroad in EUR with USD shipping and QAR customs, sells two with a deposit and balance, delivers them with an invoice, leaves a deposit on a second open order, and then checks that everything reconciles. It is safe to run again.
   `APP_ENV=demo DB_NAME=tivoli_erp_demo DB_USER="${PGUSER:-$(id -un)}" DB_HOST=127.0.0.1 DB_PORT=55432 uv run python manage.py seed_first_proof`
   Sign in as `demo_admin` and open Reports → Scenario reconciliation.

The command prompts without echoing the password. Seeded accounts have unusable passwords until set privately. Available usernames are `demo_tivoli`, `demo_roche`, `demo_natuzzi`, `demo_multi`, and `demo_admin`. `demo_admin` is a non-superuser staff account for palette configuration with only `read_stock` grants for TVL, RBB, and NAT; it has no user-management, grant-management, or finance-posting permissions.

For an externally managed PostgreSQL server, set `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT`; do not commit actual credentials. Keep `APP_ENV=demo` only for synthetic development. The seed command refuses other environments.

To run the server and UI separately, start Django with `APP_ENV=demo DB_NAME=tivoli_erp_demo DB_USER="${PGUSER:-$(id -un)}" DB_HOST=127.0.0.1 DB_PORT=55432 uv run python manage.py runserver 127.0.0.1:8000`, then run `npm run dev -- --port 5000`.

## Tests and checks

With PostgreSQL 16 running and the `DB_*` variables set (the bundled cluster uses port 55432):

```sh
export APP_ENV=demo
export DB_NAME=tivoli_erp_demo
export DB_USER="${PGUSER:-$(id -un)}"
export DB_HOST=127.0.0.1
export DB_PORT=55432
uv run python manage.py migrate
uv run python manage.py seed_demo
# Normal backend suite. Lab-only Finance tests are tagged finance_lab and
# only pass under the Finance lab settings, so they are excluded here.
uv run python manage.py test foundation scripts.test_dev_runtime --exclude-tag finance_lab
# Finance lab backend suite, including the finance_lab-tagged tests. Build the
# lab UI first: one test checks that /lab-reversals-ui/ is served.
npm run build:finance-draft
FINANCE_LAB_BACKEND_ONLY=1 bash scripts/verify-finance-draft-workflow.sh
npm test
npm run test:contrast
npm run typecheck
npm run build
```

Notes:

- Django creates a temporary PostgreSQL test database and applies the migrations to it. SQLite is not supported as a test substitute.
- Run the backend suite serially. `--parallel` currently crashes with "cannot pickle 'traceback' object" when any test fails, because `tblib` is not installed.
- The lab and acceptance runner scripts find PostgreSQL through `RETAIL_PG_BIN`, then `pg_config --bindir`. They run `initdb`, so run them as a non-root user.
- Frontend tests need Node.js 24. On Node 22 the test loader fails for almost every UI test.
- Real-browser tests (`scripts/*-browser.test.mjs`) need Chromium and are run separately; see `scripts/BROWSER_TESTS.md`.

## Data and access boundaries

- Seeded organization: one legal entity, three separately scoped retail business units, and three independent product brands.
- Synthetic locations, items, variants, example stock, and fictional accounts only.
- Inventory examples are read-only fixtures, not an authoritative stock ledger.
- Server endpoints filter stock by currently active user-to-unit `read_stock` grants. A unit header or query parameter cannot grant access. `create_order` is a separate action grant; order creation is not implemented.
- Login uses Django sessions with HttpOnly/SameSite cookies. Cookies are Secure for the Replit HTTPS preview and production settings; local HTTP demo mode disables the Secure flag. Mutations require CSRF. No password is stored in browser storage.
- No export endpoint is implemented.

## License inventory

Direct runtime dependencies: Django (BSD 3-Clause), Django REST Framework (BSD 3-Clause), psycopg (LGPLv3 with exceptions; binary distribution uses the PostgreSQL license), React and React DOM (MIT), Vite and `@vitejs/plugin-react` (MIT), TypeScript (Apache-2.0), and React type packages (MIT). Check the exact installed versions' license files before redistribution. PostgreSQL is PostgreSQL License. This is a direct-dependency inventory, not a complete transitive/SBOM audit.

## Portability and resilience checks

Portable synthetic development setup, local PostgreSQL backup/restore proof, media-archive support, and a configurable synthetic load harness are documented in [PORTABILITY_BACKUP_AND_LOAD.md](PORTABILITY_BACKUP_AND_LOAD.md). They are local development checks only and do not establish production readiness.

Operational ledger posting (journals exist only under test-only accounting assumptions), real payment processing, payroll, outbound messaging, AI, integrations, production identity, production migration, production backups, and production deployment are not implemented.
