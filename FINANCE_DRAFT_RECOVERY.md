# Isolated Finance review and create recovery

Editor-loading consolidation is now implemented. Common recovery module/panel
remains outside selected-editor loading; failed imports do not change metadata
or automatically retry financial requests. Status is restored only after a
working view verifies current synthetic session. Native failed-module/reload
tests preserve exact stored UUID/resource/fingerprint and use actual authorized
status lookup; actual UI commit/lost-ack/replay journeys also rerun.
Browser-cached module failure may require explicit page reload, retaining
available sessionStorage metadata/reopen query—not unsaved inputs or original
in-memory payload. Existing guarantees/limits below unchanged.
See [measurement/acceptance](FINANCE_EDITOR_LOADING.md) and latest handoff.

## Availability and boundary

Implemented only in the verified disposable Finance lab. Not operational
Finance, ordinary password authentication, approved shared-user access or
team acceptance. `Start application` / shared-preview recovery is separate.
No shared migration, grant, account, credential or record changes.

Use the existing operator-owned disposable facility:

```sh
FINANCE_LAB_SKIP_BACKEND=1 FINANCE_LAB_REVIEW=1 \
  bash scripts/verify-finance-draft-workflow.sh
```

This verifies an owned temporary PostgreSQL16.15 cluster, creates minimal
synthetic fixtures and serves loopback18069. The existing explicit synthetic
test-session button has no password and is not normal Finance sign-in.
It creates no shared credentials/assignments. Exit deletes this facility.
Loopback is not an owner-accessible published/shared URL; no shared Finance
setup is available through this batch. Do not adapt these commands to a
shared or production database.

## Cohesive review/reopen

Open the synthetic session at `/lab-ui/`, then **Review index**
(`/lab-review-ui/`). Its13 existing endpoint families cover source-linked
reversal review and journal,
recurring, opening-balance and accrual/prepayment kinds plus allocation,
budget, reconciliation, expense/petty cash, assets, forecasts, AP invoice/
credit/debit/advance and AR invoice/credit proposals.

The index lists current labels, IDs, kinds, revisions and observation
timestamps from one read-only repeatable-read PostgreSQL snapshot. Counts
are document counts, never financial totals or actual balances. Open an
existing family editor or **Reopen** a saved item. `?reopen=<positive-id>`
loads through the original editor's authorized GET; it does not create,
copy, convert or post a draft. Invalid/unavailable/403 links remain clear.
Current kind/revision/input validation and conflicts remain in those editors.
Common navigation retains their existing dirty/pending unload warnings.
Internal labels English; stored Arabic and bilingual customer documents
unchanged.

## Create identity contract

Implemented by [guard integration](foundation/finance_lab_views.py),
[recovery service](finance_allocation_lab/recovery.py),
[lab-only receipt](finance_allocation_lab/models.py),
[transport](src/finance-draft-lab-api.ts) and
[recovery metadata](src/finance-lab-recovery.ts).

- Updated actual lab UI sends `X-Finance-Request-ID` UUID for each new draft.
  Same unresolved canonical request reuses its ID; financial values and raw
  strings are not silently normalized into an equivalent different payload.
  Object key ordering alone is irrelevant; array order/type/value changes
  are not. Changed input/new draft type is a different request.
- Durable uniqueness: **actor + entity + request UUID**. Resource and SHA256
  canonical request fingerprint are bound on first committed successful create.
  Conflicting resource/payload reuse returns409 without changing the outcome.
- Unique insertion/row locking serializes independent PostgreSQL attempts.
  Draft, initial history and successful receipt/original201 JSON commit
  atomically. Failed validation/receipt storage rolls all back. No worker,
  schedule or posting is introduced.
- An identical replay returns the original201 result, with
  `X-Finance-Replayed: true`, not a fresh draft or its latest edited revision.
  Revisioned PUT is unchanged; reopen uses current authorized detail and
  expected-revision validation. Old acknowledgements cannot overwrite a
  newer persisted edit.
- GET `/lab/requests/<uuid>/` rechecks the existing guard and scopes to current
  actor/entity. Only safe metadata: request ID, saved state, resource/editor,
  draft ID and original revision. No payload, amount, counterparty or history
  disclosed by lookup. Another actor/entity sees the same generic404 as an
  absent key; unsigned/inactive/staff or otherwise invalid lab actor403.
  Existing entity-scoped draft visibility is unchanged—not new per-user
  ownership policy for all Finance drafts.
- **404 does not prove no create committed or will commit.** It may still
  be in flight. Retry only original input with the original identity, not
  a replacement. There are no automatic POST retries.

## Reviewer recovery

After an unreadable/5xx/missing acknowledgement, retain the local draft.
**Requests needing attention** offers **Check request status** and, while
this tab still holds the exact original encoded input, **Retry original
request**. The latter is a POST replay, not just a read-only check.

If saved, **Open saved draft** loads its current revision. This clears the
attention marker only after a successful authorized detail read. Explicit
navigation still respects the existing unsaved-work confirmation.

Only scope-bound request metadata (ID/resource/fingerprint and, once
acknowledged, draft ID) is stored in sessionStorage—never proposal payloads.
Scope derives from verified disposable-cluster identity/actor/entity.
Full-page navigation/reload can restore status checking, not the lost form
payload or an executable retry. If the input is no longer held, retry is
disabled; inspect status/reopen, or restore the exact original input in its
editor so the same identity can be reused. 403/scope changes clear visible
recovery state and suppress stale outcomes. No request-list endpoint exposes
other actors' receipts.

## Tested guarantees and limits

Latest reversal extension: same protocol for explicit source-linked reviews,
13 families,22 focused/173 relevant backend,125 affected frontend/helper,
2 actual authenticated synthetic reversal browser journeys,349 contrast pairs.
Source read-only historical revision and current proposed revision remain
distinct from the original create acknowledgement. See
[readiness](FINANCE_READINESS_MATRIX.md) and latest [handoff](REVIEW_HANDOFF.md).

Earlier consolidation-only evidence, retained rather than relabeled as new:
9 focused/160 full relevant Finance PostgreSQL tests,119 affected frontend/
helper tests,2 authenticated synthetic real-backend browser journeys,
349 contrast checks, typecheck/build/schema/system/migration checks passed.

Within the retained disposable database and supplied actor/entity/request
identity, tests show one successful create/initial revision/receipt under
identical independent concurrent calls and after committed-but-lost
acknowledgements. Conflicting payloads cannot replace it. This is **not
universal exactly-once delivery**: no guarantee across forgotten/new IDs,
cleared/unavailable browser metadata, direct legacy clients omitting the
header, deleted/restored databases, disaster recovery or permanent storage.
No original payload survives tab closure in browser storage. Browser fault
injection withholds a real committed201 at the loopback response proxy;
not a real infrastructure outage or production certification.

Lab-only migration0012 adds one receipt table, actor/entity foreign keys,
unique identity and synthetic constraint, no default model permissions.
Normal/shared migration graph excludes it. No new financial worksheet or
policy. Existing original JSON results are protected in the receipt, not
exposed by the metadata status endpoint. No new retention/expiry policy
invented; receipts live for the disposable database lifetime.
