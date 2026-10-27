# Tivoli ERP — Technical foundation

16 October 2026. Engineering selection for the first prototype; production sizing, hosting and business accounting policies remain pending. No application has been implemented or deployed by this document.

## Stack decision

Use Python with Django for the business application, Django REST Framework for APIs, PostgreSQL for transactional data, and React with TypeScript for the user interface. Start with a supported Django long-term-support branch (5.2 LTS is the initial candidate); resolve compatible supported package versions and pin them when implementation starts. Do not select an unreleased branch or automatically track latest packages.

This is an independent custom ERP. These are general-purpose components, not an ERP subscription. Tivoli's accounting and stock behavior still must be designed and tested. Framework security defaults do not implement business-unit isolation automatically.

Use one modular backend and a single primary database initially. Put shared calculations and posting rules in backend services called by every interface. Keep the web interface and API under the same site where practical, with secure authenticated sessions and CSRF protection. External integrations use separately scoped credentials. Confirm Microsoft identity integration against Tivoli's actual tenant before enabling it; no credentials are needed for the synthetic local proof.

Private file storage holds attachments; metadata and access scope stay in PostgreSQL. Use a storage interface so local development and production object storage are interchangeable. Source and migrations are held in Git under company control; remote repository provider is pending. Reproducible containers are a packaging option, not a substitute for backup, service management or security.

Use a durable database outbox for asynchronous notifications/integration events. A worker processes these after transaction commit with retry, deduplication and visible failures. Add a dedicated queue/cache only when justified; neither holds authoritative stock or money balances. Production monitoring and backup tooling are selected with hosting. AI monitoring is a later consumer of approved telemetry, not an authority over ledger values.

## Initial records and relationships

| Group | Principal records | Design rules |
|---|---|---|
| Organization | LegalEntity, BusinessUnit, ProductBrand, Location, WorkingCalendar | One current legal entity; units and locations remain separate; location tree prevents cycles |
| Identity | User, Role, ScopedGrant, Delegation | Role + unit + action grants; effective dates; revocation checked on execution |
| Governance | PolicyVersion, ApprovalRequest, ApprovalDecision, AuditEvent | Version rules; retain approver, reason and evaluated values; prevent self-approval where required |
| Trading parties | Party, CustomerAccount, SupplierAccount, Address | Stable identifiers and explicit permitted visibility; do not expose all parties by default |
| Catalog | Item, Variant, SpecificationRevision, UnitOfMeasure, Barcode | Brand prefixes do not grant access; barcode identifier differs from mutable location; retain historical specifications |
| Procurement | PurchaseOrder/Line, Shipment, Receipt/Line, SupplierInvoice/Line, LandedCostAllocation | Trace quantities and charges to source lines; no duplicated receipt from retry |
| Inventory | StockMovement/Line, StockBalance, Reservation, Transfer, ValuationEntry | Movement ledger authoritative; balance projection updated transactionally and reconciled |
| Retail | Quote/Line, SalesOrder/Line, PriceDecision, PaymentSchedule, Delivery/Line, Return | Explicit approval and fulfillment state; retain accepted prices/specifications |
| Finance | Account, AccountingPeriod, Journal/Line, Payment, Allocation, ExchangeRate | Company and unit dimensions; decimal amounts; balanced posted journals; immutable posted lines |
| Integration | IdempotencyRecord, OutboxEvent, ExternalReference, FileRecord | Unique request key plus payload hash; same key/different payload is an error; private attachment access |

HR has its own access boundary and will extend shared users, company, grade/policy and accounting references. Payroll and employee schemas are a required separate workstream, not an inferred subset of customer/party records. Factory and project budget detail follows the approved scope without deleting existing operational coverage.

## States and history

Separate commercial approval, financial posting, fulfillment and payment states; one generic status cannot accurately represent a partially delivered, partially paid order. Define permitted transitions and accountable actions for each document type.

Draft documents may be revised with history. Submitted/approved documents preserve accepted revisions. Posted journals and stock movements cannot be edited through ordinary application operations; corrections reference the original in reversal/adjustment records. Protect posted data with database constraints/triggers and restricted database roles where suitable. Database administrators still need controlled access and protected audit copies.

Retire used master records rather than deleting historical references. Use internal immutable identifiers; document numbers and brand-prefixed item codes remain business identifiers. Store UTC timestamps and apply Asia/Qatar and the working calendar explicitly for business deadlines.

## Atomic posting contract

A business command includes actor, scoped business unit, document revision and request identifier. Within a database transaction:

1. Validate current permission, document state, policy version and approvals.
2. Obtain required locks in deterministic order, including affected stock balance rows; enforce unique keys when creating missing balance rows.
3. Recheck availability, reserved quantities, monetary precision and period status under the lock.
4. Produce all applicable movements and balanced journal lines through the posting service.
5. Update balance projections, document state, request result and business audit; insert outbox events.
6. Commit everything together. External network calls run afterward, never as assumed atomic participants.

For a reservation, lock the item/variant-location stock bucket and update reservation and available balance together. Transfers lock both locations consistently. For serial-numbered goods, enforce unique active allocation of the physical unit. Deadlock/serialization retries are bounded and idempotent. Verify behavior using real PostgreSQL connections; SQLite tests cannot establish PostgreSQL concurrency correctness.

A deferred database validation or equivalent guarded posting mechanism must ensure posted journal balance, not just a screen check. Apply checks per legal entity and company currency, including explicit rounding entries. Record transaction-currency amounts and exchange-rate source. Keep sensitive values out of general logs.

## Example accuracy fixture — test policy only

Use a deliberately simple synthetic example: receive 10 chairs at QAR 1,000 each, allocate QAR 2,000 eligible landed charges, producing QAR 12,000 total value and QAR 1,200 per chair. Deliver two at QAR 2,000 each: expected revenue QAR 4,000, cost of goods sold QAR 2,400 and remaining inventory QAR 9,600, assuming this fixture's tax-free, single-cost-layer, delivery-recognition policy.

This is not Tivoli's approved accounting or tax policy. Finance must validate recognition timing, eligible costs, valuation method, foreign exchange, deposits and correction rules before real transactions. Tests then add mixed-value items to verify purchase-value allocation, rounding residuals, partial receipts and late costs after sales.

## Build sequence and exit criteria

A. Project skeleton, automated checks, local PostgreSQL, synthetic company/brands/locations and scoped access. Exit: permission tests pass on list/detail/export and direct API routes.

B. Posting services, journal/stock ledgers, reservations and idempotency. Exit: balanced fixture results, concurrency tests, rollback tests and duplicate prevention pass.

C. A thin purchasing-to-delivery interface and approval trail. Exit: staff can execute the scenario, inspect evidence and reconcile outputs without direct database edits.

D. Backup/restore and measured workload trial. Exit: recovered data and documents match, defined response-time/load targets are met, and unresolved limitations are recorded.

The full acceptance scenarios in Tivoli_ERP_Custom_Architecture_and_Proof_v0.1.md govern these increments. None of these increments constitutes completion of all seven Phase 1 modules or permission to retire Dynamics.

## References checked 16 October 2026

- [Django supported versions](https://www.djangoproject.com/download/): choose a supported release and maintain a planned upgrade path.
- [Django REST Framework permissions](https://www.django-rest-framework.org/api-guide/permissions/): object checks alone do not automatically scope list results or creation; implement and test all entry routes.
- [PostgreSQL locking](https://www.postgresql.org/docs/17/explicit-locking.html): row locks support serialization of conflicting updates within a transaction.
- [PostgreSQL transaction isolation](https://www.postgresql.org/docs/16/transaction-iso.html): concurrency behavior and retry handling must match the selected isolation strategy.

Documentation versions above are explanatory references, not a pinned database deployment version. Select the supported PostgreSQL major and package versions during environment setup.

## Synthetic Inventory packing-list preview addendum — 18 October 2026

The Inventory prototype includes a preview-only importer for synthetic supplier packing lists. CSV and `.xlsx` files are parsed in the browser; users select a worksheet and explicitly map source columns rather than relying on a supplier template. The file, parsed rows, and warehouse-entered measurements remain in memory only. The preview does not create receipts or commitments, update stock, or save measurements or attachments.

Only mapped item codes are sent to the same-origin, read-only match endpoint. It requires the user's existing active `read_stock` grant and resolves codes only within that user's selected business unit, legal entity, and authorized location membership. Matching is limited to item codes and variant SKUs present in the authorized transactional `StockBalance` projection; `StockExample` is not used. This is not a complete business-unit item-master relationship, so absence of a ledger bucket is reported as “no authorized inventory match,” not proof that a master item is invalid.

Supplier cubic volume is shown and totaled only as mapped m³; supplier area is shown and totaled separately only as mapped m². Package dimensions remain source text. Warehouse-confirmed volume is entered separately in m³ for each row and exists only in the current browser view. No area/volume conversion or derivation is performed. Rows with missing or invalid item codes, quantities, units, or mapped measurements are visibly flagged.

The browser parser accepts `.csv` and `.xlsx` only (not legacy `.xls`), caps files at 10 MiB, expanded workbooks at 24 MiB, data at 10,000 rows, and mapped worksheet columns at 256. Formula cells are not evaluated. These are technical preview limits, not supplier-template, warehouse, storage-capacity, or production policies.
