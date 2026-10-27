# Private preview recovery assessment — read-only, 2026-10-20

## Superseding authorized execution — 2026-10-21

The owner explicitly authorized the conditional recovery. It **completed**:
verified local demo provenance, private consistent backup, isolated restore and
exact additive foundation 0023–0027 rehearsal, preserved existing data/authority,
applied that subset and started the unchanged normal workflow. No seed/reset,
new grants or lab exposure. Read-only checks now show no pending migrations.
See [executed recovery evidence and rollback limits](PREVIEW_FINANCE_RECOVERY_2026_10_21.md).
The assessment and “no recovery executed” decision below are historical.
Operational Finance access/accounting policy remain unapproved; schema recovery
does not establish either. Shared signed-in team acceptance was not performed.

## Current decision update — editor-loading batch; no recovery executed

The older assessment below is retained as historical evidence, **not a fresh
attestation of the database**. This batch inspected current startup/migration
code and filesystem/process metadata only. It did not start the shared database
or workflow, connect to it, take a shared backup, restore it or apply upgrades.

### What is known now, and what can be checked before approval

- `scripts/dev.sh` still refuses non-demo/remote/missing targets, checks schema
  readiness/provenance and starts services only afterward. It never migrates,
  initializes, resets or seeds. Do not bypass it, fake migrations or expose the
  lab as a recovery shortcut.
- Non-secret runner **defaults** remain `APP_ENV=demo`, local PostgreSQL55432,
  `tivoli_erp_demo`, `.runtime/postgres`; `.env`/operator environment can override
  them. Defaults are **not proof of the effective target**. No secrets/.env
  values were read or printed in this batch.
- Existing `.runtime/postgres/PG_VERSION` reports major16 and that directory
  is not a symlink. No IPv4 listeners were present at55432/5000/8000 at inspection;
  `/proc/net/tcp6` was unavailable. This is not an HTTP check, proof that no
  Unix-socket service exists, minor-version attestation or continuous health.
  Platform reports `Start application` not started. Current database contents,
  provenance and applied/pending migration ledger remain **unverified**.
- Before approval one can inspect code/normal migration graph, defaults,
  cluster path/version-file and process/port evidence without changing data.
  Read-only SQL/provenance checks require a reachable database. Starting its
  existing process is an operation requiring explicit permission, not a
  “read-only check.” Do not quietly start it to complete the assessment.

### Exact reviewed schema envelope

Potential shared chain is now **foundation0023–0027**, in order; the earlier
0023–0026-only recommendation below is superseded. The actual pending subset
must come from the verified live ledger. Any other/unexpected pending operation
or schema/history drift stops dependent changes for separate review.

| Migration | Dependency / exact intended effect | Preservation/reversal limit |
| --- | --- | --- |
|0023 finance draft foundation | After0022; creates six separate synthetic draft account/dimension/value/journal/line/line-dimension tables, relationships and constraints. Empty tables, no `RunPython` seed, user assignment or operational journal/posting. Model default permissions are disabled. | Does not populate real accounts or change operational rows. Dropping new tables later destroys any draft data; not a lossless rollback. |
|0024 finance draft revisions | After0023; adds journal revision(default0)/updated_at, seventh draft table for immutable revision snapshots with unique journal/revision and synthetic constraints, and PostgreSQL update/delete protection function/trigger. | Existing draft rows, if any, get declared metadata defaults; no invented history. Reverse loses history/protection/columns. |
|0025 recurring proposals | After0024; adds kind(default journal)/empty recurrence JSON, changes kind constraints to permit explicit templates. | Prior drafts stay ordinary/empty; no generated jobs. Reverse can discard proposal metadata. |
|0026 opening worksheet | After0025; nullable as_of_date, opening kind/constraint and date required only for that kind. | No invented historical date. Reverse after opening rows exist can violate old kind constraints. |
|0027 adjustment proposals — additionally reviewed here | After0026; adds `adjustment_proposal` JSON(default `{}`); extends kind choices to accrual/prepayment; replaces recurrence/kind constraint and adds `finance_draft_adjustment_metadata`, requiring nonempty adjustment metadata only for those kinds and empty otherwise. | Earlier journal/template/opening rows keep empty adjustment metadata and existing contents. No recognition/account mapping/posting/seed/grant. Reversing discards adjustment metadata; old kind constraints can reject new-kind rows. |

