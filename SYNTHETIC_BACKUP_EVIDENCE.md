# Local synthetic backup-evidence summary

```sh
python -B scripts/backup_evidence.py \
  --bundle /explicit/local/synthetic/bundle \
  --synthetic \
  --observed-at 2026-10-19T12:00:00+00:00
```

This standalone standard-library script does not load Django, environment files
or database drivers. `--bundle` is required: it does not discover backups, run a
backup/restore script, connect to PostgreSQL or start services. `--synthetic`
explicitly attests that the operator supplied only synthetic evidence. Recognized
synthetic database names in the source manifest are also required. Neither
attestation nor a name proves the opaque dump's actual contents.

`--observed-at` accepts a timezone-aware ISO timestamp, normalized to UTC. If
omitted, the current UTC observation time is used. Supplying the same time with
unchanged evidence yields identical JSON. No backup-age threshold is imposed.

## Evidence, not recoverability

Output includes:

- Presence/required status and size for `database.dump`,
  `database-manifest.json`, `media-manifest.json`, `SHA256SUMS.json` and the
  conditionally required `media.tar.gz`.
- Schema-v1 database-manifest metadata: declared PostgreSQL major, table/row/
  applied-migration counts. Table names, migration names, database names,
  credentials and payloads are not returned.
- Validated schema-v1 media inclusion/count/size metadata.
- Existing checksum verifier results and streaming media-inspection results.
  Media bytes are checked in memory, including member safety/counts/lengths and
  the gzip trailer; no file is extracted to disk.
- Filesystem modification and metadata-change timestamps explicitly labeled as
  **observations only**. `ctime` is not labeled creation time; neither timestamp
  establishes when a consistent backup completed.
- Fixed, redacted issue codes for missing/incomplete, malformed, unexpected,
  unreadable, unsafe, changed-during-read or checksum-mismatched evidence.

Success is `artifact_integrity_verified` (CLI exit 0). Invalid/incomplete evidence
is non-success with exit 1. Checksum/media results remain separate so a checksum
match alone cannot hide an invalid archive.

Every response prominently states that **verified artifact integrity does not
establish restore recoverability**. `restore_recoverability` always remains
`not_established`. The dump is treated as opaque nonempty bytes: there is no
`pg_restore --list`, database/schema validation, actual restore, signature/key
verification, proof of origin or cross-artifact transaction-consistency proof.
Hashes and manifests can all be replaced together; SHA256 is not authentication.

## Strict read-only safety

- Only the existing four required bundle filenames plus optional media archive
  are accepted. Unexpected directory entries, manifest fields, checksum entries,
  duplicate JSON keys and unsupported schemas fail closed; arbitrary names and
  raw exception messages are never echoed.
- Reject parent traversal, symlinked directories/ancestors, symlinked/hardlinked
  artifacts, directories/devices/FIFOs in artifact positions and unsafe archive
  paths/types/modes or duplicates/conflicts.
- Pin the directory with a descriptor and use descriptor-relative, no-follow
  file opens. Check file identity/metadata before/after reads and directory
  membership before final success to reject observed concurrent changes.
- Use Linux `O_NOATIME` for evidence reads/listing and read-only descriptors.
  Unsupported platforms or insufficient permission for no-atime reads fail
  closed rather than retrying with mutation-prone reads. The command disables
  local bytecode-cache writing; `-B` is also recommended explicitly.
- JSON manifests have a 1MiB technical parser bound. Hash/media checks stream
  bytes, but the tool is not a resource-isolated parser for hostile giant
  archives and makes no timing/SLA claim.

Use an operator-controlled, quiescent, genuinely local synthetic directory.
Filesystem metadata checks are not an atomic filesystem snapshot or protection
against a privileged attacker/filesystem lying about metadata. A local pathname
alone cannot establish whether its underlying mount is remote; the operator must
supply local storage. No network transport is configured or invoked.

## Existing verifier compatibility and testing

`scripts/backup_artifacts.py` retains its default checksum and extraction-based
media entrypoints for existing callers. New optional reader/quiet arguments and
an explicit streaming `inspect_only` media branch let the evidence script reuse
verification without invoking those write/extraction paths.

Run the isolated, dependency-free tests:

```sh
python -B -m unittest -v scripts.test_backup_evidence
```

The tests create only temporary **synthetic file fixtures**, including an opaque
fake dump—not a real PostgreSQL backup. They do not invoke backup runners,
restore databases or connect to PostgreSQL. Evidence-command mutation guards
cover writes, subprocess/network/database activity and extraction; nanosecond
input timestamps are compared before/after. See `REVIEW_HANDOFF.md` for exact
fresh results and checks deliberately not run.

No endpoint, permission/assignment, schedule, notification, restore, repair,
retention rule, encryption/key choice, hosting region/provider or RPO/RTO is
introduced. Production recovery decisions remain unapproved.
