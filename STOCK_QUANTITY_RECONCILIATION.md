# Read-only synthetic stock quantity reconciliation

This completes the architecture's quantity-projection reconciliation foundation, not stocktake, valuation, financial approval, or production monitoring.

## Operator command

Use the existing operator-supplied local PostgreSQL connection configuration:

```sh
APP_ENV=demo python manage.py reconcile_stock_quantities
```

For an isolated run, supply `DB_HOST`, `DB_PORT`, `DB_NAME`, and `DB_USER` for the disposable cluster, with any password supplied privately through the environment. Do not copy credentials into source or documentation. The command does not initialize a database, run migrations, seed data, create users/grants, or write files.

The existing integrity-snapshot safety guard is reused unchanged: demo environment, PostgreSQL, local host, and the recognized synthetic demo/test/restore database names. This is an operator-only CLI requiring existing database access, not an HTTP endpoint or a new ERP-user capability. It must not be exposed to ordinary stock readers, who receive only the existing authorized unreserved-quantity projection.

## Check semantics

- Compare each synthetic balance's stored on-hand quantity against the exact sum of its synthetic movement quantity deltas.
- Compare its stored reserved quantity against the exact sum of active synthetic reservation quantities. Released and consumed reservations are not active.
- Use a standalone PostgreSQL `REPEATABLE READ, READ ONLY` transaction. A concurrent atomic receipt/transfer/reservation must not produce a false discrepancy by mixing generations of facts.
- Keep quantities decimal-safe. Zero-quantity landed-charge movements do not add stock; illustrative `StockExample` records are never read or substituted.
- Emit JSON with status `empty`, `consistent`, or `mismatch`, checked bucket count, and exact discrepancy strings in deterministic bucket/field order. Empty means no transactional buckets, not a successful historical completeness proof.
- On a mismatch, print the diagnostic JSON and exit unsuccessfully. Never repair a balance, append a movement/event, or change an idempotency record.

No cost/value, supplier, identity, or measurement contents are emitted. Discrepancies identify the synthetic balance ID and the stored/derived quantity only. Existing capacity fields and their independent permissions are untouched.

## Limits and next independent checks

The command proves only the equality of current quantity projections with existing movement/active-reservation facts. Matching corrupt source facts could still pass. It does not validate financial values, transfer-pair structure, journal balancing, BU relationship integrity, missing historical imports, or stocktake/correction policy. A synthetic benchmark bucket created without movement history is correctly reported as unreconciled; no opening-balance assumption is invented.

There is no schedule, network notification, repair mode, or application UI. Separate `verify_stock_transfer_pairs` and `verify_posted_journal_balances` diagnostics now check existing transfer-pair structure/references and synthetic posted-journal balance invariants, each in its own consistent read-only snapshot; see `STOCK_TRANSFER_VERIFICATION.md` and `POSTED_JOURNAL_BALANCE_VERIFICATION.md`. Returns, count corrections, valuation, closed periods and production monitoring/remediation need separate approved scope/policy.

Exact current tests and executed checks are in `REVIEW_HANDOFF.md`.