No0023–0027 `RunPython`, stock/delivery/payment/refund/posting change or assignment
operation was found. DDL/defaults/constraints are still **real schema/data effects**,
not permission to apply without rehearsal. PostgreSQL function/trigger creation
in0024 must also be rehearsed. Source/schema consistency is not presumed.

Later **finance_allocation_lab0001–0013 are disposable-app-only**, excluded from
normal settings/migration graph. Do not migrate the shared demo using lab
settings, add that app, create lab test actors or expose its session endpoints.
This performance batch adds **no migration**. Earlier foundation placement still
couples0023–0027 to normal startup; HTTP isolation did not undo that coupling.

### Concrete minimum decision for Samer

Authorize this separate, gated recovery operation—**not Finance activation**:

1. Start only the existing local PostgreSQL process if needed; never `initdb`,
   `createdb`, reset or seed. Resolve only allowlisted non-secret effective
   settings through the established safe configuration flow, and confirm
   APP_ENV=demo, actual loopback/database/cluster identity and server/client
   major/minor compatibility. Reject any remote/production/uncertain target.
2. In read-only repeatable-read, obtain applied/pending migration metadata and
   inspect existing-user DemoUserMarker coverage and **all existing** synthetic
   flag tables. Emit booleans/counts only. Missing new tables are expected if
   their migrations are pending, not a reason to skip existing-table checks.
   Stop if any provenance, migration-ledger/schema or assignment fact is uncertain.
3. Quiesce app writes; preserve a current recoverable private bundle and matching
   pre-upgrade code/assets. Restore into a **different owned disposable PG16.15
   target**, verify the restored source, then rehearse only the actual pending
   subset of the reviewed0023–0027 chain. Compare existing business/auth/
   permission/Favorites/history state before/after; do not manufacture backfill.
4. Only if all criteria below pass **and this exact shared application is
   explicitly approved**, apply that pending subset to the verified demo.
   Recheck source generation/provenance/ledger immediately before applying.
   No grants or credentials change. Use the unchanged normal private runner;
   it must pass its ordinary readiness and synthetic-provenance safeguards.
   Check exact configured HTTPS/sign-in health and assets. Do not infer
   continuous availability or authenticated acceptance from unsigned HTTP200.

A concrete approval can authorize steps1–3 first, then approve step4 using the
reported exact pending subset/rehearsal comparison. It must not be read as
authority to create Finance accounts, assign access, change policy or post.
**No such operation was executed by this batch.**

### Backup/rehearsal/rollback acceptance criteria

- Existing format: `database.dump`, `database-manifest.json`,
  `media-manifest.json`, optional `media.tar.gz`, checksum metadata from
  `scripts/backup_artifacts.py`. Use the actual verifier, not invented artifact
  names. Preserve existing bundles. A dump includes account hashes: private,
  restrictive modes, outside HTTP-served paths; no payloads/hashes in reports.
- Existing `scripts/backup_local_demo.sh` starts the DB and separately runs
  `pg_dump` then `snapshot_integrity`. It is **not a no-side-effect/same-snapshot
  preflight**. Do not invoke it blindly as proof of a consistent source.
  Keep one exported read-only snapshot open and explicitly bind dump/source
  manifest/fingerprints to it for the upgrade proof. Quiesce media mutations
  separately; PostgreSQL snapshot alone cannot freeze filesystem media.
- Verify checksums/media inventories, readable archive/schema/migration
  metadata and `pg_restore --list`; restore to disposable target **before**
  upgrading. Compare source/restored logical values, visible column relative
  order/types/defaults/constraints, sequences, users/grants, Favorites,
  transactions and historical snapshots. Physical attnum gaps may compact.
- Each normal PostgreSQL migration is transactional by default; the complete
  chain is not necessarily one transaction. A later failure can leave earlier
  reviewed migrations applied. Stop serving/preserve state and use the rehearsed
  recovery route, not blind partial reversal. Compare old fields separately
  from declared new defaults. Allowed changes: reviewed new draft schema/
  defaults/constraints/trigger and migration/content-type metadata only.
  Existing users, credentials, role/capability memberships/assignments,
  Favorites, operational rows and issued snapshots must match. No new auth
  Permission/grant is presumed allowed; default_permissions=() is verified.
