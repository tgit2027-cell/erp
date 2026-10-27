---
name: Backup and recovery approval boundaries
description: Keep production hosting and recovery choices with the company operator.
---

Local synthetic backup, restore, and load evidence is not production readiness. Do not select or imply an approved production provider or region, backup location, encryption/key lifecycle, retention, replication, backup cadence, RPO, or RTO. Do not infer daily transaction volume from the synthetic 8,000-item/40-session load test.

**Why:** The user explicitly reserved these hosting, security, recovery, and workload decisions for company/operator approval and prohibited connecting production data or choosing a final provider.

**How to apply:** Keep future development and tests isolated to synthetic data. When production hosting or recovery is requested, separate technical options from approved company policy and obtain operator decisions before configuring services or targets.

Workspace recovery bundles must also be excluded from development HTTP file
serving; Git/deployment ignores and filesystem mode 0600 are not sufficient.
**Why:** The dev server runs as the same OS user and can serve a private archive
from an ignored workspace directory without ERP authentication.
**How to apply:** Never place private files in public assets; preserve the
framework's default secret-file denials and explicitly deny private backup,
runtime and evidence directories, testing ordinary and absolute-file URLs.

Restore schema comparison should use visible column order and definitions,
not absolute physical attribute/ordinal numbers.
**Why:** PostgreSQL dumps omit dropped columns; a clean restore compacts those
slots without changing visible columns, row contents or application semantics.
**How to apply:** Compare relative order, types, nullability, defaults, lengths/
precision and data. Retain stricter physical-schema comparisons for an in-place
choices-only upgrade; do not ignore genuine missing/changed columns.

Capture a backup dump, its integrity manifest and source fingerprints from
one consistent exported PostgreSQL snapshot when proving an upgrade.
Quiesce application mutations during the final source comparison and upgrade.

**Why:** Independently timed dump/count/hash queries can describe different
database generations and cannot prove the restored bundle matches its source.
An intended pending application migration is not evidence of backup corruption.

**How to apply:** Verify the restored historical bundle before applying the
reviewed migration, then run current-schema checks afterward. Preserve an
unchanged pre-upgrade bundle and compare legacy fields separately from newly
added defaults; do not seed or invent backfill to pass startup checks.

Read-only backup-evidence inspection must not reuse extraction-based verification,
even if extraction happens only in a temporary directory. Preserve evidence access
times or fail closed if the platform/operator cannot support no-atime reads.

**Why:** The owner authorized evidence inspection with no filesystem mutation,
media extraction, database connection or subprocess. Existing restore-oriented
verification can write temporary files; ordinary reads can also change atime.

**How to apply:** Use descriptor-relative no-follow/no-atime reads and streaming
verification. Label filesystem times as observations, never backup-completion
proof, and keep artifact integrity distinct from restore recoverability.
