# Portable development, backup, restore, and synthetic load baseline

For inspection without creating/restoring a backup or connecting to PostgreSQL,
see `SYNTHETIC_BACKUP_EVIDENCE.md`. Its artifact checks are not a substitute for
the restore proof below or company approval of production recovery policy.

This guide covers the synthetic development prototype only. It does not configure production hosting, production backup, a production region, or an availability target.

## Portable development setup

### Requirements

- Linux or macOS shell with Python 3.13 and Node.js 24/npm.
- PostgreSQL 16 server and client tools: `initdb`, `pg_ctl`, `createdb`, `dropdb`, `psql`, `pg_dump`, and `pg_restore`.
- `uv` is optional. Python requirements are pinned in `uv.lock` and `requirements.txt`; frontend dependencies are pinned in `package-lock.json`.

Use PostgreSQL tools whose major version matches the data directory and backup. The bundled local runner checks this and refuses to initialize over an existing non-empty data directory. On Replit, if the runtime refresh changes PostgreSQL versions, set `POSTGRES_BIN_DIR` to the installed matching tools rather than replacing `.runtime/postgres`.

### Install and run

```sh
uv sync --locked                 # or: python -m pip install -r requirements.txt
npm ci
cp .env.example .env             # optional for local defaults; .env is ignored by Git
bash scripts/dev.sh
```

The runner starts a local PostgreSQL cluster under ignored `.runtime/postgres/` when using loopback PostgreSQL, applies migrations, loads only the explicitly synthetic demo fixture, starts Django on port 8000, and starts Vite on port 5000. Open `http://127.0.0.1:5000`.

For a persistent local Django secret, generate a value locally and put it only in ignored `.env` as `DJANGO_SECRET_KEY=...`. The demo environment can generate a temporary key if left blank; that invalidates sessions when it changes. Local PostgreSQL uses the operating-system username and loopback-only trust authentication by default.