- Fail/stop on version/provenance/drift, restore/checksum mismatch, unexpected
  migration, constraint failure, operational/auth change, data loss or failed
  readiness/navigation checks. Do not “repair” records, fake history, seed or
  migrate down to force startup.
- Keep source intact. If shared application later fails, stop app serving;
  restore the rehearsed pre-upgrade bundle into a separate target, validate
  source equality and matching old code, then require explicit operator cutover.
  No destructive reset/drop of the original or blind reverse migrations.
  Verified checksums alone are not restore recoverability, RPO/RTO or hosting
  policy. If backup/restore proof cannot be completed, do not apply shared DDL.

**Outstanding:** permission for DB-only start/preflight; actual target/provenance/
ledger; current backup/exported-snapshot/isolated restore and upgrade rehearsal;
explicit application approval; service health and owner sign-in/team acceptance.
All remain distinct from completed disposable Finance draft evidence.

## Original assessment outcome and evidence boundary — historical

No shared migrations, backup/restore operations, service starts, seeding or shared
record/credential/grant edits were performed for this assessment. The existing
startup guard remains intact.

- `scripts/dev.sh` explicitly defaults to `APP_ENV=demo`, loopback port 55432 and
  `tivoli_erp_demo`. It requires an existing cluster/database, checks all pending
  migrations read-only, checks synthetic provenance, then starts services.
  Neither migration application nor `seed_demo` is part of this runner.
- A plain Django shell defaults to `APP_ENV=development`; it is not equivalent
  to this runner. The first preflight correctly refused that context. A second
  process using the runner's explicit demo environment confirmed the expected
  loopback/database configuration but could not connect (`OperationalError`).
  No service was started to make this read possible.
- Therefore **current database contents, migration ledger and synthetic
  provenance could not be freshly attested**. Configuration and the previous
  recorded migration log are evidence, not proof of current contents.
- The last recorded managed startup stopped because 0023–0025 were pending.
  This is not evidence that the new worksheet migration has been applied
  anywhere shared. New migration 0026 is also required by the current code;
  it has only been applied in disposable test databases.
- Normal `config.urls`/`foundation.urls` do not expose the isolated Finance lab.
  `config.finance_lab_settings` and `foundation.finance_lab_urls` require the
  disposable database path and explicit synthetic test access.

## Exact migration effects and dependencies

All four migrations use PostgreSQL's normal atomic migration transactions.
There are no `RunPython` data migrations, seed calls, production account mappings,
user/grant assignments, or operations on operational journal/stock/payment tables.
Additive does **not** mean zero locking, zero metadata effects, or automatic
approval to apply them.

| Migration | Dependency | Forward effects | Existing-data effects / rollback caveat |
|---|---|---|---|
| 0023_finance_draft_foundation | 0022_quotation_presentation | Creates six draft-only tables: account, dimension, dimension value, journal, line and line/dimension join. Adds M2M state, FKs to existing entity/BU, unique codes/join pairs, TEST-DRAFT code and required English label checks, nonnegative amounts, synthetic-only checks, QAR/draft-only journal checks. `default_permissions=()` for all new models. | No operational row rewrite. New empty tables/indexes/constraints; post-migrate can create Django content types, not user grants or default model permissions. Reversing drops draft tables and would destroy their data. |
| 0024_finance_draft_revisions | 0023 | Adds revision (default 0) and updated_at to draft journals; creates seventh draft table, immutable revision snapshots with unique journal/revision and synthetic constraint; creates PL/pgSQL function/trigger preventing revision UPDATE/DELETE. | Timestamp/revision defaults affect any existing draft rows only; no manufactured revision history. On the expected fresh 0023 tables there are no such rows. Reverse removes history/table/trigger/function/columns: not a lossless operational rollback. |
| 0025_finance_recurring_proposals | 0024 | Adds kind (journal default) and recurrence_proposal (empty JSON default); constraint permits journal/empty proposal or recurring template/nonempty proposal. | Prior drafts remain ordinary journals with empty proposals; no scheduled jobs, generated occurrences or postings. Reverse discards proposal metadata. |
| 0026_finance_opening_worksheet | 0025 | Adds nullable as_of_date, extends kind choices/constraint for opening_balance with empty recurrence JSON, requires date only on opening worksheets and null date otherwise. | Existing journal/recurring rows retain null dates, without invented backfill. Reversing after opening worksheets exist cannot satisfy the old kind constraint; use rehearsed restore/code rollback instead of blind reversal. |

