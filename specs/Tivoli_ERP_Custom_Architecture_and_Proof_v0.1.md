# Tivoli ERP — Custom architecture and proof-of-accuracy specification

## Owner-approved Retail quotation addendum — 2026-10-20

- Authorized ordinary nonstaff sales may select existing items/customers,
  independently permitted quantities and fixed approved selling prices, and
  apply only their existing grade's discount ceiling. Selecting a quotation
  choice is not permission to create an item, alter prices, administer users
  or access purchasing/supplier/cost/history/margin data or exports.
- Reuse multi-line stock/special-order quotation capabilities within existing
  single-kind rules, price snapshots, approval/payment gates and idempotency.
  No mixed-kind commercial behavior, value bands or real assignments are added.
- Client presentation includes item name, category, description, image,
  quantity, selling price, discount/total, client/contact, salesperson,
  department/BU/date and explicitly supplied validity, display payment terms
  and delivery wording. English/Arabic text and titles use appropriate
  direction; missing company translations/images remain visibly missing.
- Validity, delivery promises, payment defaults and legal terms are not
  invented. Terms and Conditions await owner text. Display-only notes never
  override actual payment, approval, reservation or delivery rules.
- Synthetic-only evidence, exact checks and limits are in `REVIEW_HANDOFF.md`;
  operational usage is in `RETAIL_TEAM_ACCEPTANCE.md`. Optional metadata
  migration 0022 is not applied to shared demo data by this implementation.
  Temporary-hold stock blocking and procurement-access policy remain pending.

## Original architecture scope

16 October 2026. Status: independent custom ERP direction approved by the user; architecture and acceptance design proposed. No application or production infrastructure has been built by this document.

## 1. Foundation decision

Build an independent Tivoli ERP. Use ERPNext and other established systems to study workflows, compare behavior and identify missing requirements. They are references, not the selected runtime foundation. Do not copy ERP source into the product without explicit dependency and licence assessment. Maintain a record of third-party components and licences; independent development still uses appropriate libraries and infrastructure.

Tivoli controls its source repository, data, deployment accounts, administrator access and documentation. The initial delivery model remains the user and assistant with AI development tools. Assign accountable production operations and business acceptance before go-live. No fixed delivery date or zero-defect guarantee follows from the architecture decision.

## 2. System shape

Start with one modular server application and one transactional relational database. This keeps related stock and financial changes within a controlled transaction boundary and limits operational complexity. Separate modules in code and ownership; separate services only when a measured need justifies the added failure modes.

Desktop, scanner-assisted warehouse screens and employee/mobile interfaces use authenticated server APIs. Shared rules apply regardless of entry route. Files live in private object storage; the database holds their identifiers, access scope and audit references. Background workers handle document extraction, notifications and integrations with retry and duplicate protection. Database engine, framework, provider and region remain to be selected through a separate technical decision.

The user permits production and encrypted recovery copies overseas. Hosting selection follows a compatible deployment design and cost/recovery assessment. Development, test and production remain separate; initial proof uses synthetic data.

## 3. Modules and ownership boundaries

| Core module | Responsibility |
|---|---|
| Shared organization and records | Legal company, business units, product brands, locations, customers, suppliers, items/variants, currencies, units of measure and numbering |
| Retail and sales | Quotations, approved pricing/discounts, stock holds, deposits, orders and special specifications |
| Procurement and imports | Purchase orders, supplier commitments, shipments, receipts, supplier invoices and landed charges |
| Inventory and warehouses | Stock movement ledger, reservations, transfers, counts, package/location records and valuation |
| Delivery and after-sales | Delivery authorization, picking, handover, returns, service and storage charges |
| Finance | Chart of accounts, double-entry ledger, receivables/payables, payments, reconciliation, budgets and period controls |
| HR and payroll | Employee/grade records, attendance, leave, approved calculations, payroll review and financial posting |
| Governance | Identity, unit/action permissions, DOFA/TOS versions, approvals, audit and configuration history |

The approved seven Phase 1 business sections remain mandatory. Shared records support these sections without bringing the full later Products/Brands or CRM modules forward. Factory and fit-out PO/SO and budget capabilities must continue through core functions or explicit Dynamics coexistence. Later specialist modules extend shared identifiers and ledgers.

## 4. Organization and access

Model one legal entity with configurable retail business units: Tivoli, Roche Bobois and Natuzzi. Product brand, physical location and business unit are different fields. Add future legal entities without pretending today's showrooms are separate legal companies.

Every protected record has an explicit access scope. Enforce role, unit and action permissions in server operations, queries, search, reports, exports and file retrieval. Cross-brand stock visibility and cross-brand order creation are separate grants. A screen switch does not itself grant authority. Payroll and personnel information need specific grants even for users who can view consolidated business reports.

Policies have versions and effective dates. Transactions retain the policy version and approval evidence used. New configuration must not silently rewrite historical approved transactions.

## 5. Non-negotiable transaction rules

