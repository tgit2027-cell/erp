# Finance requirements reconciliation and isolated draft foundation

## Current integration-readiness reconciliation — 2026-10-21

The shared Finance destination is now an **informational availability/status
page**, not a generic claim that all Finance development is absent and not an
activated Finance editor. Existing lab implementations below remain isolated.
Normal shared schema recovery is complete; no Finance access follows from it.
See [current section paths, exact lab prerequisites and missing integration
steps](INTEGRATION_REVIEW_GUIDE.md) and
[readiness distinctions](FINANCE_READINESS_MATRIX.md).

Next candidate is source-linked AP receipt-reference review in the existing
isolated invoice proposal—not another worksheet or operational invoice matching.
It must intersect current lab access with the unchanged receipt panel/data/
BU/location service authorization. No account/amount inference, cost visibility
expansion, tolerances or posting. This candidate is documented, not implemented.
The full supplied scope and beneficial additions in the existing matrix remain
required; no draft or navigation page makes operational Finance complete.

**Current owner-confirmed language scope (2026-10-20):** internal ERP controls,
validation and Finance draft/recurring editors are **English only**. Only
customer quotation/invoice documents require bilingual English/Arabic. Supplied
Arabic business content remains stored/editable with English field labels.
Earlier bilingual-interface descriptions are superseded; they do not establish
a bilingual internal-ERP requirement.

**Current status — isolated persisted account/dimension setup, journals, recurring templates, opening-balance worksheets, accrual/prepayment adjustments, allocation proposals, explicit-input budget scenarios, reconciliation proposals, expense/petty-cash proposals, fixed-asset record drafts, cash-forecast scenarios, supplier-invoice, supplier credit/debit, internal customer-invoice, customer-credit and unpaid/unapplied supplier-advance proposals implemented; operational Finance/AP/AR is not complete.**

