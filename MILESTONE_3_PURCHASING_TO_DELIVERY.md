# Milestone 3 — synthetic purchasing-to-delivery services

## Scope and safety

This is a services/API and transaction-workbench prototype for synthetic data only. Every record, journal, payment reference, notification, and accounting description created by this workflow is marked as test-only. Nothing in this document is an approved Tivoli commercial or accounting policy.

Purchasing and quote services fail closed unless `APP_ENV=demo`, the acting user has a synthetic demo marker, the user has an active `create_order` grant for the selected business unit, and the user has an active approval-role assignment for that unit. Staff configuration routes additionally require a synthetic Django staff account. Session-authenticated write routes retain the existing CSRF requirement.

No new users, `create_order` grants, approval-role assignments, approved selling prices, counterparties, or special-order value bands are seeded. The demo seed creates only the configurable work calendar (Friday closed, Saturday/Sunday working) and an empty storage-exception policy. This keeps the existing demo accounts read-only unless an authorized operator separately configures the required access and data.

The simulated-payment service does not connect to a payment provider. It records synthetic payment events only. Existing Milestone 2 services post synthetic stock-receipt and delivery journals in QAR; quote payments and storage charges do not create cash/receivable journals.

## Implemented flow

- Retail & Sales now opens the same authorized sales forms/registers in a focused retail mode, replacing its placeholder. Existing stock/special-order quotes, snapshots, discount/hold/collection/reservation controls and delivery-progress reads are reused, with a link to Delivery & After-Sales. No new endpoint, role assignment, band, pricing/payment rule or financial posting is introduced; missing configuration remains unavailable. See `RETAIL_SALES_WORKBENCH.md`.

- Delivery & After-Sales now has an operational entry point using the existing authorized quote/actions/registers and non-posting return intake rather than a planned-page placeholder. A read-only quote delivery-progress panel shows exact stored per-line quantities and actual readiness/handover timestamps; it does not authorize or implement partial delivery. Procurement and quote creation remain in Purchasing. See `DELIVERY_AFTER_SALES_WORKBENCH.md` for the acceptance mapping and policy-dependent boundaries.

- Staff can assign or revoke an approval role for a synthetic user who already has an active `create_order` grant for that unit; configure an approved synthetic selling price, the unit work calendar, and storage-exception approver roles; and create synthetic supplier/customer records.
- A synthetic purchase receipt accepts imported variants and receiving locations. Shipping, clearance, and other eligible charges are allocated by supplier purchase value through the existing receipt service. The persisted rate is eligible charges divided by total supplier purchase value.
- An authorized user can create stock or special-order quotes with variant specifications. Each quote line freezes the then-current approved selling-price revision. Discounts are calculated from that snapshot and append an approval record; source list prices are never changed by a quote.
- Per-unit discount ceilings are salesperson 10%, manager 20%, commercial GM 30%, and CEO exceptional 50%. A quote over its creator's ceiling waits for an authorized approver.
- Special orders require an explicit per-order payment-plan approval and reason: 100% or 40%–70% downpayment. There are no seeded special-order value bands. Draft bands can be configured and explicitly CEO-approved, but have no workflow effect until their business purpose is approved.
- Temporary holds are limited to one through three configured working days. The seeded calendar closes Friday only; Saturday and Sunday count as working days. A hold does not reserve stock.
- Stock reservation requires at least 50% of quote value to have been recorded as non-returnable simulated payment. A 40% special-order downpayment alone never reserves in-stock goods. Once the threshold is met, available received stock is reserved atomically.
- Stock and special-order delivery normally require 100% of the quote value paid before handover. A BU GM or CEO may explicitly approve a 5%–10% remaining balance with a reason; only the approved remainder can be recorded after handover.
- A readiness notification is retained as an append-only event. The first storage charge is created at the one-calendar-month anniversary of readiness, followed by a 1% charge on the quote-value snapshot at each monthly anniversary. Each charge has an audit notification. Storage exceptions are disabled until staff configure permitted approver roles and the approver records a reason.
- Posted journals, stock movements, receipt snapshots, payment records, quote approvals, storage charges, and workflow audit events are append-only/immutable. Operations use atomic transactions and idempotency keys; key reuse with a different payload is rejected.