For an operator-managed **local** PostgreSQL instance, set `DB_NAME`, `DB_USER`, `DB_PASSWORD` (or use the operator's `.pgpass`), `DB_HOST` (`localhost`, `127.0.0.1`, or `::1`), and `DB_PORT` in the local environment. Create the empty development database yourself. Set `USE_BUNDLED_POSTGRES=0` so the runner will not start the workspace-local cluster. The synthetic development runner refuses remote PostgreSQL hosts, so it will not migrate or seed a remote database. Do not put actual credentials in tracked files. Production settings must be separately reviewed; do not run this development runner as a production service.

The backend and frontend can also run separately after installing dependencies and preparing PostgreSQL:

```sh
APP_ENV=demo DB_NAME=tivoli_erp_demo DB_USER="${PGUSER:-$(id -un)}" \
  DB_HOST=127.0.0.1 DB_PORT=55432 uv run python manage.py migrate
APP_ENV=demo DB_NAME=tivoli_erp_demo DB_USER="${PGUSER:-$(id -un)}" \
  DB_HOST=127.0.0.1 DB_PORT=55432 uv run python manage.py seed_demo
APP_ENV=demo DB_NAME=tivoli_erp_demo DB_USER="${PGUSER:-$(id -un)}" \
  DB_HOST=127.0.0.1 DB_PORT=55432 uv run python manage.py runserver 127.0.0.1:8000
npm run dev -- --host 127.0.0.1 --port 5000 --strictPort
```

Use the existing Replit runner for Replit preview, as it binds its preview servers appropriately and configures only the current preview origin for CSRF. Neither preview configuration nor the development CSRF origin is inherited as production configuration.

## Local synthetic database backup

Backups are restricted to `APP_ENV=demo`, the local `tivoli_erp_demo` PostgreSQL database, and a backup destination outside the project directory. The command will refuse a remote host, a different database name, or a mismatched PostgreSQL major version.

```sh
bash scripts/backup_local_demo.sh "$HOME/tivoli-erp-backups"
```

Each bundle contains a custom-format PostgreSQL dump, a schema/migration/table-row-count manifest, SHA-256 checksums, and—if the configured media directory contains files—an archive plus a file-count/byte-count manifest. The only uploaded files are supplier KYC documents (`SupplierKycDocument.file`, PDF/PNG/JPG up to 10 MB), stored under the configured `DJANGO_MEDIA_ROOT` and downloaded only through a signed-in API endpoint, never served directly. It rejects symbolic links.

The bundle is created with restrictive local permissions, but is **not encrypted**. A database backup includes Django account password hashes and must be treated as sensitive even though the current records are synthetic. Keep it outside Git, do not share it, and use an operator-approved encryption method and key management before moving it off the local machine. No backup key or credential is stored in source.

### Clean restore proof

```sh
bash scripts/test_backup_restore.sh "$HOME/tivoli-erp-backups/<backup-timestamp>"
```

This creates a new empty, uniquely named `tivoli_erp_restore_test_*` database, verifies bundle checksums and the PostgreSQL archive, restores into it, compares exact table row counts and applied migrations, runs Django migration/system checks, and restores/verifies media in a temporary location if present. It drops only the test database it created. Set `KEEP_RESTORE_DB=1` to retain the test database for inspection.

To retain a clean restored copy instead:

```sh
bash scripts/restore_local_demo.sh \
  "$HOME/tivoli-erp-backups/<backup-timestamp>" \
  tivoli_erp_restore_review
```

The restore command refuses existing databases and only accepts the `tivoli_erp_restore_` target prefix. It keeps the verified database. If media is present, it writes to a new location outside the project by default; an explicit third argument can select a different, non-existing media destination. Review before pointing `DJANGO_MEDIA_ROOT` at restored files.

## Synthetic load baseline

The harness creates and destroys a separate, fresh local `tivoli_erp_loadtest_*` database. It never adds the load dataset to the development database. Default run:

```sh
bash scripts/run_synthetic_load.sh \
  --items 8000 \
  --sessions 40 \
  --workers 40 \
  --requests-per-session 1 \
  --warmups 1
```

`--items` must be at least 8,000 (maximum 100,000); sessions and workers are limited to 100 (the expected number of named ERP users). Keep `--workers` below PostgreSQL's `max_connections` (100 by default) minus a few connections for the harness itself. Items, sessions, workers, request count, warm-ups, deterministic synthetic seed, and JSON result destination are configurable. `--keep-db` retains the isolated database; otherwise it is dropped when the run ends. The generated JSON defaults to ignored `generated-artifacts/synthetic-load-baseline.json`. A 40-worker report run can use substantial application-process memory; lower `--workers` on constrained machines.

The workload creates synthetic catalog items, one variant and stock example per item, and report-only sales examples with no cost fields. It creates up to 40 fictional users with unusable passwords, each assigned only `read_stock` for TVL and the synthetic sales-report role for TVL. It verifies that cross-unit stock and sales-report requests are denied. Each user also gets `create_order` and the salesperson role for TVL, and every catalog variant gets a current approved QAR price. The measured permitted requests are catalog lookup by item code, a scoped stock-list query, the quote screen's choices, a quote item search by a substring of an item code, and the sales report.

The harness reports request counts, response bytes, elapsed time, p50/p95/maximum/mean latency, throughput, Python process CPU and peak RSS, a host memory snapshot where available, PostgreSQL database counter deltas, and the maximum active PostgreSQL backends observed. Requests pass through Django middleware and session cookies via Django's in-process test client; timings exclude browser, TCP/TLS, reverse proxy, and external hosting overhead. This is a local baseline for the explicitly requested 8,000-item/40-session dimensions, not an SLA, capacity guarantee, or estimate of company transaction volume.

### Original unpaginated baseline (historical reference)

On the current Replit development workspace (Python 3.13.11, Django 5.2.11, PostgreSQL 16.15), the default 8,000-item / 40-user / 40-worker run completed in **247.420 seconds**. It made 40 measured requests per endpoint after one warm-up per endpoint:

| Permitted request | p50 | p95 | Max | Mean | Requests/s | Total response bytes |
|---|---:|---:|---:|---:|---:|---:|
| Catalog lookup | 1,904.167 ms | 2,726.221 ms | 3,774.749 ms | 1,973.358 ms | 0.162 | 13,840 |
| TVL stock list | 57,405.698 ms | 92,176.765 ms | 94,661.164 ms | 55,829.821 ms | 0.162 | 110,406,920 |
| TVL sales report | 179,959.821 ms | 219,028.289 ms | 223,135.695 ms | 185,374.744 ms | 0.162 | 74,922,040 |

Python process CPU time was **242.1039 seconds** and peak RSS was **2,596,472 KiB**. The host snapshot reported 8,156,256 KiB total and 2,034,356 KiB available memory. PostgreSQL database counter deltas were 8,551,054 buffer hits, 0 block reads, 4,452,124 tuples returned, and 0 temporary bytes; the activity sampler observed up to 9 active database backends. These are local application-path measurements, not a service target. The large response payloads and memory/time use show that this prototype's unpaginated stock/report responses are not suited to this concurrent baseline without further application/database work. The harness records the full result at ignored `generated-artifacts/synthetic-load-baseline.json`.

### Instrumented pagination and query optimization run

The pre-change comparison below is a separate instrumented rerun using the same request dimensions, with SQL/stage timing and `EXPLAIN (ANALYZE, BUFFERS)` enabled. It is more directly comparable to the final run than the original historical numbers above. Both used Python 3.13.11, Django 5.2.11, PostgreSQL 16.15, 8,000 catalog items/stock rows/report-only sales facts, 40 fictional sessions, 40 worker threads, one warm-up and one measured request per session per endpoint. The optimized run retained that seed and request configuration; the report and stock APIs returned page 1 at page size 50.

| Request | Pre-change p50 | Optimized p50 | Pre-change p95 | Optimized p95 | Mean response bytes, pre → optimized |
|---|---:|---:|---:|---:|---:|
| Catalog lookup | 988.444 ms | 475.928 ms | 1,467.807 ms | 1,270.012 ms | 346 → 467 |
| TVL stock list | 35,984.476 ms | 704.342 ms | 42,308.071 ms | 1,058.293 ms | 2,760,173 → 17,274 |
| TVL sales report | 64,765.609 ms | 1,040.336 ms | 85,094.435 ms | 1,459.721 ms | 1,873,051 → 10,688 |

The stock-list and sales-report payloads fell by approximately 99.4%. The catalog payload grew by 121 bytes because the response now includes explicit pagination metadata; the catalog query still uses the same five substring-search fields.

| Request | SQL queries/request, pre → optimized | SQL duration p50, pre → optimized | Database fetch p50, pre → optimized | Python transform/aggregation p50, pre → optimized | JSON render p50, pre → optimized |
|---|---:|---:|---:|---:|---:|
| Catalog lookup | 6 → 7 | 791.797 → 365.256 ms | 452.478 → 186.408 ms | 0.409 → 0.540 ms | 0.034 → 0.047 ms |
| TVL stock list | 6 → 7 | 8,769.812 → 591.909 ms | 27,197.207 → 252.990 ms | 6,517.135 → 2.576 ms | 221.621 → 0.181 ms |
| TVL sales report | 11 → 13 | 17,219.588 → 861.123 ms | 24,276.771 → 269.380 ms | 36,003.802 → 0.154 ms | 23.413 → 0.169 ms |

The final run took 3,005.558 ms for the measured batch, used 2.613 seconds of Python CPU, and peaked at 93,348 KiB process RSS, compared with 102,687.716 ms, 109.4655 CPU seconds, and 2,656,156 KiB RSS in the instrumented pre-change run. The final PostgreSQL counter delta was 5,766,048 buffer hits, 0 block reads, 0 temporary bytes, and 5,989,416 tuples returned. The activity sampler observed up to 8 active PostgreSQL backends in this short final run; samples varied between runs, so the configured 40 worker threads should not be read as proof of 40 simultaneously active SQL statements.

Plan evidence and changes:

- The stock-list plan no longer has the previous `Unique` node and wide full-row sort caused by redundant `.distinct()`. The existing authorized location/business-unit join is unique by its schema constraint. The page still uses deterministic item-code/SKU/ID ordering and PostgreSQL top-N sorting.
- The catalog search plan previously joined roughly 8,004 stock rows before applying the five-field `icontains` join filter, touching about 80,147 shared buffers. The final query builds candidate item, variant, and location ID subqueries first, then intersects them with the already-authorized stock queryset; the exact benchmark term returned one row with about 330 shared buffer hits. `icontains` still scans synthetic item/variant data because no optional trigram extension was added.
- The report period bounds now use aware half-open timestamps in the configured timezone. For the synthetic 2026-10-17 Asia/Qatar date, PostgreSQL used `demo_sales_unit_time_idx` with `business_unit_id = 1`, `created_at >= 2026-10-16 21:00:00+00`, and `created_at < 2026-10-17 21:00:00+00`; the transaction-backed line plan also used `sales_report_unit_time_idx`.
- Ordinary sales pages now fetch and transform only the requested six-field row page; sales totals and period values remain database-aggregated, and best-seller/category aggregates are skipped unless the authorized panel requests them. Database plans still read all 8,000 sample facts for complete totals and exact counts; they return only 50 report rows to the page.
- The benchmark checks that cross-unit stock and report requests are denied. Export tests confirm Excel/Word exports keep the full authorized result set and audit records, but export runtime and memory were not part of this load run.

The exact final JSON, including every per-request SQL count/timing, transformation/JSON timing, resources, and plans, is in ignored `generated-artifacts/synthetic-load-optimized-final.json`. The instrumented pre-change result is in ignored `generated-artifacts/synthetic-load-instrumented-before.json`. These short local runs are not an SLA or a daily-volume forecast; they exclude HTTP/TLS, the Replit proxy, browser rendering, long-duration soak, and dedicated database lock-wait measurements.

### 80,000 items and 100 users (October 2026)

Company sizing given on 22 October 2026: about 80,000 items, 100 ERP users and 300 employees. The quote screen previously downloaded every priced item with its stock on each load; it now gets the first 50 prices and customers with totals, and searches the server for the rest (`/api/purchasing/quotation-items/`, `/api/purchasing/quotation-customers/`). Migration `0037_search_indexes` enables `pg_trgm` and adds trigram indexes on the upper-cased item, variant and customer columns that substring search compares.

Two runs on the same 4-CPU, 16 GB development container, with PostgreSQL 16 on the same host (default settings: `max_connections` 100, `shared_buffers` 128 MB), 80,000 items, 100 session users, one measured request per user per endpoint:

| Endpoint | One request at a time: p50 / p95 | 80 identical requests released at once: p50 / p95 |
| --- | --- | --- |
| Catalog lookup (stock search) | 293 / 393 ms | 5.9 / 6.7 s |
| Stock list page | 63 / 78 ms | 8.1 / 8.3 s |
| Quote screen choices | 135 / 174 ms | 4.0 to 13.7 s (varied between runs) |
| Quote item search | 35 / 41 ms | 1.7 to 3.4 s |
| Sales report page | 33 / 40 ms | 1.6 / 1.8 s |

Before the change, the quote item search took about 250 ms on its own and about 6.4 s in the 80-request burst, because filtering prices by an OR across the joined item and variant tables scanned the whole catalog. It now finds matching variant ids through separate single-table lookups that use the trigram indexes.

The single-request column is the response time one user sees. The burst column releases 80 identical requests at the same instant into one Python process on 4 CPUs, with the database on the same machine. It measures queueing under a worst case that 100 office users rarely produce, not normal response time. Production for 100 users needs several application worker processes, a separate database server sized for the catalog, connection pooling, a shared cache for sign-in lockouts, and PostgreSQL memory settings (`shared_buffers`, `work_mem`) tuned for it; the burst run spilled about 0.9 GB of sorts to temporary files with the default `work_mem`.

## Decisions still requiring company/operator approval

- Production hosting provider, region, network exposure, identity configuration, and production deployment process.
- Approved backup location, retention, off-site replication, encryption method/key ownership and rotation, access policy, and restore authorization.
- Recovery-point and recovery-time objectives (RPO/RTO), backup frequency, and whether operational production data should be included in a future production backup design.
- Production media/attachment storage and retention. Supplier KYC documents are the only uploads today; production storage, retention and malware scanning are not set up.
- Whether a future load test should include real network/TLS hosting, longer soak runs, large full-result Excel/Word export measurements, or a company-approved workload beyond the user-specified synthetic dimensions.

Do not infer these choices from the local test results.
