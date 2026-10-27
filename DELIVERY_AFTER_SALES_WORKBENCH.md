# Synthetic Delivery & After-Sales operations

## Approved independent increment

The existing **Delivery & After-Sales** navigation destination now opens the
existing authenticated workflow instead of a planned-section placeholder.
It reuses Purchasing Workbench state/request ownership, transaction controls,
scoped registers and the non-posting sales-return intake. Purchasing's existing
destination remains available and retains its full procurement/quote workflow.

Delivery mode focuses on:

- Selecting an already-authorized quote/order and reviewing its existing facts.
- Existing simulated quote payments, readiness, explicitly approved deferred
  balance terms, whole-quote delivery/handover and storage actions, each subject
  to the unchanged capability/configuration/state checks and authoritative API.
- Quotes, storage charges, reservations and delivered-sales registers using
  their existing distinct server capabilities/scopes. A quote capability is not
  a report capability; a denied register remains denied.
- Existing synthetic return intake/inspection, with the configured 14-calendar-
  day delivery-based window, good-condition review, showroom/collection details
  and Finance-pending state. No refund, credit or stock reversal is added.

Procurement, receipt/commitment and quote-creation forms are not part of this
entry point. Related procurement/directory GETs are not made just to render it.
Staff-only configuration needed by the existing approval/action gates remains
staff-only; missing or denied configuration never becomes implicit permission.
No staff assignment, grant, price, customer or other demo record is added.

## Read-only delivery progress

`GET /api/purchasing/quotes/<id>/?business_unit_id=<selected-unit>` retains its
existing authentication, synthetic-actor, active `create_order`, role and
selected-unit checks. Its detail response additionally includes:

```text
delivery_progress:
  ready_at: existing quote readiness timestamp or null
  delivered_at: existing quote handover timestamp or null
  lines: existing quote-line PK order
    quote_line_id
    variant_id
    ordered_quantity: exact decimal string
    reserved_to_date_quantity: exact stored quote-line reservation quantity
    delivered_quantity: exact stored quote-line delivered quantity
    remaining_quantity: exact ordered minus delivered decimal string
```

These are stored workflow facts, **not an available-to-promise calculation or
permission to deliver a partial quantity**. Reservation coverage is historical
quote-line state, not live reserved inventory; it can remain nonzero after the
reservation has been consumed by delivery. No stock costs, values, supplier
data or transfer history is added.

The UI displays the API's exact strings and timestamps without floating-point
recalculation or inferring a date policy. Existing context invalidation prevents
another BU's quote/progress from remaining visible. Write responses, idempotency
payloads/keys, audit events and posting services are unchanged.

## Implemented acceptance versus decisions still needed

| Requirement | Current evidence/boundary |
| --- | --- |
| Imported goods/landed cost into stock | Existing purchasing services, partial-receipt/commitment APIs and tests; unchanged. |
| Authorized quote to readiness to handover | Existing services now reachable in the Delivery section; PostgreSQL session/CSRF and rendered regressions exercise the existing operations. |
| Payment gate and approved late balance | Existing full-payment/default or explicitly approved deferred-balance gates; delivered-state-preserving later collections and retry identity remain intact. |
| Quantity reconciliation visible to operator | Read-only per-line ordered/reservation-coverage/delivered/remaining strings and actual readiness/handover timestamps. No partial posting added. |
| Separate BU and register capability checks | Existing quote/read APIs; the UI never treats navigation/favorites as authorization. |
| Returns | Existing delivery-based eligibility and non-posting intake/inspection with pending Finance review; unchanged. |
| Partial delivery/posting | **Not implemented:** current readiness requires every quoted unit reserved and delivery consumes the whole active quote reservation set. |
| Transport scheduling/picking proof/signature | No approved data/state/access contract; no invented dispatch or warehouse-role workflow. |

Partial delivery needs owner-approved semantics for which lines/quantities may
be handed over, payment/approved deferred-balance allocation, quote completion/
timestamp treatment across handovers, storage cessation/charge allocation and
delivery-date selection for returned items. Existing aggregate read progress
does not resolve these decisions.

Financial return approval authority, exceptions, failed-inspection disposition,
refund execution/credit notes and stock/valuation/accounting reversals remain
pending explicit Finance/operations decisions. No account mappings, currency,
valuation or production accounting policy is introduced by this increment.

See `REVIEW_HANDOFF.md` for exact fresh versus historical checks and browser/
runtime limitations. All tests use isolated synthetic fixtures or in-memory
mock APIs; no shared/production transaction or permission assignment is made.

## Picking preparation — local review only

The focused child `#/delivery/preparation` (Favorite `delivery.preparation`)
combines each authorized quote line's variant reference/specifications with
its exact ordered, delivered and remaining quantity and existing readiness
timestamp. Review marks are browser-local, explicitly **reviewed only**, and
reset on quote/BU changes, reload and a fresh lookup. They are not physical
picking confirmation, allocation, stock movement or dispatch.

`GET /api/purchasing/quotes/<id>/preparation/?business_unit_id=<id>&unit=<id>`
uses the unchanged quote/demo/active-order-grant/approval-role checks.
Optional item, SKU, brand and location identities require a separate active
`read_stock` grant and the existing legal-entity/BU/location ledger scope.
Only the exact quote-line variant/location bucket may match. Missing stock
access, special-order location or authorized bucket returns no identity;
organization location choices never substitute for stock authorization.
No static catalog fallback, hidden ancestor, cost, capacity or stock-history
field is introduced. Historical reservation coverage is not live availability.

The link to existing readiness/handover retains the selected quote and original
action gates. Reading/checking creates no business records. Only an explicit
Favorite action writes a personal navigation preference. New choice-only
migration `0021_delivery_preparation_favorite` is required for that pin;
the subsequent controlled rollout applied it to the verified shared synthetic
demo on 2026-10-20, preserving all existing records/grants after backup/restore
proof. No users, assignments or transactions were added. The normal preview
serves the current function, but the shared demo's zero quotes/approval roles
mean empty/unavailable states, not a populated acceptance journey. See the latest handoff for
isolated backend/browser evidence and [team steps](RETAIL_TEAM_ACCEPTANCE.md#delivery-picking-preparation--review-only).