## API surface

- Governance configuration: `/api/governance/purchasing/...`
- Synthetic receipt: `POST /api/purchasing/receipts/`
- Quote create/read: `POST /api/purchasing/quotes/`, `GET /api/purchasing/quotes/<id>/`
- Quote actions: `/discount-approval/`, `/holds/`, `/payments/`, `/post-handover-approval/`, `/ready/`, `/deliver/`, `/storage-accrual/`
- Storage exception: `POST /api/purchasing/storage-charges/<id>/exception/`
- All transaction writes accept an `Idempotency-Key` header. Decimal inputs should be sent as strings.

## Test-only assumptions and unresolved policy

- This prototype uses the supplied discount ceilings, Friday-only closed calendar, 50% non-returnable reservation threshold, stated special-order payment ranges, and 5%–10% approved post-handover range.
- The charge is recorded on the first date after the one-month free period and then on monthly anniversaries (a test-only timing convention). Whether storage is billed in advance or arrears remains unapproved.
- Special-order value-band thresholds are not populated, and their intended effect is not implemented. Approval purposes/thresholds must be agreed before those bands affect routing.
- Storage-exception approver roles default empty; staff must explicitly configure them. Whether an outstanding storage charge blocks delivery is not defined; this prototype records it separately and does not use it as a delivery gate.
- Approved selling prices, `create_order` grants, role assignments, and synthetic counterparties are not seeded. Until those are configured, the transactional API remains inaccessible or returns a clear missing-configuration error.
- Supplier payables, payment reconciliation, taxes, refunds, partial delivery/cancellation, quote expiry, inventory valuation policy beyond the existing test-only Milestone 2 journals, and production accounting/approval rules remain outside scope.
- The original increment was backend/API only. Subsequent authorized increments now provide a transaction workbench, scoped paginated read registers, multi-line commitments/partial receipts, ledger-backed Inventory, and a rendered post-delivery balance/retry regression. No configuration or access is inferred when required grants, approvers, prices or counterparties are absent. See the current acceptance mapping and evidence in `REVIEW_HANDOFF.md`.

## Verification

Results from the current implementation are recorded in `REVIEW_HANDOFF.md` after the final full-suite run.

## Reviewed packing-list receipt preparation

The existing local CSV/XLSX preview supports direct-receipt preparation and explicit existing-commitment receipt preparation inside the workbench. Direct lines require an explicit authorized variant choice; commitment preparation requires an explicit commitment and one exact authorized commitment line per source row, with ordered/received/remaining quantities displayed. Both require an existing receiving location, separately confirmed quantity and source-unit review. Missing/ambiguous/denied matches, duplicate commitment mappings and quantities exceeding remaining block handoff. Reviewing only copies local draft lines. Direct receipts still require supplier/cost inputs; committed receipts fix the supplier and reuse immutable committed values without displaying costs. An explicit final submission is required through the unchanged receipt workflow. Source/mapping/context/progress changes require fresh review. No upload, review or unconfirmed selection creates any record.

For direct preparation, `GET /api/stock-receipt-candidates/` reuses the existing selected-BU, active `read_stock`, legal-entity and location-membership ledger scope and returns only variant/item/brand display metadata. Commitment preparation instead reuses the existing authorized, paginated `GET /api/purchasing/commitments/` progress; it does not require or expand a ledger catalog match. Receipt posting still requires all existing purchasing permissions/configuration and validates exact commitment/line/variant/supplier/location references server-side. No commitment or supplier is inferred from a file, and no global catalog access is added. Source units and warehouse measurements are local only because the current receipt API has no corresponding fields. See `PACKING_LIST_RECEIPT_PREPARATION.md` for steps and limitations.