- Every posted financial journal balances in company currency using explicit precision and rounding rules. Foreign amounts and exchange rates retain provenance.
- Posted records are corrected through authorized reversals or adjustment documents, not silent overwrites. Closed-period corrections follow Finance-approved rules.
- Stock quantities change through traceable stock movements. Reservations do not create stock. Define available quantity consistently across all sales channels.
- Reservation checks and updates are atomic: concurrent requests cannot both obtain the same final unit. Negative-stock policy must be explicitly decided and enforced.
- Where a document creates both stock and financial records, commit them together or fail together. Background integrations cannot leave a false success state.
- Retried posting/payment-import requests cannot create duplicates. Record request identity and processing result.
- Landed cost and selling price remain distinct. Use decimal arithmetic for money and defined quantity precision.
- Purchase commitments, actual expenses and payments are separate concepts; prevent double-counting budget consumption.
- Business authorization is checked when executing the action, not only when opening a screen. Record who approved exceptions and why.
- Audit captures business actions and privileged changes; protected copies and access controls are needed because an application audit table alone cannot defeat a privileged attacker.

## 6. First executable proof

Build a bounded end-to-end cycle before broad interface development. This is a foundation milestone, not completion of Phase 1.

Synthetic scenario: purchase furniture in foreign currency, receive partially, allocate shipment expenses by purchase value, establish actual landed cost, set an authorized selling price, quote and reserve stock, collect a deposit, authorize the remaining payment, deliver, invoice and reconcile. Include a showroom transfer and three brand users with restricted access.

Finance must approve the exact posting timing, inventory valuation method, accounts, currency rules and expected journals before this proof becomes a production specification. Do not infer those policies solely from a reference ERP's defaults. Use an explicit example policy for synthetic tests until business policy is confirmed.

| Test | Required observable result |
|---|---|
| Purchase receipt and landed charges | Quantities and values match independently calculated expected results |
| Concurrent last-item reservation | Exactly one reservation succeeds; the other gets a clear unavailable result |
| Hold expiry across Friday | Expiry follows the configured working calendar and recorded start time |
| Deposit and delivery | Delivery is blocked until the approved payment condition or documented exception is satisfied |
| Discount approval | Unauthorized discounts fail through both screen and API routes |
| Repeat submission | Repeated identical request produces one business transaction |
| Failure during posting | No partial stock/journal transaction remains committed |
| Partial delivery and return | Remaining quantities, credits, stock and balances reconcile |
| Late landed cost after a sale | Adjustment follows the approved valuation/accounting policy and reconciles |
| Closed period | Unauthorized historical posting is refused; authorized correction is traceable |
| Brand isolation | Unauthorized search, report, file, export and direct API access is denied |
| Backup restoration | A clean environment recovers matching database records and linked documents |

Keep expected results, executed test evidence and unresolved gaps in version control. Use property/invariant tests for balancing, quantities, rounding and duplicates alongside business scenarios. A demonstration with correct-looking screens is insufficient.

## 7. AI operations assistant

Add after basic application monitoring and access controls exist. Initial permissions: read sanitized operational telemetry and approved technical metadata; prepare fixes on a separate development branch. No unrestricted employee/customer payloads in prompts. Logs and imported documents are untrusted input, not instructions to the agent.

Monitor failed jobs, backup freshness, dependency advisories, authentication anomalies, performance and deterministic reconciliation failures. Report evidence, severity, affected records/services and a proposed action. Deduplicate alerts and record investigation outcomes.

Proposed fixes must pass tests in an isolated environment. Financial data changes, access grants, payroll adjustments and production deployments require the applicable approval. Later automate narrowly scoped reversible operations only after explicit procedures, rollback and audit are demonstrated. Monitor the monitoring pipeline itself; an agent failure must not disable underlying alerts.

## 8. Production readiness and long-term continuity

Required gates: Finance and operations acceptance; HR/payroll acceptance for that workstream; permission/security review; representative load tests; full restore drill; migration rehearsals; support ownership and incident procedure; controlled parallel operation with reconciled outputs.

Define peak workload and acceptable response times before benchmarking. Test more than 8,000 items plus representative history, concurrent ERP work and attendance bursts. Item count is not a substitute for a workload model.

Keep Dynamics as the designated system of record for each transaction class until its replacement is accepted. Avoid uncontrolled dual posting. Cutover requires reconciled opening balances, outstanding orders, stock/reservations and employee records, with a defined rollback decision point.

Plan ongoing dependency upgrades and regression tests. Retain a reproducible deployment, schema migrations, documentation, source history, backup procedures and licence inventory. Verify that another qualified maintainer can operate the system from these materials. Protect stable master identifiers so new modules and future migrations preserve history.

## 9. Next implementation package

1. Propose the supported application/database stack and hosting alternatives with operating implications.
2. Define the initial data model, document states, permission matrix and posting contracts.
3. Prepare synthetic fixtures and Finance-reviewable expected accounting results.
4. Implement the bounded proof and report executed results, failures and remaining gaps.

This sequence authorizes design and local prototype work within the existing request. It does not purchase hosting, migrate real business data, deploy production or establish a delivery commitment.
