# Local synthetic notification-outbox health summary

```sh
python manage.py summarize_notification_outbox_health
```

Run only with the existing explicitly configured local synthetic demo or
disposable PostgreSQL test/restore environment accepted by the integrity guard.
The command requires `APP_ENV=demo`, PostgreSQL, a recognized local demo/test/
restore database name, and a local database host. Supply operator connection
settings through the existing environment; never store credentials in source or
point this tool at production. No new environment configuration is required.

This is a separate local metadata command. It does not modify or extend the
existing three-check integrity summary, invoke the worker, claim jobs, retry
intents, instantiate a sink, change counters, send messages or write run history.

## Deterministic output

One JSON object contains:

- A prominent **SYNTHETIC DATA ONLY** label distinguishing dry-run intent
  records from externally delivered notifications.
- `counts_by_status` with **all four keys even when empty**: `pending`, `retry`,
  `dry_run_recorded`, `dry_run_failed`.
- Total intent count, stored attempt total, intent-count distribution at each
  existing attempt count 0–3, and the worker's existing three-attempt limit.
  This reports existing counters, not an independent attempt-event audit.
- `failure_code_counts`: only the existing worker code `FAKE_SINK_FAILURE`
  is emitted verbatim. Any other nonempty stored code is grouped under
  `REDACTED_UNRECOGNIZED`; empty codes are omitted. Unknown strings are never
  returned, truncated or echoed. Code classification happens inside SQL.
- `external_notification_sent_by_this_command: false` and
  `external_delivery_verified: false`. There is no “delivered count.”

No event messages, JSON details, payloads, recipients, channels, quote/item/
customer values, names or identifiers are selected or emitted. No timestamps,
ages, durations, severity classifications or overdue thresholds are added.
Unchanged data produces byte-identical sorted-key JSON.

`status` is `empty` or `recorded_metadata`, not a claim that jobs are healthy or
externally delivered. Exit 0 means metadata was read successfully, including
when failed dry-run intents are counted. Safety/scope rejection raises
`CommandError` without outputting a misleading partial summary; database errors
remain errors rather than fabricated zero counts.

## One database snapshot and no writes

The scope check and aggregation execute inside **one PostgreSQL REPEATABLE READ,
READ ONLY transaction**. Existing outer transactions are refused. The command
fails closed if any intent or required related event, quote, BU, legal entity or
customer is outside synthetic scope, or an event is not one of the existing
readiness/storage-charge notification kinds. No record is silently filtered out
to make the counts appear successful.

All counts and attempts therefore describe the same committed snapshot. An
in-flight worker's uncommitted changes are not visible; this command does not
lock/claim intents or determine whether a worker is currently running.
`dry_run_recorded` remains an in-memory fake-sink outcome, **not delivery**.
The command is not a transport audit or approval to send notifications.

## Verification and boundaries

`foundation/test_outbox_health.py` covers empty/mixed states, exact counters,
determinism, SQL-level code redaction, no payload columns, no worker/sink/network
invocation, before/after Foundation-row equality, unsafe target/outer-transaction
rejection, supported-event scope refusal and existing database rejection of
non-synthetic fixture mutations. No constraint is disabled to create invalid
company records.

A real independent writer commits status/counter changes and a new intent
between the reader's scope query and aggregation. The reader sees only its old
snapshot; the next run sees the complete new generation. Another test verifies
isolation settings and PostgreSQL's rejection of an attempted write. Test-only
fixture writes are not command behavior.

See the final `REVIEW_HANDOFF.md` section for fresh commands/results and cleanup.
There is no web endpoint/UI, permission or assignment change, schedule,
notification, automatic retry, repair, accounting behavior, network integration,
production access or change to existing enqueue/worker semantics.
