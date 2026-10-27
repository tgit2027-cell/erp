# Read-only synthetic stock transfer-pair verification

This is an offline operator diagnostic for the existing synthetic transfer facts. It is not a new transfer workflow, permission, repair facility or accounting acceptance.

## Run

Using the existing local synthetic-development database configuration:

```sh
python manage.py verify_stock_transfer_pairs
```

The command reuses the same guard as `snapshot_integrity` and `reconcile_stock_quantities`: PostgreSQL, `APP_ENV=demo`, a recognized local demo/test/restore database name, and a local host. It rejects execution inside an existing transaction rather than silently using a weaker isolation level. It must not be pointed at production or real company records.

The command was exercised only against disposable synthetic PostgreSQL data during implementation. Its presence is not authorization to run a scheduled job or expose results to application users.

## Checks

In one standalone PostgreSQL **REPEATABLE READ, READ ONLY** transaction:

- Read synthetic transfer headers and their existing stock-movement references.
- Require exactly one `transfer_out` and one `transfer_in` movement per header; report missing, duplicate or extra sides.
- Compare outbound quantity to negative header quantity and inbound quantity to positive header quantity. Equal opposite quantities alone are insufficient if both differ from the header.
- Compare movement bucket IDs to the header's source/destination balance IDs and movement idempotency IDs to the header's idempotency ID.
- Check the linked operation's existing `transfer` type and `complete` state.
- Check that the existing source/destination balance dimensions agree on legal entity, business unit and variant, with different locations, as the existing service requires.
- Include transfer-kind movements when detecting a missing recognized header reference. Reject non-synthetic related records rather than silently considering such a transfer valid.

There is no receipt, stock, reservation, journal, audit, grant, idempotency or other write. The transaction is database-enforced read-only; the command never creates a discrepancy record or changes a malformed fact.

## Output and exit behavior

JSON contains:

- `check`, `status` (`empty`, `consistent`, `mismatch`), `read_only`, and test-only `basis`.
- `checked_transfers`, `checked_movements`, `discrepancy_count`.
- Deterministically ordered discrepancies with transfer/movement/bucket/idempotency IDs, structural codes, side counts and exact decimal-string quantities where applicable.

`empty` means no transfer headers or transfer movements were present. It is not proof of historical completeness. A mismatch prints its JSON, then raises `CommandError` for an unsuccessful command exit. Nothing is repaired.

No transferred value, movement value, inventory value, cost, supplier, user identity, capacity content, memo, request key, payload/hash or idempotency result is selected or emitted. Operator output includes cross-unit synthetic internal IDs and quantities and must not be turned into an ordinary stock-reader API.

## Limits

- Structural agreement is not proof that the source facts are historically correct or complete. Entirely missing transfers and internally consistent fabricated facts can pass.
- This is separate from quantity reconciliation, journal balancing, valuation and financial reconciliation. It does not verify monetary amounts, cost layers or exchange rates.
- It does not recompute historical permissions from current grants or location memberships; these may change after posting. No new transfer rules, negative-stock policy or retroactive authorization policy is inferred.
- Existing PostgreSQL constraints/triggers prevent several malformed references from being inserted at all. Tests retain these protections: they prove database rejection and exercise the defensive comparator with in-memory copies for unpersistable link corruption. Missing/duplicate/quantity-mismatched pairs and isolated mutable-bucket drift are tested in PostgreSQL without disabling constraints or editing immutable movements/transfers.
- A real separate-connection regression commits a complete transfer between the header and movement reads. The first snapshot sees the old complete generation; a fresh snapshot sees the new complete generation, without a false discrepancy.
- Current implementation materializes the selected facts and discrepancies in memory. No large-history throughput, memory bound, schedule or SLA is claimed.
- Synthetic flags and the local-demo guard do not establish the provenance of mislabeled content. Only synthetic fixtures are authorized.

Exact executed evidence and private-preview recovery details are in `REVIEW_HANDOFF.md`. No HTTP endpoint, dashboard panel, network notification or sidebar navigation change was added.