### Why isolated Finance currently gates the shared preview

Finance models/migrations were placed in the existing `foundation` app. Although
HTTP/UI access is isolated, the ordinary app loads that same model registry and
migration graph. The global `migrate --check` consequently regards these
migrations as required. **Schema isolation was not achieved by HTTP isolation.**
This coupling made the Finance schema mandatory for current normal startup;
it was not an approved grant of shared Finance access.

Do not fake migration records, edit applied migration history, selectively skip
pending checks, weaken authentication, or expose the lab to solve this.
Separating Finance into another installed app could be a future architectural
change, but requires its own state/data migration plan; it is not a safe quick fix.

## Original conservative recovery proposal — superseded by current decision above

Authorize a separate recovery operation to:

1. Start **only the existing local database**, if necessary, without running the
   application or seed command. Verify its resolved cluster path matches
   `.runtime/postgres`, major version 16, expected database/loopback target and
   actual pending migration ledger. Refuse remote/production/uncertain targets.
2. In one read-only repeatable-read transaction check all existing users against
   `DemoUserMarker` and every **existing** synthetic-flag table for non-synthetic
   rows. Missing new Finance tables are an expected schema fact, not permission
   to skip checks on existing data. Report booleans/counts only, no payloads.
3. Preserve and verify a current private recoverable bundle; restore it to a
   different disposable PG16.15 cluster and rehearse **only the actually pending,
   reviewed 0023–0026 chain at that point**; current review also includes0027,
   as detailed above. An unexpected pending migration stops dependent
   changes. No reseed/reset or shared fixture creation.
4. After the rehearsal and explicit approval, apply that exact pending additive
   chain to the verified demo, comparing business/permission data before/after;
   then use the unchanged normal private startup workflow.

This approval concerns schema recovery only. It does not approve Finance
capabilities, shared-user exposure, accounts, policy or assignments.

## Backup, restore and rollback validation plan — not executed here

- Quiesce application mutations; preserve all existing bundles. Match all client
  binaries to PG16.15 and explicitly target every command. Capture dump and
  source fingerprints from **one exported snapshot**. Do not rely on separately
  timed dump/manifest reads during writes.
- Reuse the existing bundle format/checksum/media verifier and logical integrity
  comparison. Review wrappers before running: the current backup wrapper starts
  the database and takes dump/manifest separately, and rejects in-project bundle
  paths. It must not be invoked blindly as a no-side-effects/single-snapshot tool.
- Use a private non-HTTP-served location, restricted file modes, checksum
  verification, readable `pg_restore --list`, schema/migration metadata and media
  inventory. Never put dumps or manifests into public assets or print contents.
- Restore database/media only into a fresh disposable target. Compare visible
  column definitions/order, source row counts/digests, sequences, constraints,
  migration ledger, users, grants, Favorites, quotations/transactions and
  historical snapshots. Restored physical column ordinal gaps need not match.
- Rehearse pending migrations there, compare existing operational/auth tables
  unchanged, permit only reviewed new Finance schema/migration/content-type
  metadata, verify existing auth and representative navigation, and capture exact
  commands/counts. The current fresh-database tests are **not** this existing-data
  restore/rehearsal.
- Before shared application, repeat the quiesced source comparison to prevent
  backup-generation drift. Retain the matching pre-upgrade code/assets.
- On failure, stop serving and restore the verified pre-upgrade bundle into a
  separate target, validate it, and obtain the operator's explicit cutover
  approval. Never drop/reset the original to improvise rollback. Integrity
  checks alone are not proof of recoverability, production RPO/RTO or off-site
  retention. No backup-age threshold, hosting or production decision is implied.

## Original independent development record

The opening-balance batch established English-interface, non-posting proposals in
the explicitly enabled disposable lab. They do not depend on this shared
recovery approval. See REVIEW_HANDOFF.md for actual current test results and
the distinction between persisted synthetic drafts and shared-user authority.