Existing drafts now have consolidated review/reopen navigation and durable,
actor/entity/request-scoped create recovery in isolation. No additional
financial worksheet was added. See [protocol](FINANCE_DRAFT_RECOVERY.md) and
[updated finite backlog](#finite-draft-backlog-and-consolidation-plan).

**Source-linked GL reversal review is also implemented in isolation**:
specific immutable journal revision, explicit proposed inputs, exact balance/
account-BU-dimension net comparison, history/conflicts and create recovery.
Reversal-batch evidence:22 focused /173 full relevant Finance PostgreSQL tests,
125 affected frontend/helper tests,2 actual authenticated synthetic browser
journeys,349 contrast pairs and project/schema checks. See
[consolidated readiness matrix](FINANCE_READINESS_MATRIX.md); no operational
GL/AP/AR activation, shared authority or fourth worksheet presumed.

Subsequent packaging consolidation now loads only the selected existing editor.
New checks:131 affected Finance UI/helper,23 quotation regressions,4 actual
authenticated synthetic browser journeys and349 contrast pairs; typecheck/
build/disposable schema checks pass. No backend service change or backend-suite
rerun; prior173/22 counts remain prior evidence. Measured bytes/tradeoffs:
[editor loading](FINANCE_EDITOR_LOADING.md). Shared recovery decision now
reviews foundation0023–0027 but no DB start/upgrade occurred.

## Sources and acceptance language

- Owner-supplied Finance scope restated in the implementation request and
  [roadmap Finance coverage](docs/history/MILESTONE_ROADMAP_2026-10-16_to_2026-10-21.md#received-finance-scope--required-coverage-policydesign-pending).
  No separate Finance attachment was available in the inspected repository;
  this matrix preserves the supplied list rather than claiming to have read one.
- [Technical foundation](specs/Tivoli_ERP_Technical_Foundation_v0.1.md), especially
  core entities, state/history, decimal, access and integration boundaries.
- [Architecture/proof](specs/Tivoli_ERP_Custom_Architecture_and_Proof_v0.1.md).
- [Business readiness](BUSINESS_READINESS_CHECKLIST.md#5-finance--accounting) and
  [current review handoff](REVIEW_HANDOFF.md#isolated-recurring-journal-template-proposals--2026-10-20).
- Actual models/services/routes/tests linked below. A sidebar label, model name,
  synthetic arithmetic test or roadmap bullet is **not** proof of an operational
  Finance workflow.

**Company boundary:** one legal entity with business-unit dimensions. No
multi-company operation, consolidation eliminations, intercompany posting or
currency policy is presumed. QAR below is only the established synthetic test
invariant, not activation of production accounting.

“Existing” means implemented before this batch. “New” means implemented here.
“Missing” is not silently approved. No team acceptance or production readiness
is claimed.

## Requirement-to-implementation matrix

| Supplied requirement | Working capability / traceable evidence | Missing scope | Dependency and next bounded deliverable |
| --- | --- | --- | --- |
| GL: chart of accounts | Existing `LedgerAccount` and test-only mappings in [`transaction_services.py`](foundation/transaction_services.py); not an account-administration screen. **New:** separate `FinanceDraftAccount` proposals and test-only create service in [`finance_drafts.py`](foundation/finance_drafts.py). | Approved chart structure, classifications, mappings, balances and live account administration. | Draft code/name setup needs no production mapping. Activation needs approved chart/mappings and Finance access scope. |
| GL: accounting periods | No operational period model, close/open control or accepted fiscal-calendar setup found. | Period configuration, locks and controlled reopen. | Production calendar/authority remain decisions. Explicitly entered draft-period proposals can be a later isolated design, not inferred current periods. |
| GL: manual journals, debit/credit validation and review | Existing immutable operational synthetic journals remain unchanged. Separate draft creation/review tests: [`test_finance_drafts.py`](foundation/test_finance_drafts.py). **Implemented:** real UI/backend list, save, reload/reopen, edit, preserved revision snapshots and optimistic-conflict recovery; [`test_finance_draft_workflow.py`](foundation/test_finance_draft_workflow.py), [`finance-draft-real-browser.test.mjs`](scripts/finance-draft-real-browser.test.mjs). | Authorized shared-user entry, Finance review/approval and posting; no whole-GL completion claim. | Shared exposure waits for approved Finance capability/scope, not inferred staff/superuser authority. Draft-only expansion continues independently. |
| GL: dimensions | Existing legal-entity/BU references on operational records. **New:** optional existing-BU dimension per draft line plus proposed dimension definitions/values, same-entity validation and synthetic-only constraints. | Approved dimension taxonomy, mandatory/conditional rules, production allocations. | Owner-entered draft labels/values are allowed; no dimension meaning or requirement is inferred. |
| GL: recurring entries, accruals, prepayments, allocations and reversals | **Isolated implemented:** recurring/accrual/prepayment and explicit allocation proposals with exact immutable/conflict-protected persistence; existing recurrence/adjustment/allocation tests. **Source-linked reversal review now implemented:** explicit historical journal revision/date/explanation/accounts/BU/dimensions/amounts, exact arithmetic offset review, real persistence/revisions/recovery; [`reversals.py`](finance_allocation_lab/reversals.py), [`test_reversals.py`](finance_allocation_lab/test_reversals.py), [`actual browser`](scripts/finance-reversal-real-browser.test.mjs). Purchase-value landed allocation remains separate. | Operational scheduling, recognition/amortization, rule-driven allocation/reversal generation, Finance approval and posting. | No frequency, month-end/holiday, allocation basis/percentages, residual distribution, FX, recognition, reversal sign/date/account choice or approval inferred. A grouped-net match is not accounting approval; activation needs approved dependent rules and access. |
| GL: FX, opening balances, future intercompany | Existing journals are QAR-only synthetic fixtures. **Isolated opening-balance worksheet proposals are implemented**, with explicit as-of dates, exact amounts, saved revisions/conflicts; [`test_finance_opening.py`](foundation/test_finance_opening.py). No operational opening import, FX or intercompany workflow. | Rates, revaluation, actual opening migration/reconciliation and future intercompany. | Approved rates/treatment/migration controls needed for activation. Preserve future scope; do not assume several companies. |
| AP: suppliers and bank-detail approvals | Existing `SyntheticCounterparty` supplier references and permission-gated procurement directory are not supplier/bank master approval. | Supplier onboarding, bank verification/approval and audit lifecycle. | Authority, sensitive-field access and validation responsibilities required. No procurement power or supplier visibility is inferred from Finance drafts. |
| AP: invoices, credits, debits, advances | [Supplier-invoice proposals](#supplier-invoice-proposals--implemented-in-isolation), [credit/debit drafts](#supplier-creditdebit-proposals--implemented-in-isolation), and [unpaid/unapplied advance proposals](#supplier-advance-proposals--implemented-in-isolation): explicit references/dates/reasons/QAR amounts, existing draft accounts/BU/dimensions, exact review and persisted revisions/conflicts. Invoice duplicate advisory remains separate. Existing commitments/receipts/landed charges are not an AP subledger. | Issued/approved operational documents, advances executed/applied, settlement, subledger and posting. | No supplier master/directory/bank authority inferred. Classification does not infer signed balance/account mapping. Tax, matching, payment terms/percentages, approvals, posting and shared access need approved rules. |
| AP: matching, payments and aging | Receipt/commitment line progress and over-receipt locking are implemented in [`purchasing_services.py`](foundation/purchasing_services.py), tested in [`test_purchasing_workflows.py`](foundation/test_purchasing_workflows.py). These are not invoice matching or payable settlement. | Tolerances, invoice matching, settlement, due dates and payable aging. | Matching/authority/payment-term policy required before activation; do not invent tolerance bands or due dates. |
| AR: customers, limits and holds | Existing minimal scoped customer choices for quotes, fixed-price quotation and collection follow-up. Not customer-account administration or credit control. | Customer account lifecycle, limits, credit holds and authorization. | Customer-data ownership/visibility and credit authority required. Pending temporary-hold stock behavior remains unchanged. |
| AR: invoices, credits, overpayments and write-offs | [Internal customer-invoice proposals](#internal-customer-invoice-proposals--implemented-in-isolation) and [customer-credit proposals](#customer-credit-proposals--implemented-in-isolation): explicit synthetic references/dates/QAR lines, exact totals, validated optional invoice link, persisted immutable revisions/conflicts. Invoice duplicate warning remains advisory. Separate bilingual customer documents unchanged; returns non-posting/Finance-pending. | Issued operational documents/subledger, credit notes, excess-money application and write-offs. | Customer master/credit/refund/tax/recognition/settlement/output and production authority decisions block dependent activation, not independent drafts. AP advance proposals are separately implemented preparation, not payment/application. |
| AR: collections, promises/disputes and FX | Existing simulated quotation collections and merchandise-only balance follow-up; idempotent post-delivery collections retain delivered state. [`RETAIL_TEAM_ACCEPTANCE.md`](RETAIL_TEAM_ACCEPTANCE.md), [`test_retail_operations.py`](foundation/test_retail_operations.py), purchasing tests. | Real settlement/bank application, promises/disputes, AR aging and FX. | Existing prototype collections are not receivables or real money. No new dates/credit terms or settlement rules. |
| Cash/bank and reconciliation | Isolated English [reconciliation worksheet](src/FinanceReconciliationLab.tsx), [explicit-input service](finance_allocation_lab/reconciliation.py) and [PostgreSQL tests](finance_allocation_lab/test_reconciliation.py): manually entered dated QAR balances/rows, exact differences, full-row match proposals, real saved revisions and conflicts. No operational bank account is marked reconciled. | Operational cashbook/bank setup, bank connections/imports, complete-source/cutoff semantics, approved reconciliation/adjustment workflow. | Bank formats/data handling, mappings, matching/tolerance conventions, adjustment/approval authority and shared Finance access remain unapproved; prototype arithmetic is not operational reconciliation. |
| Fixed assets | [Isolated fixed-asset record drafts](#fixed-asset-record-drafts--implemented-in-isolation): proposed identifiers/descriptions/dates/QAR cost and draft account/BU/dimensions, real disposable register/save/reopen/edit, immutable revisions and conflict recovery. Not a capitalized asset subledger. | Operational asset identity/register/evidence, capitalization, depreciation, transfers/disposals, reconciliation and ledger integration. | Approved shared Finance access plus specific asset identity/classes, thresholds, lives/methods, custody/ownership, approval, tax and posting rules are required only for their dependent activation. |
| Budgeting | Isolated persisted scenario entry/list/reopen/edit, explicit dates/QAR by draft account/BU/dimensions, exact totals, advisory duplicates/overlaps and immutable revisions/conflict recovery: [`budget.py`](finance_allocation_lab/budget.py), [`FinanceBudgetLab.tsx`](src/FinanceBudgetLab.tsx), [`test_budgets.py`](finance_allocation_lab/test_budgets.py), [`real browser tests`](scripts/finance-budget-real-browser.test.mjs). Existing synthetic `ReportBudget`/budget-actual reporting and controlled report access are separate and unchanged. | Operative budgets, ownership/approval/spending controls, actuals classification/integration, fiscal calendars, period splits, distributions and shared Finance authorization. | Proposed input totals are not approved budgets, actuals or enforcement. Overlap/duplicate warnings are advisory, not acceptance rules; no rows are consolidated. No worksheet values feed existing reports or operational budgets. |
| Configurable future tax, no VAT activation | No active tax rates, tax engine or VAT behavior. Scope retained. | Future tax configuration, applicability and controls. | Finance/legal-approved applicability/rates/mappings required; no inferred Qatar-law statement. |
| Expenses and petty cash | Existing synthetic costs/expenses report panel plus [isolated explicit-input expense/petty-cash proposals](#explicit-input-expensepetty-cash-proposals--implemented-in-isolation): real disposable persistence, exact totals, immutable revisions and conflict recovery. Neither is operational claim approval or petty-cash settlement. | Real evidence handling, operational claims/advances/settlement, custody, reimbursement/disbursement, approvals, posting and integrations. | Approved Finance access/scope/assignments, evidence/eligibility, categories/mappings, authority, tax and custody/limit rules are required only for their dependent operational activation. |
| Period close and reporting | Posted-journal balance diagnostic, scoped operational reports and report audits exist. | Trial-balance/financial statements under accepted mappings, closing checklist/locks/reopen and reconciliation sign-off. | Company accounting/calendar/close authority required; diagnostics do not certify a close. |
| Project management accounting | Required roadmap scope only; ordinary BU references do not implement projects. | Project structures, budgets, commitments, actuals, cost attribution and reporting. | Project system-of-record, attribution and approval boundaries required. |
| Approvals/workflow engine | Existing purchasing role ceilings and separately controlled report/capacity administration; no Finance workflow authority can be inferred from them. | Finance-specific draft/review/approve/post segregation, delegation and audit lifecycle. | Define Finance capabilities/scopes first. No grants, memberships or user assignments are added by this batch. |
| Sales Order / Procurement / Inventory integration | Existing atomic synthetic receipts/deliveries, stock ledger, snapshots, reservations, outbox and test journal facts. | Approved operational Finance document/subledger/posting integration, corrections and reconciliation. | Preserve existing test assumptions; production mappings/valuation/recognition and correction rules remain decisions. |
| Cash forecasting; facilities/loans | [Isolated manual cash-forecast drafts](#cash-forecast-scenario-drafts--implemented-in-isolation): explicit proposed opening/date and dated inflows/outflows, exact proposed balances, persisted revisions/conflict recovery. No live cash or bank integration. | Operational forecasting/data integration/authority; facilities/loan lifecycle remains unimplemented. | Actual data sources, realization/timing conventions, ownership, terms and authority require specific approval. Quotation balances are not assumed cash forecasts. Manual draft display order is not payment priority. |
| BU shared-cost reporting; factory costing | Beneficial additions retained; current cost/report access is not an allocation or factory-costing system. | Allocation inputs/bases, factory lifecycle/cost model and reconciliation. | Shared-cost attribution and manufacturing/valuation rules required. |
| Payroll integration | Required beneficial future integration; HR/Payroll remains unfinished. | Authorized payroll interface and journal mappings. | HR/payroll privacy, legal, calculation, access and posting rules; no employee data here. |
| Controlled exports | Existing server-authorized report Excel/Word exports, full authorized result set, field masking and `ReportExportAudit`; [`reporting_exports.py`](foundation/reporting_exports.py), reporting tests. | No Finance draft export/print/download entitlement is established. | Existing report-export authority is not reused to grant draft-journal export. |
| Migration planning | Portable setup, local synthetic backup/restore evidence and reconciliation diagnostics exist. | Real Finance migration inventory, mappings, opening validation, cutover and acceptance. | Owner-approved source data/access, mappings, retention and cutover decisions. No production import/connection in this batch. |

## Current isolated draft workflow

## Supplier-advance proposals — implemented in isolation

English-only `/lab-supplier-advances-ui/` via existing Finance lab setup.
Explicit synthetic supplier/document reference, proposed date, purpose and
non-negative exact QAR amount. Existing saved draft account, active BU and
dimensions independently optional—blank remains blank. Server validates
provided references against synthetic entity, strict date/decimal/whitelist,
without supplier master/directory/bank access. Exact review and persistence
always proposed/unpaid/unapplied, no payment/application/payable effect,
approval/posting or inferred terms/percentages/tax/mapping.

Real disposable PostgreSQL save/list/reopen/edit and immutable revisions,
atomic writes and competing-edit recovery preserving local work. Current
uncertain-save limits remain explicit, not exactly-once creation. Raw Arabic
data preserved; internal labels English, bilingual issued documents unchanged.
Lab-only migration0011 absent from normal graph, disposable-only; no shared
changes. [Service](finance_allocation_lab/supplier_advances.py),
[API](finance_allocation_lab/supplier_advance_views.py),
[UI](src/FinanceSupplierAdvanceLab.tsx),
[PostgreSQL](finance_allocation_lab/test_supplier_advances.py),
[rendered](src/finance-supplier-advance-ui.test.tsx),
[real browser](scripts/finance-supplier-advance-real-browser.test.mjs).

New checks:20 PostgreSQL16.15 tests (10 advance +10 invoice),27 affected
frontend/helper tests (4 advance +8 invoice +15 lab),2 authenticated actual-
backend desktop/mobile flows;321 existing contrast pairs, typecheck/main+
lab build, Django/schema/migration/syntax/diff passed. Exact commands,
names/durations/screenshots/limits in latest REVIEW_HANDOFF.md. No full ERP,
normal Finance login or team acceptance claim. Disposable data/services removed.
Shared-preview recovery remains separate and outstanding.

## Finite draft backlog and consolidation plan

**Consolidation and create recovery now implemented/verified in isolation.**
`/lab-review-ui/` reopens existing saved types through their original editors.
Actor/entity/request identity atomically records original create outcomes,
same-request replay and metadata-only status recovery; conflicting reuse409,
current guard enforced. See [protocol/limits](FINANCE_DRAFT_RECOVERY.md) and
latest REVIEW_HANDOFF.md. New evidence:9 focused /160 full relevant Finance
PostgreSQL tests,119 affected frontend/helper tests,2 actual authenticated
synthetic browser journeys,349 contrast pairs, project/schema checks passed.
No shared activation, unrelated worksheets, posting or policy added.

Planning scope is finite; not authorization to invent additional worksheets
for every unimplemented operational requirement. One legal entity, BU
dimensions, existing isolated access; no shared exposure or inferred policy.

| Priority / remaining independent item | Concrete gap and bounded next deliverable | Acceptance boundary |
| --- | --- | --- |
| 1. Consolidate existing saved-proposal review/reopen journeys — implemented in isolation | Unified guarded review of12 existing endpoint families including all journal kinds, current metadata and original-editor deep links. No new accounting calculation, business record or merged monetary total. | Actual advance/invoice cross-type persisted navigation/reopen, dirty/pending safeguards, forbidden/entity boundaries and existing editor regressions verified. All12 families covered by shared guard/index tests; not every deep link separately native-tested. No shared rights or team acceptance. |
| 2. Recoverable create/retry outcome — implemented in isolation | Atomic durable actor/entity/request receipt, canonical original input identity, metadata-only status, explicit replay and protected current-revision reopening. No payload stored in browser storage. | Real post-commit acknowledgement loss, independent concurrent identical/conflicting calls, replay, rollback and unauthorized lookup proved. At-most-one successful create within retained identity/DB scope, not universal exactly-once. Legacy no-header clients/forgotten IDs/storage/database loss remain outside guarantee. |
| 3. Source-linked explicit GL reversal proposal review — implemented in isolation | Specific immutable source revision plus explicit proposed inputs, exact balance and account/BU/dimension grouped-net offset review; persistent revisions/relink/conflicts and durable create recovery. |22 focused /173 relevant backend,125 frontend,2 real-backend desktop/mobile journeys passed. Source/history preserved, source/proposal immutability protected, current authorization retained. No auto swap/date/period/account choice, generation, schedule, approval, posting or shared activation. |

All three bounded planning items are now implemented in isolation. **Do not assume further draft
worksheets are approved or needed**. Reconcile actual reviewer feedback and
requirements before extending the list. This is not a claim that the full
Finance backlog has only three items; the following operational scope remains.

| Remaining operational requirements | Exact dependency / safe limit |
| --- | --- |
| GL account structure, dimension taxonomy, periods/locks/reopen, approve/post, recognition, recurrence, allocations, real opening balances, FX/intercompany | Approved chart/mappings/calendar/recognition/rates/authority and migration decisions. Existing draft arithmetic is not operational accounting. Future intercompany is retained, not presumed multi-company operation. |
| AP supplier/bank approval, invoices/credits/debits/advances, matching, payments/settlement/aging; AR customer/credit controls, invoices/credits, collections/overpayment/write-off/promises/disputes | Master-data ownership, independent Finance capabilities/scopes, terms/matching/credit/refund/approval/mapping decisions. Prototype references and unpaid advances confer none of these powers; no due dates/buckets/tolerances invented. |
| Bank feeds/imports, operational reconciliation, cash forecasting, facilities/loans | Authorized sources, timing/match/tolerance/adjustment/account/contract/authority rules. Existing manual reconciliation/forecast proposals stay separate from actual cash. |
| Assets depreciation/capitalization/disposal/custody, operative budgets, expenses/petty cash settlement, close/financial statements | Life/residual/threshold, ownership/fiscal/distribution, reimbursement/custody/limit, close/reopen/report mapping and authority decisions. Existing proposed cost/amounts are not approved assets/budgets/payments. |
| Project accounting, BU shared-cost attribution, factory costing, payroll integration, future tax | Project/factory/HR systems of record, attribution/valuation/privacy/calculation/mapping and Finance/legal tax configuration. Do not invent a project schema, Qatar-law assertion or tax rate. |
| Sales/Procurement/Inventory Finance integration, controlled Finance output, migration/cutover/shared lab activation | Approved document/posting/valuation/correction mappings and scope, separate output entitlement and explicit approved user assignments; verified backup/restore and separately authorized shared recovery. Existing operational reports/exports do not grant Finance access. |

All original Finance requirements and retained beneficial additions remain in
the matrix; missing policy blocks only dependent activation, not existing
isolated development or reviewer-led consolidation. Shared recovery is a
separate technical/authorization dependency, not a completed step here.

## Customer-credit proposals — implemented in isolation

Internal English-only `/lab-customer-credits-ui/` linked from existing synthetic
Finance lab setup. Explicit customer/document reference/date/reason and QAR
non-negative magnitudes, descriptive lines and saved draft accounts/optional
BU/dimensions. Exact totals, date/reference validation and same-entity choices.
Optional explicit customer-invoice proposal link validates current entity and
case-sensitive customer reference (surrounding ASCII whitespace ignored);
captures locked source ID/revision/reference evidence without modifying source
invoice/history. Later source changes never rewrite saved credit history;
editing revalidates current source. Explicit link removal supported.

Sources: [service](finance_allocation_lab/customer_credits.py),
[API](finance_allocation_lab/customer_credit_views.py),
[UI](src/FinanceCustomerCreditLab.tsx),
[PostgreSQL tests](finance_allocation_lab/test_customer_credits.py),
[rendered tests](src/finance-customer-credit-ui.test.tsx),
[real browser](scripts/finance-customer-credit-real-browser.test.mjs).

Real disposable PostgreSQL save/list/reopen/edit, atomic immutable revisions,
optimistic competing-edit recovery retaining local work;400 keeps inputs,
403 clears; unknown-save outcome requires inspect/reopen, not exactly-once
creation. No inferred eligibility, signed receivable effect, source-value cap,
account mapping, tax or approvals. Not an issued credit note, refund,
settlement, receivable adjustment or posting. No new customer master/output
authority. English internal editor preserves Arabic data and separate
bilingual customer documents unchanged.

New focused verification:21 PostgreSQL16.15 tests (11 credit +10 invoice),
26 affected frontend/helper tests (4 credit +7 invoice +15 lab),2 actual
authenticated real-backend desktop/mobile flows,321 existing contrast pairs;
typecheck/main+lab build, Django/schema/migration/syntax/diff checks passed.
Exact names/commands/times, screenshot and scope limits in REVIEW_HANDOFF.md.
Lab-only migration0010 absent from normal graph, applied only to owned
disposable clusters then cleaned up. No shared upgrades/records/grants/
credentials. No full ERP rerun, ordinary Finance login, shared-team acceptance
or operational AR completion claim; shared-preview recovery still outstanding.

Subsequent candidate, now implemented above: AP supplier-advance
proposals with explicit synthetic references/date/reason/QAR amount and saved
draft account/BU/dimensions, persisted immutable revisions/conflicts only.
Not payments, bank instructions, invoice application, payable effects or
posting; no inferred payment terms/tax/approvals/matching/account mappings.

## Internal customer-invoice proposals — implemented in isolation

English-only `/lab-customer-invoices-ui/`, linked from the existing isolated
Finance lab setup and its explicit synthetic test session. Sources:
[service](finance_allocation_lab/customer_invoices.py),
[API](finance_allocation_lab/customer_invoice_views.py),
[UI](src/FinanceCustomerInvoiceLab.tsx),
[PostgreSQL tests](finance_allocation_lab/test_customer_invoices.py),
[rendered tests](src/finance-customer-invoice-ui.test.tsx) and
[real browser](scripts/finance-customer-invoice-real-browser.test.mjs).

Explicit required synthetic customer/invoice reference and invoice date;
optional manually entered synthetic customer details and independent due date.
Descriptive exact QAR amount lines use existing saved draft accounts, optional
same-entity BU/dimensions; no customer master lookup, dates/terms, account
mapping, credit limits, tax or payments are inferred. Raw supplied Arabic
content is retained, not translated. Internal labels are English; issued
customer quotation/invoice presentation remains bilingual and unchanged.
Review is proposed/not-issued/not-operational-receivable/not-approved/not-posted.
No print/export/output authority or operational document is added.

Real disposable PostgreSQL list/save/reopen/edit, immutable atomic revisions
and explicit conflict reload retaining local work. Duplicate warning observes
current same-entity AR proposal references (case-sensitive, surrounding ASCII
whitespace ignored), never mixes AP/operational records, merges or enforces
uniqueness. Unknown save retains existing explicit inspect/reopen safeguards;
not exactly-once creation or permanent shared storage. Lab-only migration0009
is absent from normal graph and applied only to disposable clusters; no shared
schema/data/grant/credential change. Shared-preview recovery stays outstanding.

New verification:20 PostgreSQL16.15 tests (10 AR +10 invoice regressions),
30 affected frontend/helper tests,2 authenticated real-backend desktop/mobile
flows,321 existing contrast pairs, typecheck/main+lab build, Django/schema/
migration/syntax/diff checks passed. See latest REVIEW_HANDOFF.md for exact
commands/names/times/screenshots and limitations. Not ordinary Finance login,
shared-team acceptance, full-suite verification or operational AR completion.

Subsequent candidate, now implemented above: isolated customer credit
proposals, optional validated source invoice link, explicit reasons/QAR
magnitudes and persisted revision/conflict review; not issued credit notes,
refunds, receivable effects, settlement, account mappings or posting.

## Supplier credit/debit proposals — implemented in isolation

English-only `/lab-adjustments-ui/` linked from the existing Finance lab setup.
Sources: [service](finance_allocation_lab/supplier_adjustments.py),
[API](finance_allocation_lab/supplier_adjustment_views.py),
[models](finance_allocation_lab/models.py),
[UI](src/FinanceAdjustmentLab.tsx),
[helper](src/finance-adjustment.ts),
[PostgreSQL tests](finance_allocation_lab/test_supplier_adjustments.py),
[rendered tests](src/finance-adjustment-ui.test.tsx) and
[real browser](scripts/finance-supplier-adjustment-real-browser.test.mjs).

Reviewer explicitly chooses credit/debit classification, synthetic
supplier/document references, proposal date/reason and optional source invoice.
Descriptive QAR line amounts are exact non-negative magnitudes; existing saved
draft accounts and optional same-entity BU/dimensions are validated. No account
mapping, signed payable impact, tax, settlement/matching, terms, approval or
posting convention is inferred. Proposal review explicitly rejects the
interpretation of issued credit notes, approved adjustments or operational AP.
No supplier master authority or procurement-directory access is established.

Optional link validates current invoice entity/synthetic flag and supplier
reference under transaction locks. Comparison is case-sensitive apart from
surrounding ASCII whitespace; original supplied references are retained.
Source invoice/history remain unchanged. Minimal invoice revision/reference
evidence is preserved in each immutable adjustment snapshot, not live-tracked;
later source edits do not rewrite history, and future saves revalidate linkage.
Real PostgreSQL list/save/reopen/edit with atomic immutable revisions, expected
revision conflict recovery and preserved local inputs. Unknown saves retain
the existing explicit inspection/reopen boundary, not exactly-once creation.

Lab-only migration0008 is absent from normal migration graph; no shared schema
or access change. Current new evidence:21 PostgreSQL16.15 tests (11 adjustment,
10 invoice),29 affected frontend/helper tests,2 authenticated real-backend
desktop/mobile flows,321 existing contrast pairs, typecheck/main+lab builds,
Django/schema/migration/diff checks passed. Full unrelated ERP suites were not
rerun. See latest REVIEW_HANDOFF.md for exact commands/names/durations, screenshots
and limitations. This is isolated prototype verification, not shared-user
Finance login, team acceptance or operational AP completion. Shared-preview
recovery remains separately outstanding.

Subsequent candidate, now implemented above: isolated AR customer-invoice
proposal drafts using explicit references, dates and QAR lines with persisted
revision/conflict review; no customer master authority, issued document/output
entitlement, credit/terms/tax, operational receivables, payments or posting.
AP advance proposals remain unimplemented; matching/payment/aging and all
operational activation require specifically approved rules and access.

### Explicit recurring-template proposals

The connected lab now includes **New recurring template** and a separate
template register; it does not turn ordinary journals into recurring ones.
Templates use existing draft accounts/dimensions and exact entered line amounts.
Each explicitly supplied date repeats that common line set unchanged.

Enter inclusive range start/end and **every occurrence date** as YYYY-MM-DD,
one per line, in strictly increasing order. No dates are generated or adjusted.
Blank/invalid dates, ambiguous formats, duplicates, unordered/out-of-range dates,
reversed ranges and unsupported convention fields are rejected without writes.
100 dates and 200 lines are technical input/preview bounds, not company policy.

Save/reopen/edit uses the same immutable revision snapshots and expected-version
conflicts as journal drafts. Date metadata is preserved in each new snapshot.
Draft kind cannot change during editing. Migration **0025** adds only draft kind,
proposal JSON and a kind/metadata constraint; existing rows default to ordinary
journals, and earlier revision snapshots are not rewritten. It was applied
only to disposable databases by this batch.

**Preview saved proposed occurrences** reads the saved balanced revision.
Unsaved edits must be saved first; editing clears any displayed old preview.
Preview shows exact dates, each occurrence's balanced totals, aggregate proposed
totals and the common account/BU/dimension entry lines. It creates no occurrence
records, jobs, operational journals or other business records. Incomplete drafts
cannot produce an occurrence preview.

There are no schedules, frequency/month-end rules, per-date amount overrides,
FX, accrual recognition, approval or posting. Dates are proposal dates, not
recognized accounting periods or effective posting dates. The existing lab
test-session boundary and unknown-save recovery apply unchanged.

The original create/review foundation below is retained. The next bounded batch
now connects it to the English-only UI **inside a disposable runner only**:

- [`FinanceDraftLab`](src/FinanceDraftLab.tsx) uses real Django HTTP endpoints
  from a **separate URLConf**, [`finance_lab_urls.py`](foundation/finance_lab_urls.py).
  The normal app does not include those routes or mount the Finance editor.
- List saved drafts, open/reopen them after a page reload, edit lines and
  English/Arabic titles, review exact totals and save a new revision.
  Account/dimension/value setup also round-trips to that same temporary DB.
  Multiple dimension values per line are retained on reopening/editing.
- Accepted creation records revision 1. Editing locks the journal row and
  requires the exact expected revision. A stale editor receives **HTTP 409**;
  its local inputs remain visible until the user explicitly confirms reloading
  the saved version. No automatic overwrite or retry occurs.
- Every accepted edit stores a complete immutable snapshot of title, lines,
  account references/names, BU/dimensions, exact amounts, memos and balance
  review. PostgreSQL blocks UPDATE/DELETE of revision rows. An old unversioned
  isolated draft gets an actual baseline snapshot at first edit, not invented
  earlier history.
- Draft state and QAR synthetic invariants remain. Balanced review is not
  approval or posting. UI saving requires a locally balanced review; the
  internal service still supports incomplete drafts for review as before.
- Failed validation/conflicts retain editor inputs. Transient refresh failures
  retain authorized content; actual synthetic-session denial clears it.
  Pending operations disable navigation/duplicate submission and warn on
  unload. Switching saved drafts/new draft asks before discarding local edits.
  A lost save response is explicitly an **unknown outcome**, not a failed
  commit: save is blocked until explicit reopening/recovery.

### Authorization and persistence boundaries

The runner checks actual PostgreSQL 16.15 `data_directory` before migrations,
pins loopback ports **55167 / 18069**, unsets shared connection/secret inputs,
creates one new `test_finance_lab` database and supplies only a minimal synthetic
entity, two BU choices and one **nonstaff, non-superuser actor with an unusable
password and zero grants**. It runs no demo seed. Every lab request rechecks the
configured actual temporary directory, explicit lab flag, PostgreSQL `test_*`
target, `APP_ENV=demo` and synthetic entity. Data endpoints also require the
explicitly opened synthetic test session; CSRF is enforced on writes.

This is a real Django session and backend, **not** ordinary ERP password login
or evidence of an approved Finance permission model. Staff/superuser status
does not give lab data access. No real user assignment is made. All lab drafts
belong to the one fixed synthetic fixture entity and the one test actor; no
multi-user Finance ownership/access policy is implied.

Saved drafts survive page reload/new browser sessions **while this runner is
active** because they are committed PostgreSQL records. They are intentionally
discarded, with the entire database, when the runner exits. This is not
production durability, backup/restore or shared-team availability.

Migration `0024_finance_draft_revisions` adds revision metadata to the separate
draft table plus a revision table and its immutability trigger. It does not
change operational journals, financial rules or grants. Neither 0023 nor 0024
was applied to the shared demo by this batch; shared schema state was not
queried.

### Persisted domain foundation — disposable PostgreSQL only

- Migration [`0023_finance_draft_foundation`](foundation/migrations/0023_finance_draft_foundation.py)
  creates **six new synthetic draft tables** and their constraints/relations.
  There is no data migration, existing-table alteration, account mapping, seed,
  user/grant assignment or posting trigger. `default_permissions = ()` prevents
  implicit Django model permissions from being created for these models.
- Draft account and dimension/value codes require their explicit `TEST-DRAFT-`
  prefixes. Arabic names are optional supplied content; none are generated.
- Journals belong to one existing synthetic legal entity. Lines may carry an
  existing BU from that same entity and proposed dimension values. Other-entity
  references and duplicate dimension values are rejected.
- Atomic draft creation preserves incomplete/unbalanced drafts for review.
  Review reports exact two-decimal QAR test totals, missing/invalid line issues
  and balance problems without writing records. Draft records cannot change to
  a posted state under their database check constraint.
- This is **not a shared/main-app API** or a shared-user Finance workspace. Persistence requires
  `APP_ENV=demo`, explicit test-only `FINANCE_DRAFT_TEST_MODE=True` and a
  PostgreSQL database named `test_*`. The existing test harness additionally
  verifies its disposable PG16.15 data directory before migrations. Demo mode
  alone fails closed.

### Bilingual UI modes: local-session example versus persisted lab

[`FinanceDraftWorkspace`](src/FinanceDraftWorkspace.tsx) uses English control and
validation labels, optional supplied Arabic business content, explicit
account/dimension/value entry, add/edit/remove journal lines,
optional synthetic BU dimension, memos, exact decimal review, a per-line review
table and session-only draft snapshots. Invalid inputs retain the current draft
and show actionable feedback. There are **no HTTP requests** and no post,
approve, payment or export controls.

The prototype is not imported by `App.tsx` or registered in the shared sidebar,
routes, Favorites, API or Django admin. No current staff, superuser, sales,
purchasing, report or capacity authority is reinterpreted as Finance access.

**Connected disposable review**, with synthetic values only:

```sh
FINANCE_LAB_REVIEW=1 bash scripts/verify-finance-draft-workflow.sh
# Open http://127.0.0.1:18069/lab-ui/ in the same environment.
# Click “Open isolated synthetic test session”; no credentials are needed.
# Ctrl-C stops the server and deletes this temporary database.
```

Prerequisites: the existing PostgreSQL 16.15 binary path from the script,
installed project dependencies and free ports 55167/18069. The runner fails
closed if provenance/port ownership cannot be proved. It never starts/restarts
the shared workflow. No shared test account/credentials are supplied.
The loopback URL is for a browser on that same machine (for example a local
checkout), not a public/shared Replit review link. Inside this workspace,
`npm run test:finance-draft-real` runs the isolated browser journey and saves
captures. No new shared interactive access or public port is configured.

**Offline example only**, retained explicitly as session-only:

```sh
npm run build:finance-draft
npm run preview:finance-draft
# Open http://127.0.0.1:18068/ from the same local environment.
# Stop the command when review is finished.
```

This offline root has no database/login and its saves disappear on reload.
It must not be confused with `/lab-ui/` on the dedicated disposable Django
runner. The connected lab is now wired to persistence; this static mode is not.

Useful synthetic example: create `TEST-DRAFT-ACCT-A`, optionally a proposed
dimension/value, enter a debit of `0.30`, credits of `0.10` and `0.20`, then
review. Expected total is exactly `0.30 = 0.30`, state remains draft/unposted.
Change a credit to `0.19`; the review must report the imbalance without clearing
the entered lines. No production account names or mappings are supplied.

## Verification and honest limits

Exact current commands/results and test names are recorded in the
[handoff](REVIEW_HANDOFF.md#isolated-recurring-journal-template-proposals--2026-10-20).
The new PostgreSQL tests cover synthetic-only/schema guards, balance arithmetic,
incomplete drafts, entity/BU/dimension isolation, decimal rejection, atomic
rollback, read-only review, no operational-record creation and no new Finance
shared API/model permissions. New API/revision/concurrency tests and rendered
recovery tests supplement actual desktop/mobile UI-to-backend acceptance.

This does not establish an authorized shared Finance workflow. No shared demo
migration, real Finance account setup, ordinary/shared-user Finance browser
journey, real user assignment, posting, approved chart, tax/FX/period/valuation rule, Finance
approval, production integration, team acceptance or production readiness was
performed. No output entitlement is added.

## Opening-balance worksheet proposals — implemented in isolation

**Implemented:** non-posting opening-balance worksheet proposals use existing
synthetic draft accounts/dimensions, optional same-entity BU dimensions, an
explicit required as-of date and exact user-entered debit/credit strings. The
English lab has a separate worksheet register and create/reopen/edit actions.
Saved date/lines and immutable revision snapshots survive browser reload during
the disposable runner's lifetime. PostgreSQL row locks and expected revisions
reject stale edits with HTTP 409; inputs remain available for explicit recovery.
Review shows exact totals and signed debit-minus-credit difference without an
automatic balancing entry. The UI requires a valid balanced review to save, as
with the existing editor; the backend also permits unbalanced **drafts for
review**, never posting them. No date default or production calendar is inferred.

Migration 0026 adds only draft worksheet metadata/constraints; it has **not**
been applied to the shared demo. API and UI remain absent from normal shared
routes. This advances the GL opening-balance **proposal** requirement, not actual
opening imports, operative mappings, period activation or posting.
See [recovery assessment](FINANCE_PREVIEW_RECOVERY_ASSESSMENT.md) for the
separate schema/startup dependency and required approval; current checks and
limitations are recorded in REVIEW_HANDOFF.md.

### Bounded synthetic reviewer journey

Use only the established disposable facility:
`FINANCE_LAB_REVIEW=1 bash scripts/verify-finance-draft-workflow.sh`.
It initializes **its own temporary database**, applies schema there, creates
one unusable-password nonstaff synthetic test actor with zero grants, and
serves `http://127.0.0.1:18069/lab-ui/` on that machine while running.
It neither starts the managed preview nor seeds/resets the shared demo. This
same-machine URL is not an owner-accessible published/shared Finance service.
Stop with Ctrl-C; the temporary database is deleted, so do not treat it as
long-term storage or save real data. Never transplant test-session access into
the normal app.

1. Open the explicit isolated synthetic test session. Use TEST-DRAFT account
   and dimension/value definitions, not real account configuration.
2. Select **New opening-balance worksheet**; enter an explicit as-of date
   (for example `2028-02-29`), a synthetic title, accounts and optional BU/dimensions.
3. Enter debit `0.30` and credit `0.30` on separate lines. Review: totals
   `0.30 / 0.30`, difference `0.00`, synthetic/unposted. Save to disposable DB.
4. Reload the browser and explicitly reopen the worksheet; date/amounts remain.
   Edit the date/lines, review and save; revision increases and old history stays.
5. With the same saved revision open in a second lab browser, save in the first
   then attempt to save in the second. Expect explicit conflicting-edit feedback,
   retained local inputs, and no overwrite until deliberate reload/review.
6. A blank/impossible date or invalid/overprecision amount is rejected; imbalance
   is reported, not repaired. No import, posting, stock, payment, grant or
   operational journal record is created by these steps.

## Explicit-input accrual/prepayment adjustment proposals — implemented in isolation

There are now distinct **New accrual proposal** and **New prepayment proposal**
actions and a saved adjustment register. Each proposal requires a reviewer-entered
calendar date, explanation (technical limit 2,000 characters), title and exact
account/dimension/optional BU debit/credit lines. Dates/amounts are proposals,
not effective recognition or posting dates. No period, rate, allocation method,
number of months, recognition formula or offset account is chosen by the system.
If those values are needed, the reviewer must explicitly enter the intended lines;
the lab does not infer a calculation.

Save/reopen/edit uses the same disposable PostgreSQL persistence, immutable
revision snapshots, expected-revision conflicts and uncertain-save protection as
the preceding batches. Date/explanation are retained on validation/conflict and
replaced only on explicit saved-version reload. Changing the kind of an existing
saved proposal is rejected; create a distinct proposal instead.
Review shows entered debit/credit lines, exact totals and signed difference.
The API can preserve an unbalanced draft for review; the existing UI requires a
valid balanced review to save. There is no automatic balancing or journal generation.

Synthetic reviewer steps use the same-machine disposable facility described
above, not the shared preview: open the explicit lab test session, create/select
TEST-DRAFT account/dimension choices, choose one of the new proposal actions,
enter e.g. date `2028-02-29`, a synthetic explanation and two `0.30` debit/credit
lines; review/save, reload/reopen, edit/review/save, then verify a stale second
browser gets a conflict without overwriting the saved revision. Nothing here
imports, schedules, approves or posts. No real data should be entered.

Migration **0027_finance_adjustment_proposals** adds only draft metadata/kind
constraints and is applied only to disposable databases. No shared migration or
upgrade execution occurred. The previous preview recovery assessment remains
unchanged and separate; any future recovery must inspect/review the actual
pending chain, including this new migration, before shared application.
Exact new checks and access/persistence limitations are in REVIEW_HANDOFF.md.

## Next independent Finance candidate

**Explicit-input supplier credit/debit document proposals** are the next
independent supplied AP requirement. Use manual synthetic supplier/document
references, explicit dates/QAR lines and existing draft accounts/BU/dimensions,
exact review and persisted revisions/conflicts. Keep document types distinct
without inferred sign or account mapping. This next candidate is not
implemented; it must not settle or modify invoices, issue operational credit
notes, access supplier directories, or infer tax, terms, matching, approval,
payment or posting. Supplier-invoice proposal drafts are now implemented below,
not a completed operational AP system.
Shared-user activation remains dependent on an approved Finance capability/scope and deliberate
user assignment; that dependency does not stop isolated draft development.

## Supplier-invoice proposals — implemented in isolation

Owner-approved manual, synthetic references are not approved supplier masters.
English-only `/lab-invoices-ui/` linked from existing guarded `/lab-ui/`.
Explicit invoice date, optional independently entered due date (no terms/default
or chronological policy inferred),1–200 descriptions/exact non-negative QAR
amounts, required existing same-entity draft account and optional saved
BU/dimensions. Exact Decimal/BigInt totals; preserves original references,
descriptions and Arabic user content. No supplier data directory, bank fields
or real documents/imports. Review remains proposed, not operative/approved/posted.

Real disposable PostgreSQL register/save/reopen/edit, immutable snapshots,
atomic revision writes and optimistic-conflict recovery preserving local work.
Current server-scope duplicate advisory compares exact case-sensitive supplier/
invoice references after surrounding ASCII whitespace removal. It excludes self,
does not expose foreign-entity matches, never merges/rejects duplicates, and is
not an approved uniqueness policy. It appears on current saved load/save;
not a fuzzy identity resolver or exactly-once concurrent-create guarantee.

Reuses synthetic lab-only session/entity/access guard; normal URL/migration
graph and existing ERP permissions remain unchanged. Migration
`finance_allocation_lab.0007_supplier_invoice_drafts` is disposable-only, no
shared application. Staff/superuser status is not lab authority;403 clears
protected UI,400 preserves local work,409 requires confirmed reload. Unknown
write outcomes retain explicit inspect-list/reopen recovery; no durable
creation key/status or exactly-once guarantee. Missing saved references are
shown for correction, never replaced automatically.

New evidence:20 PostgreSQL16.15 tests (10 invoice +10 forecast),34 affected
frontend/helper tests (8 invoice +26 existing),2 actual authenticated
desktop/mobile UI/backend journeys and321 contrast pairs passed. Typecheck,
main/prototype build, Django, migration/schema and final diff checks passed.
Exact commands, names, initial failures/corrections and limits in the latest
`REVIEW_HANDOFF.md`; no unrelated full-suite claim.

Sources: [service](finance_allocation_lab/invoices.py),
[API](finance_allocation_lab/invoice_views.py),
[UI](src/FinanceInvoiceLab.tsx),
[PostgreSQL tests](finance_allocation_lab/test_invoices.py),
[rendered tests](src/finance-invoice-ui.test.tsx),
[native browser](scripts/finance-invoice-real-browser.test.mjs).

Operational AP supplier approval, bank verification, invoice issuance/import,
tax, matching/tolerances, payments/aging, credit/debit/advances, account mappings,
approval/posting rules and shared Finance access remain incomplete and require
their specific decisions. This batch does not activate any of them.
Shared-preview recovery assessment remains intact and separate.

## Cash-forecast scenario drafts — implemented in isolation

English-only `/lab-forecasts-ui/` is linked from the existing guarded `/lab-ui/`
synthetic facility, not a normal ERP Finance route or shared capability.
Named scenario, explicit proposed opening QAR balance/as-of date and manually
entered dated inflow/outflow rows/descriptions, optional existing active synthetic
BU and saved draft dimension values. No account is required or inferred.

Source rows remain in input order. Review presents ascending calendar date,
then original input position for equal dates. Same-day rows begin from the
opening as-of balance and are sequenced **for presentation only**: no intraday
timing, bank execution, payment priority or financial calendar is established.
Dates cannot precede the opening as-of date; equal-date entries are allowed.
Exact proposed inflows/outflows/net change/closing and per-row running balances
use decimal/BigInt-cent arithmetic, including valid negative balances. Negative
values are not approved overdrafts, credit facilities or actual cash.

Real disposable PostgreSQL register/save/reopen/edit, immutable complete
snapshots, row locks and expected-revision conflict recovery preserving local
work. Foreign/inactive references rejected; vanished choices require explicit
correction, never substitution. Each amount's absolute technical limit is
999999999999.99 with at most two decimals; opening can be signed, row amounts
are nonnegative and direction supplies their sign. Zero allowed. Derived totals
can exceed an individual input limit. Between1 and200 rows is a technical
worksheet bound, not a cash/approval limit.

Current unknown-save safeguards retained: an uncertain create blocks blind
retry/new replacement and requires explicit saved-list inspection/reopen.
No durable creation request identity, exactly-once guarantee or proof that a
momentarily empty list excludes a later commit. Persistence lasts only during
the disposable facility's lifetime. Not scheduled, approved, posted, actual cash,
bank integration, inferred receipt/payment timing, FX or financial records.

Sources: [service](finance_allocation_lab/forecasts.py),
[guarded API](finance_allocation_lab/forecast_views.py),
[UI/calculation](src/FinanceForecastLab.tsx),
[exact client arithmetic](src/finance-forecast.ts),
[PostgreSQL cases](finance_allocation_lab/test_forecasts.py),
[rendered/helper cases](src/finance-forecast-ui.test.tsx),
[real backend browser](scripts/finance-forecast-real-browser.test.mjs).
Lab-only migration `finance_allocation_lab.0006_cash_forecast_drafts` adds draft/
history tables and immutable-history protection, no default permissions; normal
migration graph excluded and no shared migration applied. New20 selected
PostgreSQL tests (10 forecast +10 asset),36 affected frontend/helper tests,
2 actual authenticated desktop/mobile journeys and298 contrast pairs passed;
exact commands and limits are in [handoff](REVIEW_HANDOFF.md).
Shared-preview recovery remains outstanding, separate and unchanged.

## Fixed-asset record drafts — implemented in isolation

English-only `/lab-assets-ui/` is linked from `/lab-ui/`, using only the existing
guarded explicit synthetic test session. No normal ERP route, shared Finance
permission, staff/superuser bypass or production chart of accounts is introduced.

- One proposed record at a time: explicit synthetic identifier/name/description,
  acquisition date, QAR cost and saved draft account/BU/dimensions, plus optional
  user-supplied reference/location text. All source text/Arabic content remains
  as supplied. Optional location is **not** a warehouse lookup or custody
  assignment. User identifiers are opaque proposed references; duplicate draft
  identifiers are allowed, not proof of a unique operational asset.
- Exact nonnegative two-decimal costs up to999999999999.99, with zero permitted
  in a draft. These are technical inputs, not capitalization thresholds or cash
  limits. Explicit dates have no assumed capitalization or recognition rule.
  Blank BU means not proposed, not an entity-wide allocation; current saved
  account/BU/dimension entity consistency is validated without automatic mapping.
- Real disposable PostgreSQL draft register/save/reopen/edit, immutable complete
  revision snapshots, locking and expected-revision conflict rejection preserving
  local work. Invalid inputs create no partial draft/revision. Confirmed reload
  is explicit. Current access denial clears protected data; a transient error
  does not erase the draft or turn an acknowledged save into an unknown outcome.
- Draft review is expressly **not approved, not capitalized, not posted**. No
  depreciation, useful life/residual defaults, thresholds, custody, approvals,
  disposal, real documents/imports, asset valuation or ledger posting.
- Unknown create outcomes block blind retry/new replacement until explicit
  saved-list inspection/reopen. There is no durable creation-request identity,
  exactly-once creation guarantee or status lookup; an empty list at one instant
  is not proof that a delayed create cannot commit. This limitation remains
  explicit, rather than presenting session state as durable data.

Sources: [validation/persistence](finance_allocation_lab/assets.py),
[guarded API](finance_allocation_lab/asset_views.py),
[UI](src/FinanceAssetLab.tsx),
[PostgreSQL cases](finance_allocation_lab/test_assets.py),
[rendered/helper cases](src/finance-asset-ui.test.tsx),
[real-backend browser](scripts/finance-asset-real-browser.test.mjs).
`finance_allocation_lab.0005_fixed_asset_drafts` is installed only in guarded
disposable lab settings, with no default permissions and immutable-history
database protection. No shared migration was applied. Persistence lasts for the
disposable facility's lifetime; no shared-team/production readiness is claimed.
Exact new evidence and the reproduced description-label/validation correction
are in [review handoff](REVIEW_HANDOFF.md). The shared-preview recovery assessment
remains outstanding, unchanged and separate.

## Explicit-input expense/petty-cash proposals — implemented in isolation

The English-only `/lab-expense-ui/` worksheet is linked from `/lab-ui/`;
neither is exposed by the normal ERP URL configuration. It reuses the existing
guarded explicit synthetic session and saved draft accounts/BU/dimensions, not
staff/superuser authority or a new Finance permission.

- Explicit `expense` or `petty_cash` type, named proposal and explanation;
  one to 100 independently entered date/reference/description/QAR-amount rows.
- Claimant/custodian and receipt references are synthetic text, not verified
  employees, evidence files, custody assignments or eligibility decisions.
  No receipt file input or upload endpoint. Missing reference/BU is informational;
  blank BU means **not proposed**, not an inferred entity-wide allocation.
- Exact nonnegative magnitudes up to 999999999999.99, max two decimals per row;
  zero is permitted in a draft. These are technical field constraints, not
  reimbursement/cash limits, tax conventions or authority. Repeated explicit
  rows are retained and included in the proposed total, not merged/deduplicated.
- The existing draft validator checks saved account/BU/dimension entity
  consistency and distinct dimension-value IDs. Distinct values belonging to
  the same dimension remain allowed; no new one-value-per-dimension policy.
- API save/list/reopen/edit with immutable complete revision snapshots;
  locked expected-revision updates reject conflicting edits without replacing
  local work. Explicit confirmed reload is required to discard that work.
  Invalid input makes no partial save. Uncertain writes block blind resave/new
  replacement until saved-list inspection/reopen; there is no exactly-once
  create/replay guarantee. Proposed references/IDs are not historical issued
  financial-document snapshots or approved operative account mappings.
- All reviews state **not approved, not paid, not posted**. No reimbursement,
  disbursement, actual bank access, scheduling, tax, import, journal posting,
  balancing entries, operational expenses or cash movements are produced.

Sources: [service and validation](finance_allocation_lab/expenses.py),
[guarded API](finance_allocation_lab/expense_views.py),
[UI](src/FinanceExpenseLab.tsx),
[PostgreSQL tests](finance_allocation_lab/test_expenses.py),
[rendered/helper tests](src/finance-expense-ui.test.tsx),
[real-backend browser journeys](scripts/finance-expense-real-browser.test.mjs).
The new `finance_allocation_lab.0004_expense_proposals` migration is lab-only,
absent from the normal migration graph, with no default permissions. It has
not been applied to shared data. Exact new verification, initial failures and
limits are recorded in [review handoff](REVIEW_HANDOFF.md).

Persistence is real PostgreSQL but lasts only for the disposable facility's
lifetime. Explicit test-session authentication is not ordinary Finance password
login or approved shared-user Finance authority. The existing
[shared-preview recovery assessment](FINANCE_PREVIEW_RECOVERY_ASSESSMENT.md)
remains outstanding and unchanged; this independent lab batch did not execute
a shared upgrade, restart or health check. No team/production readiness claim.

## Explicit-input allocation worksheet — implemented in isolation

The lab's **Allocation proposals** page is `/lab-allocation-ui/`, reached through
the existing draft lab link. An unauthenticated reviewer is directed to
`/lab-ui/` and its existing explicit synthetic test session; no new authentication
or shared Finance authority is introduced. After that session is opened, reviewers
can use the allocation page with saved synthetic draft account/dimension choices.
There is no seeding on entry. Empty choices link to the existing lab setup.

Reviewers enter source amount, **currency explicitly** (QAR only is supported),
proposed date, explanation and each destination's amount/account/optional BU/
dimensions. Dates are proposal dates, not recognition dates. Currency is not
defaulted and no FX is available. Exact source-minus-destinations review labels
remaining versus overallocated values. Over-allocation, invalid precision and
incompatible entity/choice references are rejected without partial saves.
Underallocated proposals can be saved as drafts with their remainder visible.

Journal lines are separately entered and reviewed under the existing minimum
two lines/one positive side/balance criteria. No account mapping or offset is
generated from destinations. Journal balance and complete allocation are separate
arithmetic checks, neither of which authorizes posting or accounting treatment.
The API can retain unbalanced journal lines for review; the UI requires balanced
journal review before save, consistent with the prior draft editor.

Save/reopen/edit uses disposable PostgreSQL, immutable revision snapshots,
row locking/expected revision conflicts, explicit reload confirmation and
uncertain-save resubmission protection. Supplied Arabic business titles/memos/
explanations survive edits, but all internal controls are English.

This batch uses the separate `finance_allocation_lab` app installed **only**
by `config.finance_lab_settings`, with its own disposable-only migration.
No foundation/shared migration was added or applied; normal installed apps and
their migration graph exclude the allocation schema. The existing shared-preview
recovery assessment and dependency are unchanged. Exact new checks, actual
browser scope and limits are recorded in REVIEW_HANDOFF.md. This is not shared
user readiness, operational allocation, team acceptance or Finance completion.

## Explicit-input budget scenarios — implemented in isolation

The distinct English worksheet `/lab-budget-ui/` is reached through **Open isolated
budget scenario worksheet** on `/lab-ui/`. It uses the existing explicitly opened
synthetic session and saved draft accounts/BU/dimension choices; it creates no
session/setup on entry. The backend is `/lab/budgets/` (GET/POST) and
`/lab/budgets/<id>/` (GET/PUT), present only in the guarded disposable URLConf.

Reviewers name a scenario, explicitly enter its start/end dates, and add/edit/
remove rows with their own start/end dates, QAR amounts, draft account, optional
same-entity BU/dimensions and explanation. QAR is fixed and visible; there is no
currency conversion. Each row must lie within its entered scenario bounds.
These bounds are not fiscal periods, recognition dates or ownership rules.
Amounts reuse the existing exact non-negative draft-money format/limit; zero
proposals are allowed. Exact totals count each row once, without classification,
netting, calendar distribution, allocation or comparison with actual data.

For the same account/BU/dimension set, identical date/amount combinations get a
duplicate advisory; intersecting inclusive date ranges otherwise get an overlap
advisory. Explanation differences remain preserved. Different BU/dimensions are
not collapsed. Warnings do **not** reject, merge or deduplicate entries or claim
company acceptance rules. Each row's full entered amount remains in the proposed
total; no prorating/period totals are inferred. A journal/balance gate does not
apply to this non-journal worksheet.

Save/reload/list/reopen/edit uses actual disposable PostgreSQL, immutable complete
revision snapshots, expected revisions and locking. Concurrent stale editors get
409 without lost updates; local work stays until explicit confirmed reload.
Network/HTTP5xx save uncertainty blocks blind resubmission/new replacement until
explicit saved-scenario recovery. Confirmed 403 clears protected state, including
when a list refresh fails after a committed save; unrelated failures retain work.
English controls preserve supplied Arabic explanation/name content.

`finance_allocation_lab.0002_budget_scenarios` creates only lab proposal/history
tables and a history immutability trigger. That app is installed only by guarded
lab settings and absent from the normal migration graph. No new foundation/shared
migration or shared upgrade/access/data change occurred; the recovery assessment
is unchanged. Operational report budgets, grants, records, actuals and posted
journals are unchanged. Exact current evidence is in REVIEW_HANDOFF.md: new
budget PostgreSQL 9/9, relevant Finance 59/59, rendered budget 9/9 plus 23 existing
affected UI tests, real-backend browser 10/10 including both budget widths, and
contrast 215/215. This is isolated implementation/verification, not shared-team
acceptance or Finance completion.

## Explicit-input reconciliation worksheet — implemented in isolation

Distinct English `/lab-reconciliation-ui/`, reached from `/lab-ui/` after the
existing explicit synthetic test session. Manually supplied statement and book
snapshot balances/dates and 0–100 rows per source; rows have a local proposal ID
(not a prescribed bank reference), date, QAR magnitude, explicitly chosen
increase/decrease direction, source reference and explanation. Signed balance
inputs are numbers, not authorization of overdrafts. Source text, supplied Arabic
content and source order are preserved; normalized decimal values remain exact.

Manual matches are bounded to one-to-one complete rows of equal exact amount
and equal direction. No source row can be used twice. Missing, duplicate,
incompatible or dangling proposals cannot save; explicit unmatch is required
before deleting a matched source. Different row dates/references are not inferred
to be a mismatch; no date window, tolerance, grouping, partial matching or netting
rule is invented. Existing sources/explanations remain intact across matching
and all prior saved revisions remain immutable.

Definitions shown in the UI: row net = increases minus decreases; balance
difference = statement snapshot minus book snapshot; unmatched-row difference =
unmatched statement net minus unmatched book net; comparison residual = balance
difference minus unmatched-row difference. Different balance dates trigger an
arithmetic-only advisory. Snapshot balances are not opening balances and row
sets are not assumed complete; zero residual is never operational reconciliation,
approval or posting. Empty/balance-only and zero rows are proposals, not real
transactions.

Actual isolated PostgreSQL save/list/reopen/edit uses atomic revisions, locking
and expected revisions. 409 retains local work until an explicit confirmed saved
version reload; uncertain writes block blind resubmission/new replacement.
Confirmed 403 clears protected inputs, including a post-commit list-refresh
denial; unrelated errors preserve local work. Entry/history is not session-only
persistence, but disposal of the lab database removes it.

Only guarded lab settings install the schema. Lab migration
`finance_allocation_lab.0003_reconciliation_proposals` adds proposal/history
tables, constraints and an immutable-history trigger; it has no dependency on
bank/operational account setup, and no shared migration/grant was added/applied.
No connections/imports, bank reconciliation flags, cash movement, adjustment,
journal posting, schedule, shared records or shared services were changed.
Exact final checks and actual browser scope are recorded in REVIEW_HANDOFF.md.
This is an isolated review prototype, not an operational bank reconciliation
system, shared-team acceptance or Finance completion.
