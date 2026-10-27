# Local synthetic operations-integrity summary

`python manage.py monitor_synthetic_integrity` runs the existing diagnostics in
this fixed order:

1. Stock quantities: `reconcile_stock_quantities`.
2. Transfer pairs: `verify_stock_transfer_pairs`.
3. Posted journals: `verify_posted_journal_balances`.

This is **SYNTHETIC DATA ONLY**, an operator-run command, not an HTTP service or
production monitoring system. Use an explicitly configured, local synthetic demo
or disposable restore/test database accepted by the existing integrity guard:
`APP_ENV=demo`, a recognized database name, PostgreSQL, and a local host. Supply
connection settings/secrets through the existing operator environment, not source
control. Never point this command at production. It refuses an existing outer
transaction before any diagnostic runs.

## Results and exit behavior

The command writes one JSON object to stdout with stable ordering and no
timestamps, durations or backend PIDs. Repeated runs on unchanged facts produce
identical JSON.

For each check:

- `execution: passed` means the diagnostic **executed successfully**, not that
  its data necessarily reconciles. `outcome` distinguishes `empty`,
  `no_discrepancies` and `discrepancies`; `result` is the unchanged diagnostic
  output, including its existing basis, counts and discrepancy details.
- `execution: failed_to_run` means no integrity conclusion is available for
  that check. `outcome` is `unavailable` and `result` is null, never a fabricated
  zero-discrepancy report. The error includes its exception class and a fixed
  message, not arbitrary exception text, SQL or connection details.
- A check failure does not prevent later independent checks from running.

Top-level `status` is `failed_to_run` if any check could not execute, otherwise
`discrepancies` if any completed check found discrepancies, otherwise `passed`.
`failed_check_count` counts execution failures.
`completed_check_discrepancy_count` counts discrepancies from completed checks
only; it must not be interpreted as a complete total when a check failed. It is
a sum of diagnostic findings, not a deduplicated count of business incidents.

Exit 0 means all checks executed with no discrepancies (including empty ledgers).
A discrepancy or execution failure writes the JSON first, then raises a Django
`CommandError` (CLI exit 1) on stderr. Global safety-preflight rejection also
raises `CommandError` but does not produce a misleading completed summary.
Process interruption/termination is not caught or reported as success.

## Snapshot boundaries

**The combined output does not share one snapshot.** Each unchanged diagnostic
opens its own database-enforced `REPEATABLE READ, READ ONLY` transaction. All
queries within a diagnostic see one consistent snapshot. An intervening commit
can therefore appear in later checks but not earlier ones.

Keeping these independent transactions preserves the proven existing diagnostic
contracts, permits later checks to proceed after an SQL error rolls back one
check, and avoids weakening their protection against execution inside an
uncontrolled outer transaction. The summary is not a cross-ledger, point-in-time
reconciliation and never compares facts across these snapshots. Both
`shared_snapshot: false` and the transaction scope are explicit in the output.

## Non-goals and limits

No writes, repairs, postings, persisted run history, schedules, transports,
notifications, recipients, network sinks, web endpoints, grants or role
assignments are added. No accounting policy, valuation or historical access
approval is inferred. All existing individual diagnostic scope and limitations
continue to apply. Local operator access does not grant ERP-user access.

This is deterministic integrity reporting, not AI monitoring, alert
deduplication, operational severity policy, backup-freshness monitoring,
production readiness, or an SLA. Empty results are not business acceptance.

See `REVIEW_HANDOFF.md` for exact fresh verification. The seven tests in
`foundation/test_operations_monitoring.py` use disposable synthetic PostgreSQL:
empty/healthy facts, deterministic no-write output, an allowed inconsistent
balance fixture, a real division-by-zero SQL failure, read-only write rejection
in all three transactions, unsafe-target/outer-transaction rejection, and a real
independent writer committing between checks to prove the documented snapshot
boundary.
