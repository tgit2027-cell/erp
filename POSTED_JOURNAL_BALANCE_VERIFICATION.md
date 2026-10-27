# Read-only synthetic posted-journal balance verification

This operator diagnostic checks existing test-only journal invariants. It is not approval of Tivoli accounting policy, an accounting report, or a posting/repair facility.

## Run and boundary

With the existing local synthetic database environment configured:

```sh
python manage.py verify_posted_journal_balances
```

The command reuses the guard from `snapshot_integrity`: PostgreSQL, `APP_ENV=demo`, a recognized local demo/test/restore database name and a local host. It refuses an enclosing transaction so that its own snapshot isolation and read-only mode cannot silently be weakened.

All journal/header/line/account reads occur within one **REPEATABLE READ, READ ONLY** PostgreSQL transaction. Posted journals and their related facts must be marked synthetic; detected non-synthetic related facts cause a refusal, not a misleading partial pass.

There is no HTTP endpoint, application-user capability, assignment, scheduled run, notification, repair flag or output file. Shell/database operator access is separate from ERP report permissions. The diagnostic is not run automatically against the preview or production.

## Existing invariants checked

- Each posted journal independently has at least two lines and exactly equal decimal debit and credit totals.
- Every line account belongs to the journal's existing legal entity.
- The header and accounts use QAR.

These are the existing service/deferred-trigger invariants, not new accounting rules. Entries are not netted against each other: two errors cannot cancel into a successful company-level total. No account codes/classes/mappings, tax, recognition, valuation, exchange rates, period controls, balances brought forward or historical permissions are inferred.

Numeric facts retain exact decimal arithmetic, including totals larger than a single `Numeric(16,2)` field and a caller with reduced Decimal precision. No rounding tolerance or float conversion is introduced.

## Output

Deterministic JSON contains `check`, `status` (`empty`, `consistent`, `mismatch`), `read_only`, checked entry/line/legal-entity counts, a discrepancy count/list and a prominently test-only `basis`.

Valid journal rows and their amounts are not emitted. Discrepancies contain only internal entry/legal-entity/line IDs, structural codes, expected/actual reference or currency facts, line counts, and exact decimal-string debit/credit totals/difference for an imbalance. Description, memo, policy label, account name/class/code, supplier/customer/user identity, request key, payload/hash and idempotency result are not selected.

A mismatch prints JSON and raises `CommandError` for an unsuccessful command exit. Nothing is posted, repaired, granted, revoked or audited as a side effect. Discrepancy amounts are sensitive operator output: do not expose this command as an ordinary-user endpoint or use it to bypass report permissions.

## Verification and diagnostic limits

- Existing database protections remain enabled. They prevent unbalanced/empty/single-line posted journals, cross-entity/non-QAR lines, non-QAR headers/accounts and edits to posted entries/lines.
- Although the model has a draft status, the deferred PostgreSQL trigger requires posting before commit. Tests prove this rather than persisting a draft by disabling protections. Other sessions' uncommitted drafts are not visible to the diagnostic.
- A persisted discrepancy fixture changes only mutable account legal-entity metadata inside a disposable test database. The verifier reports the now-inconsistent account reference, leaves it unchanged and returns identical JSON on repeated reads.
- Unpersistable imbalances, including offsetting errors and exact large totals, are exercised with in-memory copies of synthetic PostgreSQL facts. This is comparator coverage, not a claim that PostgreSQL accepted a corrupt posted journal.
- No-write proof combines captured SQL, unchanged full journal/line/account/idempotency facts and table-count manifests, plus an injected accidental `UPDATE` rejected by PostgreSQL read-only SQLSTATE `25006`.
- A real second connection commits a balanced journal between header and line reads. The current snapshot sees one entry/two lines; a fresh snapshot sees two/four without mixing generations.
- Balance/structural agreement does not prove historical completeness, correct recognition/valuation/account mapping, legitimate transactions, historical authorization or production readiness. Entirely missing journals and internally consistent false entries cannot be detected.
- Synthetic flags and local naming are safeguards, not proof that mislabeled content is synthetic.
- Selected facts and discrepancies are materialized in memory. No large-history benchmark, resource bound, monitoring schedule or SLA is claimed.

Exact commands, test names/counts and outcomes are recorded in `REVIEW_HANDOFF.md`. Existing stock reconciliation and transfer-pair diagnostics remain separate and unchanged.
