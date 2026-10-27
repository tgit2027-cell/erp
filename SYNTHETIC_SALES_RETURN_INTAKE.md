# Synthetic non-posting sales-return intake

This is a review record, not a refund, credit note, restocking instruction or
accounting document. All data and fixture transactions are synthetic only.

## Owner-confirmed delivery-date rule

The owner described good-condition returns within 14 days, usually refunded by
the original cash/card payment method, with items received at a Tivoli showroom
or collected by the warehouse for large items.

**The owner confirmed: "Confirmed; count from delivery."** Enforce the configurable
default of 14 calendar days from actual delivery as a company rule. It is not a
verified statement of Qatar law; legal validation remains pending.
`SalesReturnPolicy.provisional_calendar_days` is a persisted per-BU setting,
defaulting to 14 when no setting exists. GETs never create a setting.
The date basis is explicitly **delivery**, including a database constraint on the
configuration. The actual sale `delivered_at` is converted to its calendar date in
the existing configured Django timezone (currently Asia/Qatar). A request must
satisfy **delivery date ≤ request date ≤ delivery date + configured calendar days**.
The deadline is inclusive: default day +14 passes, +15 fails; a request before
delivery also fails. No invoice/payment date or working-day calendar is used.

The server previews the assessment for the selected request date and validates
it again on POST. Accepted intakes store delivery date, derived deadline, request
date, day count and date-window eligibility in `delivery_window`, alongside their
policy snapshot. A failed window returns HTTP 400 with the delivery/deadline
message and persists no intake, event or idempotency row.

There is no new policy editor or new policy-management authority. Operator
configuration can persist the day count; exceptions, exclusions and financial
approval still require separately approved rules.

## User flow

In **Purchasing Workbench**, expand **Sales-return intake — synthetic,
non-posting**. The register is fetched only after opening; collapsing retains the
mounted draft. Existing demo baseline users are not given transaction grants or
role assignments to enable it.

1. Enter an existing authorized delivered quote ID. Customer, brand and item
   identity come from the originating sale, not a new customer directory.
2. Select returned lines and quantities, the request date, explicit receiving
   mode and an existing BU-associated location. Enter receipt/collection details.
   Location type, vehicle, collection schedule and physical receipt are not
   inferred from the selected location.
3. Record a proposed QAR refund amount, user-recorded original payment method and
   the customer's preference: usually the original method, cash or card.
   The requested method follows that explicit preference. Existing simulated
   payments have no method field, so the original method is labeled
   **user-recorded, not verified**.
4. Review actual delivery, inclusive deadline and server-derived window eligibility;
   save an in-window intake as `awaiting_inspection`. Record `good` or `not_good` and notes
   for every returned line to move to `finance_pending`.
5. Finance approval/review remains pending. A not-good finding records only an
   observation: no exclusion, exception, disposal or other disposition is inferred.

There is no Finance approval endpoint or enabled refund/restock action. No
authority to approve Finance decisions is inferred from Purchasing roles, staff
or superuser status. No stock is put back even after inspection.

## API and authorization

- `GET /api/purchasing/returns/?business_unit_id=...&page=...&page_size=...`
- `POST /api/purchasing/returns/`
- `GET /api/purchasing/returns/sales/<quote_id>/?business_unit_id=...&request_date=YYYY-MM-DD`
- `POST /api/purchasing/returns/<intake_id>/inspection/`

Every operation requires existing session authentication, `APP_ENV=demo`,
synthetic user/records, active BU `create_order` and the existing active Purchasing
approval-role assignment. Staff alone is insufficient. Selected BU is required;
source sales and return records cannot be looked up across another BU. Receiving
location must belong to the existing BU/legal entity and still be associated when
reading/inspecting. No new role, grant, capacity permission or report capability
is created. Responses use display metadata, returned quantities and intake facts;
no supplier cost, landed cost, stock valuation or movement history is exposed.

POSTs use CSRF and `Idempotency-Key`. Same key/payload returns the original result;
changed payload conflicts. Authorization is rechecked before replay. Inspection
locks the intake, and intake/inspection facts, append-only audit events and
idempotency completion commit together. Failure rolls them back together.
Database constraints limit states to the two non-posting states; a PostgreSQL
trigger rejects updates/deletes of return audit events.

Intake quantities cannot exceed their originating sale line. Distinct requests
are proposals only, not reserved refund entitlements; cumulative return/refund
eligibility and duplicate-case resolution remain unapproved.

## Evidence and limits

See the final sales-return section in `REVIEW_HANDOFF.md` for exact commands:
27 return PostgreSQL tests within 63 focused / 239 full backend tests, 11 rendered
return tests within 193 full frontend tests, and 105 contrast checks passed.

These tests use disposable PostgreSQL and mocked HTTP. The sign-in page was
visually checked after migration. Five additional isolated Chromium tests now
verify return-form native keyboard operation, accessible names and alert/status
roles, visible focus, explicit mock retry identity, and desktop/390px/320px
layouts without document horizontal overflow. The final browser run passed all
11 tests (5 return + 6 existing sidebar), alongside 193 frontend tests and 105
contrast checks. Screenshots were inspected, but real-session authentication,
spoken screen-reader output and live-backend browser interaction remain unverified.
Independent-connection return-inspection concurrency, identical/conflicting
idempotent retries and post-intake grant/role revocation are verified by six
PostgreSQL API tests. Two additional independent-connection API tests now prove
concurrent intake creation with the same key and payload records one intake/audit
outcome, returns the original result on retries, and rejects conflicting payloads
with 409. Actual PostgreSQL lock waits are observed; business-state snapshots
prove no financial or inventory posting. Separate keys are not treated as the
same operation, and cumulative return eligibility is not defined by these tests.
Production returns,
financial approval authorities, legal validation, disbursement,
credit notes, disposition, stock re-entry, tax and valuation remain outside this
intake-only implementation.

Migration `0018` changes existing policy configuration to delivery and assesses
historical intakes from actual delivery using each captured day count. It does
not rewrite historical policy snapshots, workflow status, audit events or
idempotency results. Historical out-of-window records remain visible, cannot
advance inspection, and gain no exception or disposition. Missing actual delivery
fails closed as unavailable. Exact replays preserve their original completed
outcome even if the configurable day count later changes; new requests use the
current configuration. All current authorization checks still precede replay.
