# TIV-ERP — business readiness and phased team-testing checklist

**Owner-confirmed language boundary:** internal ERP navigation/forms/errors/
dashboards/reports and isolated Finance editors are English only. Bilingual
English/Arabic applies to customer quotations and invoice documents, not ERP
controls. Supplied Arabic business/customer content and historical snapshots
remain intact. Apply this distinction to all section acceptance below.

## Daily recap: what can honestly be said

- **Latest Inventory register recovery (2026-10-20):** the removed-location
  selection display/recovery defect is corrected. No automatic scope widening;
  explicit clear/reselect retains dates and stale responses cannot restore old
  results. Fresh **23 focused / 333 full frontend, 8 PostgreSQL, 2 actual
  authenticated browser and 175 contrast checks passed**. Full frontend includes
  those 23; full backend was not repeated. See the latest handoff and the
  [started-section reconciliation](#started-section-acceptance-reconciliation-after-register-recovery)
  below for missing functionality versus verification-only limits. No section
  is declared complete or production-ready by these numbers.
- **Earlier Inventory recovery batch (2026-10-20):** capacity access/refresh
  defects were reproduced and corrected without changing permissions.
  Confirmed viewing loss removes protected measurements; EDIT-only loss
  retains independent VIEW, and temporary verification failures retain a
  clearly identified cached snapshot/draft. Stale responses and saved
  snapshots cannot overwrite newer state or local edits. Fresh **23 capacity
  + 50 related frontend, 23 focused PostgreSQL, 3 actual authenticated native
  browser and 175 contrast checks passed**; type/build/schema/system checks
  passed. These are relevant targeted checks, not freshly repeated full ERP
  suites or team acceptance. Capacity remains independently assigned per
  physical location across BU switches. No shared users/grants/data were changed.
  Subsequent register-filter recovery is recorded above, not still pending.
- **Earlier combined Retail/Delivery acceptance (2026-10-20):** the two-item quotation →
  simulated deposit/reservations → follow-up → preparation → readiness →
  full-payment handover journey passed with actual isolated backend/browser
  authentication at desktop and 320px. Exact per-line stock/consumption,
  failed-request rollback, unchanged-key retries, masked responses and scoped
  denials are verified. Fresh checks: **2 native browser, 11 focused PostgreSQL,
  159 relevant frontend and 175 contrast checks passed**, plus type/build and
  Django/migration checks. No runtime behavior changed; no shared team
  acceptance or fresh full-ERP test claim. See the latest
  [handoff](REVIEW_HANDOFF.md) and
  [combined team journey](RETAIL_TEAM_ACCEPTANCE.md#combined-multi-item-retail-to-delivery-review).
  The subsequent current-quotation access recovery batch reproduced and fixed
  stale displayed detail after confirmed read loss. Fresh **13 focused rendered
  cases (included in 305 full frontend), 3 real-backend browser, 20 focused
  PostgreSQL and 175 contrast checks passed**; action-only and transient cases
  retain authorized content/drafts. This is reactive verification, not push
  revocation or whole-section/team readiness. The subsequent warehouse-capacity
  recovery batch is recorded above; its fresh results are separate.
- **Implemented:** a configured, synthetic purchasing → reservation → whole-quote delivery journey, operational workspaces, non-posting returns, reporting/controlled exports, and supporting integrity/recovery foundations.
- **Technically verified:** the bounded journeys and permission boundaries described below. Navigation success and full-suite success do **not** establish full business-section completion.
- **Team acceptance:** Procurement and Retail/Delivery have repeatable acceptance instructions and isolated browser evidence. Recorded business-owner/team sign-off is **not established** by these documents.
- **Production readiness:** **not established for any section.** Synthetic accounting, unresolved policies/access boundaries, production identity/operations, migration and business acceptance remain separate gates.
- **Two decisions awaiting Samer:** whether temporary holds must block other sales; and whether operational procurement progress/supplier references/writes should remain accessible through the existing `create_order` boundary. Neither was changed by this review.
- **Subsequent implemented business slice:** read-only Retail collection follow-up now shows remaining simulated quote balances under existing quote authorization. Its actual implementation/evidence is in the latest handoff and extended Retail team checklist.
- **Provenance:** the original reconciliation was documentation-only. The subsequent, separately authorized collection-worklist implementation used isolated synthetic services/tests, not shared data. Historical counts below remain attributed to their original run.

### How to read readiness

Use these four statuses independently **for each workflow**, not as one section-wide green badge:

| Status | Meaning / required evidence |
| --- | --- |
| Prototype implementation complete | The bounded, configured workflow has working services and an appropriate UI. Denied/unconfigured states are intentional, not evidence of the happy path. |
| Technically verified | Identify the named test, real versus mocked backend, fixture assumptions, results and limitations. A route opening is navigation evidence only. |
| Awaiting team acceptance | An owner-prepared synthetic session, named reviewer, expected outcomes, observed results and feedback/sign-off are still needed where no such record exists. |
| Production readiness | Requires approved business/accounting/access policies, migration/cutover, production security and operations, recovery and workload acceptance. No prototype result below grants this status. |

## Evidence and requirement reconciliation

Sources have different purposes. The original architecture is a broad target, not proof that its proposed models or acceptance cases exist. Later owner-approved milestones and explicit clarifications narrow what has actually been implemented. Historical handoff entries preserve earlier evidence; use the newest applicable result, not a sum of successive suite totals.

| Source | What it establishes |
| --- | --- |
| [Architecture and proof specification](specs/Tivoli_ERP_Custom_Architecture_and_Proof_v0.1.md) §§3–6, 8 | Seven mandatory Phase 1 areas; Factory/project continuity; required transaction proofs and production/cutover gates. |
| [Technical foundation](specs/Tivoli_ERP_Technical_Foundation_v0.1.md) | Intended shared masters, atomic posting, invariant fixture, identity/quantity/money boundaries; proposed record names are not an implemented-model inventory. |
| [Purchasing-to-delivery specification](MILESTONE_3_PURCHASING_TO_DELIVERY.md), [reporting specification](MILESTONE_4_REPORTING_AND_EXPORTS.md) | Approved bounded synthetic workflows and reporting/export controls. |
| [Current roadmap](MILESTONE_ROADMAP.md), [review handoff](REVIEW_HANDOFF.md) | Implemented increments, exact historical commands/results, and outstanding decisions. |
| [Sidebar catalog](src/sidebar-destinations.ts), [destination matrix](ALL_SECTIONS_DESTINATION_MATRIX.md), [function-page contract](WORKSPACE_FUNCTION_PAGES.md) | Nine sidebar sections and actual focused destinations. They prove arrangement, not business completion. |
| [API routes](foundation/urls.py), [models](foundation/models.py), [purchasing API](foundation/purchasing_views.py), [services](foundation/purchasing_services.py) | Actual available records/actions and authorization paths, rather than intended model names. |
| [Current frontend](src/App.tsx), [purchasing registers](src/PurchasingReadside.tsx), [reports](src/ReportingArea.tsx), [personal views](src/PersonalReportingWorkspace.tsx) | Rendered workspaces, reused functions, restrictions and saved presentation behavior. |

**Documentation conflicts to avoid repeating:** the original `replit.md` still describes a Milestone-1-only starting authorization, while subsequent owner requests and the roadmap authorize later increments. The roadmap's preserved early sidebar entry describes anchor/state navigation; its newer navigation correction and the current catalog/matrix describe focused fragment destinations. Neither old wording is evidence that later work is absent. Conversely, the roadmap's “implemented” labels explicitly exclude unfinished business and production scope.

### Earlier sensitive-data review — historical results

The [bounded cross-section sensitive-data review](REVIEW_HANDOFF.md#bounded-cross-section-sensitive-data-review) records:

| Check | Recorded result |
| --- | --- |
| New disclosure regressions: `RETAIL_BACKEND_TEST=foundation.test_sensitive_surfaces bash scripts/verify-retail-acceptance.sh` | **6 passed, 0 failed**, disposable PostgreSQL **16.15** |
| `bash scripts/verify-retail-acceptance.sh` browser portion | **4/4 real-backend Chromium scenarios passed**; that invocation subsequently failed on obsolete receipt-cost test assertions, so the whole invocation was not successful |
| Final backend: `RETAIL_SKIP_BROWSER=1 bash scripts/verify-retail-acceptance.sh` | **32 focused passed / 288 full backend passed**, 0 failures; 5.743s / 117.372s |
| `npm test` | **252 passed, 0 failed**, 18 test processes |
| `npm run test:contrast` | **116 passed, 0 failed** |
| `npm run typecheck`; `npm run build` | Passed; existing 511.82 kB JavaScript chunk advisory remains |
| `python manage.py check --database default` | 0 issues |
| `python manage.py migrate --check`; `python manage.py makemigrations --check --dry-run` | Passed / no changes detected, in the isolated test environment |
| `git diff --check` in that implementation batch | Passed |

These are historical implementation checks from the sensitive-data review,
**not fresh checks for this batch**. Current register-recovery results are above
and in REVIEW_HANDOFF. The corrected historical assertions check unchanged
private allocation calculations while requiring public receipt responses to
mask costs. Those successful browser scenarios remained valid because subsequent
edits changed test expectations, not application behavior.

Earlier isolated navigation acceptance covered all **41 sidebar child destinations** with **3 real-backend browser scenarios**. The latest four-scenario Retail/security run included **31 focused destinations across six sections**, not a fresh rerun of every child. Earlier keyboard/mobile checks used mocked APIs and remain separate evidence. No authenticated shared-preview acceptance is claimed.

## Section-by-section business checklist

### Overview, business-unit context and Favorites / My Page

**Implemented:** authenticated organization/location hierarchy, authorized BU selection, per-BU themes, user-specific section and function Favorites, and My Page. Focused child navigation, history/direct links, context invalidation, draft-discard warning and pending-submission departure protection exist. Favorites are navigation only.

**Evidence:** [destination matrix](ALL_SECTIONS_DESTINATION_MATRIX.md); `foundation/test_function_favorites.py`, `foundation/test_favorites.py`; `scripts/real-navigation-browser.test.mjs`, `scripts/navigation-safety-ui.test.tsx`, `scripts/overview-location-tree-ui.test.tsx`, `scripts/stock-context-ui.test.tsx`. Earlier real-backend tests checked fresh migration and existing-fixture Favorites `0018 → 0019` compatibility. Current full suites include the regression modules.

**Teams can test:** sign-in in an owner-prepared isolated session, allowed/denied BU choices, hierarchy replacement, theme consistency, pin/reload/unpin, child pages, back/forward and safe draft departure.

**Not complete / dependencies:** not a company-wide identity/user-management system; not evidence of real employee assignments or production SSO/session readiness. No current shared migration/HTTP check was performed for this recap.

### 1. Retail & Sales

**Current identity clarification:** the owner has confirmed **Tivoli Group
L.L.C** as the registered company and **Tivoli** as the trademark/logo name.
New quotation pages separate those identities; historical snapshots remain
unchanged. No Arabic registered name is inferred. This is a presentation
correction, not invoice implementation or whole-section acceptance. Retail
still awaits approved T&C/contact/Arabic content, output-right decisions and
the separately pending hold/access/Finance questions.

**Implemented:** authorized quote register/detail; stock and configured special-order quote creation; item/variant specifications and approved selling-price snapshots; discount requests/decisions; temporary intent holds; simulated collections and deposit-based reservations; delivery/readiness links. Discount ceilings are Salesperson 10%, Manager 20%, Commercial GM 30%, CEO exceptional 50%. Holds have the existing three-working-day calendar limit; reservations require the existing minimum 50% non-returnable deposit. Ordinary stock delivery requires full payment; explicitly approved deferred terms remain separate.

**Evidence:** [Retail scope](RETAIL_SALES_WORKBENCH.md); `foundation/test_retail_operations.py`, `foundation/test_purchasing_workflows.py`, `foundation/test_sensitive_surfaces.py`, `foundation/test_quotation_choices.py`; `scripts/retail-workbench-ui.test.tsx`, `scripts/purchasing-quote-lines-ui.test.tsx`, `scripts/retail-real-browser.test.mjs`. The latest 10 real-backend browser scenarios cover permitted discount, Manager approval/denial, hold recording, lost-ack collection replay, readiness/handover, ordinary masking, BU switch/revocation, multiline decimal/retry/rollback and nonstaff bilingual quoting. Final responsive changes also passed both targeted desktop/mobile scenarios. They do not cover every approval role/exception in a browser; see the latest handoff for distinct run phases.

**Teams can test now:** [Retail team acceptance checklist](RETAIL_TEAM_ACCEPTANCE.md), including its quotation and [Manager/final-handover continuation](RETAIL_TEAM_ACCEPTANCE.md#continue-manager-decision-and-final-handover). The owner's subsequent approval now permits configured nonstaff sales to select minimal quotation choices without full staff setup access. Use existing fictional authorized personas in a disposable session, not new shared assignments. Verify source prices stay unchanged, above-grade sales submissions are denied, and replay does not duplicate facts.

**Missing / dependencies:** no general customer-management, invoicing, credit control or accounting receivables workspace. A separate Collection follow-up view now supplies scoped paid/remaining simulated quote values; it does not turn the quote register into accounting receivables. Special-order value bands stay unconfigured; no thresholds may be invented. Hold blocking awaits Samer. Full business/team acceptance remains unevidenced.

### 2. Procurement & Imports

**Implemented:** multi-line synthetic purchase commitments; independently ordered/received/remaining quantities; successive partial receipts; explicit CSV/XLSX worksheet/column/item/commitment-line review and warehouse enrichment; local receipt draft; separately authorized receipt submission; purchase-value landed-charge allocation; receipt/charge registers and staff counterparty directory. Upload/review alone does not post anything. Commitment is the implemented synthetic record, not proof of a complete commercial PO/invoice system.

**Evidence:** [Procurement scope](PROCUREMENT_IMPORTS_WORKSPACE.md), [packing receipt preparation](PACKING_LIST_RECEIPT_PREPARATION.md); `foundation/test_procurement_workspace.py`, `foundation/test_purchasing_workflows.py`, `foundation/test_packing_receipt_candidates.py`; `scripts/procurement-workspace-ui.test.tsx`. Earlier **2 real-backend browser tests** covered actual CSV/multi-worksheet XLSX upload → reviewed partial receipts, exact progress/ledger quantities, lost-ack retry and over-receipt rollback. Its **34 focused backend** result is historical. The current 288-test suite covers updated API assertions; the latest browser review replayed a receipt, but did not repeat the full upload journey after the masking fix.

**Teams can test now:** [Procurement team acceptance checklist](PROCUREMENT_TEAM_ACCEPTANCE.md). Use small synthetic samples and an owner-prepared isolated receiving session. Upload/review must leave business records unchanged; explicit receipts update selected lines once; any over-receipt fails atomically. Test both masked and permitted costs using established test personas only.

**Missing / dependencies:** foreign currency, supplier invoicing, shipment/customs management, unit conversion, ambiguous catalog ownership/matching, and late-cost valuation are not implemented as full workflows. Nonstaff `create_order` currently permits operational procurement reads/writes under existing BU, approval-role and configuration checks; the staff-only directory is not a backend procurement gate. Supplier references/history classification and intended procurement access await Samer. Do not change grants or treat denied screens as configuration to bypass.

### 3. Inventory & Warehouses

**Implemented:** GET-only `StockBalance` quantity projection with exact decimal-string **unreserved quantity**, scoped search including location code, location filter/detail, and existing identity metadata. It never combines/falls back to illustrative `StockExample` quantities. Separate authorized inventory/reservation/transfer registers; shared receiving/preparation entry points; per-physical-location capacity measurements, available floor area and independent assignment/audit foundation. m² and m³ remain separate.

**Evidence:** [Inventory scope](INVENTORY_WAREHOUSE_WORKSPACE.md); `foundation/test_stock_ledger.py`, `foundation/test_inventory_workspace.py`, `foundation/test_warehouse_capacity.py`; `scripts/inventory-workspace-ui.test.tsx`, `scripts/stock-context-ui.test.tsx`, `scripts/warehouse-capacity-ui.test.tsx`. Recorded ledger tests cover receipt/reservation/release/delivery/transfer projection, no double-counting, exact field allowlists, scoped pagination and no GET writes; context regressions cover stale/failed refreshes. These modules are within the latest full suites; earlier native workspace checks are not a fresh warehouse end-to-end team acceptance.

**Teams can test now:** quantity changes after isolated approved receipts/reservations/handover; search/location denial; stale-response protection; separately assigned capacity viewing/edit validation; ordinary-stock-reader capacity denial. Capacity access is per explicitly assigned physical location, not inferred from BU, staff or stock access.

**Missing / dependencies:** no exposed transfer-posting workflow, stock-count/adjustment journey, automatic put-away/capacity allocation, established barcode values/formats or scanner-hardware acceptance. Plain item/location search is not barcode parsing. Catalog matching is limited to authorized ledger identities; unmatched does not prove absence from the company catalog. Broad catalog/BU ownership, transfer/count authorization and adjustment/valuation policy must be confirmed before those writes. Do not convert floor area into volume.

### 4. Delivery & After-Sales

**Implemented:** authorized quote lookup and whole-quote progress; readiness record; existing payment/exception gates; whole-quote final handover; delivery/storage/reservation registers under their independent scopes; readiness/storage dry-run notification intents; storage accrual and existing approved exceptions. A later valid deferred-balance collection appends payment/audit facts without resetting delivered status/timestamps or requiring a second handover.

**Subsequent bounded increment:** Delivery picking preparation is a distinct
read-only per-line review view with independent stock-identity gating and
browser-local reviewed marks, not picking execution. Favorites migration 0021
has now been rehearsed and applied to the verified synthetic demo after a
recoverable backup proof. Current shared quotes and approval-role assignments
remain zero; owner/team positive acceptance still needs the isolated configured
session. See the latest handoff and P1–P7 in the Retail/Delivery checklist.

**Returns implemented:** non-posting intake and inspection, originating sale/items/customer, condition, showroom receipt or warehouse collection details, proposed cash/card preference, and pending Finance status. Configurable default **14 calendar days from actual delivery**, inclusive deadline and stored assessment are enforced. This is the owner-confirmed company rule, **not a verified statement of Qatar law**.

**Evidence:** [Delivery scope](DELIVERY_AFTER_SALES_WORKBENCH.md), [return scope](SYNTHETIC_SALES_RETURN_INTAKE.md); `foundation/test_delivery_operations.py`, `foundation/test_sales_returns.py`, `foundation/test_sales_return_concurrency.py`, `foundation/test_purchasing_workflows.py`; `scripts/delivery-workbench-ui.test.tsx`, `scripts/sales-return-ui.test.tsx`. Recorded independent-connection tests cover inspection/intake concurrency, retry/conflict and revocation without posting. Rendered coverage verifies post-delivery collection refresh and preserved delivery state. Current real-backend browser evidence verifies Manager/full-payment/final-handover and replay, **not a fresh real-backend browser run of every return/deferred-exception case**.

**Teams can test now:** [Retail/Delivery handover checklist](RETAIL_TEAM_ACCEPTANCE.md#continue-manager-decision-and-final-handover), delivered/remaining quantities, repeated acknowledgement without duplicate stock changes, and non-posting return inspection in isolated configured fixtures. A Delivery register may correctly deny a quote-authorized actor lacking separate SALES panel/data grants; do not add grants to force a pass.

**Missing / dependencies:** picking execution, partial delivery, transport/installation/service scheduling are not supplied by a read-progress screen. Partial quantities need approved allocation of payments, storage/readiness and return-date treatment. Refund/credit-note approval authority, failed-condition disposition, exceptions and stock/valuation reversals remain unapproved/unimplemented. No real notification channel/recipient/wording/schedule or external delivery exists. Hold blocking remains unchanged.

### 5. Finance & Accounting

**Implemented foundation only:** immutable synthetic stock/journal postings, decimal reconciliation, balanced QAR fixture, atomic rollback/idempotency, private costing, and read-only diagnostics. Synthetic reports/budgets are not an operational Finance workspace.

**New isolated draft foundation (2026-10-20):** separate synthetic draft
accounts, proposed dimensions/values, optional same-entity BU dimensions and
atomic draft journals with read-only exact balance review. A standalone
English-interface editor supports local-session setup and journal review without
network requests. The shared Finance placeholder is not activated: no Finance
capability exists, so no staff/superuser/report/purchasing grant is reused.
See the [complete Finance matrix and local-only review instructions](FINANCE_REQUIREMENTS_RECONCILIATION.md).
Backend persistence is restricted to explicitly enabled PostgreSQL test DBs.
The separate disposable runner now connects the English-interface editor to real
save/list/reopen/edit APIs and immutable revision history, with stale-edit
conflict recovery. Desktop/mobile browser acceptance used a real Django
synthetic test session and backend; not ordinary/shared Finance authorization.
The offline static editor remains explicitly session-only. Migrations 0023/0024
were applied only to disposable environments by these batches.

**Evidence:** `foundation/test_transactions.py`, `foundation/test_journal_verification.py`, `foundation/test_sensitive_surfaces.py`; [test-only accounting assumptions](MILESTONE_2_TEST_ONLY_ASSUMPTIONS.md). The established fixture receives 10 chairs for QAR 10,000 plus QAR 2,000 eligible landed charges; two delivered yield QAR 4,000 revenue, QAR 2,400 COGS and QAR 9,600 remaining inventory under that fixture's assumptions only.

**Teams can test now:** Finance can review synthetic arithmetic, preserved private facts, report masking and exceptions/questions. This is review evidence, not authority to post real transactions.

**Missing / dependencies:** Finance sidebar remains unfinished. No accepted company chart/mappings, tax/FX, supplier invoices/AP/AR, bank/cash reconciliation, period controls, correction/reversal process, late-cost-after-sale handling or production valuation/recognition policy. Finance must approve these; payroll/legal and real financial data remain disconnected.

**Recurring-template proposals now implemented in isolation:** explicitly entered
dates/range and common amounts/account/dimension lines, saved/reopened/edited
with revisions/conflict recovery, and read-only proposed-occurrence preview.
No frequency generator, scheduler, recognition/approval or posted entries.
New evidence: 9 focused / 355 full PostgreSQL tests, 346 frontend tests and
2 expanded real-backend desktop/mobile journeys; exact layers/limits in the
[handoff](REVIEW_HANDOFF.md#isolated-recurring-journal-template-proposals--2026-10-20).
Not available to shared users or team-accepted. Migration 0025 was not applied
to shared data by this batch.

**Next independent work:** isolated, non-posting opening-balance worksheet
proposals with explicit as-of date and amounts; no actual import or activation.
Shared exposure requires an approved Finance action/scope and deliberate
assignment; production chart/posting/period/tax/FX/valuation/approval rules remain
separate decisions. The draft prototype is not Finance section completion.

### 6. HR & Payroll

**Implemented:** honest planned/unavailable sidebar only; shared authentication does not constitute employee records or payroll access.

**Evidence:** current sidebar/App and destination matrix verify placeholder behavior, not HR calculations. There is no HR/payroll workflow acceptance evidence.

**Teams can test now:** confirm it is clearly unavailable and does not expose personnel data. No payroll or legal scenario should be invented for a demo.

**Missing / dependencies:** employee/grade records, attendance, leave, approved calculations, payroll review/posting and dedicated personnel access. Need owner/HR-approved data scope, rules, authority, privacy and applicable legal validation before a meaningful section batch.

### 7. Governance & Administration

**Implemented:** staff-controlled built-in/custom palettes, name/hex/picker/preview/contrast validation and persisted BU assignment; existing report-manager panel assignment/revocation; superuser-only per-location capacity designation/access management and metadata audit; staff read-only BU purchasing configuration. Tivoli orange is an accessible substitute, not a claimed exact logo-asset match. Independent staff/report-manager/superuser gates are preserved.

**Evidence:** [Governance scope](GOVERNANCE_ADMINISTRATION_WORKSPACE.md); `foundation/test_business_unit_palettes.py`, `foundation/test_governance_workspace.py`, `foundation/test_warehouse_capacity.py`, `foundation/test_reporting.py`; `scripts/governance-workspace-ui.test.tsx`, `scripts/reporting-workspace-ui.test.tsx`. Earlier workspace/browser checks plus the current full suites verify existing boundaries; the ordinary-persona browser review is not a new positive test of every privileged screen.

**Teams can test now:** configured fictional administrators can review authorized palette, panel-only assignment/revocation and capacity administration in isolated sessions; ordinary staff must be denied superuser controls. Capacity audit returns event metadata, not before/after measurements. Panel assignment never grants data access.

**Missing / dependencies:** no broad user/grant administration workspace, new delegation/DOFA/TOS policy/version lifecycle or production administrator approval process. The original “ERP/system administrator” capacity foundation is currently implemented as Django superuser; it is not an invitation to grant real users superuser status. Real role/authority mapping remains an owner decision.

### 8. Reports & Dashboards / controlled exports

**Implemented:** operational sales and supported cost/expense, best-selling item/category, gross-margin, budget-versus-actual, inventory/history panels; existing daily/monthly/quarterly aggregation; authorized BU/location and single-company scopes; managing-partner/report-manager panel assignment/revocation; personal named views with ordered assigned panels and filters. Every query/export rechecks data permissions. Full authorized-result Excel/Word exports are audited and are not restricted to the visible page.

**Source separation:** operational rows/counts/summaries/trends/pagination/exports use transactional `SalesReportLine` only. `DemoReportSalesFact` belongs only in the separately labeled **“Synthetic examples — not operational sales”** area, with independent counts/summary; it is not operational sales history.

**Evidence:** [reporting specification](MILESTONE_4_REPORTING_AND_EXPORTS.md); `foundation/test_reporting.py`, `foundation/test_reporting_calendar.py`, `foundation/test_personal_reporting.py`, `foundation/test_sales_report_examples.py`, `foundation/test_sensitive_surfaces.py`; `scripts/reporting-workspace-ui.test.tsx`. Recorded boundary tests exercise existing date/timestamp semantics; latest disclosure tests verify full-result XLSX/DOCX content, masking, export authority/revocation and audit.

**Teams can test now:** assigned fictional users can change date/BU/location scope, compare operational versus illustrative examples, save/reload personal views, and exercise allowed/denied full exports. A report manager can assign/revoke panels without gaining/granting costs or underlying rows.

**Missing / dependencies:** no shared-dashboard ownership workflow, newly defined location-expense attribution, multi-entity FX conversion/eliminations, approved fiscal calendar or budget ownership. Role/user-target report assignment is not general role/grant administration. Earlier local XLSX-reader compatibility is recorded; latest tests parsed ZIP/XML, not Microsoft Office/LibreOffice rendering. Do not call all reports production-accounting approved or treat test fixture budgets as accepted budgets.

### 9. Purchasing Workbench

**Implemented:** focused quote lookup, transaction and record views using the same approved services as Retail/Procurement/Delivery—not a second purchasing engine. Per-action pending state, action/quote-identifiable outcomes, stale BU/navigation/config guards, unchanged-key replay, input validation and unavailable states are implemented.

**Evidence:** `foundation/test_purchasing_workflows.py`, `foundation/test_sensitive_surfaces.py`; `scripts/purchasing-transaction-context-ui.test.tsx`, `scripts/purchasing-quote-context-ui.test.tsx`, `scripts/purchasing-governance-context-ui.test.tsx`, `scripts/purchasing-counterparty-context-ui.test.tsx`, plus the current real-backend review.

**Teams can test now:** their authorized functions, denial/configuration states, unchanged request retries and non-destructive navigation. Use the section-specific checklists rather than inventing a second business acceptance process.

**Missing / dependencies:** same business gaps as the shared services; no added capabilities follow from this route. Latest response/replay masking fixed a real receipt disclosure, but the operational procurement access question remains open.

## Roadmap scope beyond the current sidebar

| Area | Actual implementation versus requirement | What can be reviewed now / what blocks business completion |
| --- | --- | --- |
| Factory | Architecture requires existing Factory PO/SO and budget continuity through core functions or explicit Dynamics coexistence. Generic commitments, quotes and BU budgets do **not** prove Factory-specific order/job/budget coverage. No dedicated Factory workflow/UI is evidenced in the current catalog/models/routes. | Inventory current process/source identifiers and continuity requirements without real data. Need the accepted Factory workflow, responsibility/access mapping, budget treatment and Dynamics coexistence/cutover boundary. Do not invent BOM, routing, production costing or approvals from a heading. |
| Projects / Fit-out | Later specialist workspace; existing project PO/SO/budget continuity remains a core requirement, not silently deferred away. Current retail orders and company/BU budgets are not project commitments/actuals/payment reconciliation. No dedicated project model/workspace is evidenced. | Establish confirmed project identifiers/workflow and system-of-record ownership. Need budget/commitment/actual/payment definitions, dimensions and authority before financial or operational project execution. |
| CRM / Marketing | Explicitly later full module. Synthetic trading parties and quote customer references exist; these are not CRM contacts/leads/opportunities/campaigns or customer-service management. | Review synthetic cross-references only. Need lifecycle, visibility/ownership, permitted personal data, consent/communication and integration rules. No real outreach or new customer-data scope is approved here. |
| Products / Brands and shared masters | Brand/item/variant identities, specifications/prices on quotes, and scoped ledger catalog matching support core flows. Full master-catalog maintenance, barcode/UOM governance and historical specification administration are not established. | Existing scoped item search/mapping is testable. Need catalog-to-BU membership, edit authority, UOM conversions and barcode source/format before broader master administration. |
| Operations, portability, backup and migration | Local clean restore and synthetic load foundations; stock/transfer/journal diagnostics, combined integrity monitor, outbox health and local backup evidence summary exist. These are supporting proofs, not the next business section. | Preserve existing evidence. Integrity checks do not repair data; bundle verification does not prove restore recoverability; 8,000-item/40-worker measurements are not an SLA or proof of 40 simultaneous DB queries. Production provider/region, keys, backup location/RPO/RTO, support, migration and controlled Dynamics coexistence need approval. No new monitoring utility is recommended over business workflow work. |

### Original proof requirements: do not lose these unfinished exits

| Architecture acceptance scenario | Reconciled result |
| --- | --- |
| Receipt → eligible landed allocation → sale arithmetic; decimal rounding; atomic failures | Implemented and tested under explicit synthetic QAR assumptions, not company accounting approval. |
| Last-unit concurrency, duplicate requests, retry conflicts and rollback | Recorded independent PostgreSQL connection tests and action-specific replay regressions. Not a claim that every business action has browser concurrency coverage. |
| Partial receipt and reconciliation | Implemented per commitment line, tested across multiple receipts and actual browser upload/submission. |
| Returns/reversals | Intake/inspection and non-posting Finance-pending review only; financial/inventory reversal acceptance remains open. |
| Late landed cost after sale, FX and closed-period correction | Not implemented as approved company workflows; Finance decisions required. |
| Permission isolation including direct reads/exports | Broad scoped regression evidence and bounded cross-section review; known procurement classification question remains. Attachment access is not proven merely by local packing-file parsing. |
| Restore and cutover | Local synthetic restore proof exists; production recovery, real migration and transaction-class cutover/parallel reconciliation are not accepted. |

## Collection follow-up — recommended slice subsequently implemented

### Retail collection follow-up worklist

**Existing requirement served:** usable quote/order collections and approved balance-after-delivery workflow. The earlier gap was the absence of a paid/remaining worklist; the separate `#/retail/collections` view now closes that bounded gap.

**Code evidence:** `SalesQuoteCreateView.get` in [purchasing_views.py](foundation/purchasing_views.py) still returns its unchanged quote-register contract. `_quote_summary` exposes authorized per-quote payment facts; `record_simulated_payment` in [purchasing_services.py](foundation/purchasing_services.py) uses DEPOSIT/BALANCE totals separately from storage. The new [collection API](foundation/collection_views.py) and [focused view](src/RetailCollections.tsx) reuse those semantics without modifying payment services.

This bounded slice was subsequently explicitly authorized and implemented.
See the newest handoff for actual checks, rather than treating the earlier
recommendation/effort estimate as verification.

**Implemented scope and acceptance checks:**

1. Add a focused read-only list using the **same existing quote authorization, BU/location restrictions and pagination**. Derive paid/remaining decimal strings server-side from the existing quote/payment classification; no stored balance cache or new business records.
2. Keep quote collections separate from storage payments. Label remaining value as an operational simulated quote balance, **not an accounting receivable, overdue amount or authority to collect**. Do not invent due dates, aging, credit limits, refund offsets or new eligibility.
3. Show quote identity, status, quote value, recorded quote collections and remaining quote value; clearly distinguish already-delivered quotes. Link to the existing selected-quote collection view, without enabling a disabled action or bypassing current payment/approval gates.
4. Do not include costs, supplier/history details, full customer profiles or company-wide totals. Denied/unconfigured contexts remain explicit; do not add report/data grants, staff exposure or real assignments.
5. Test no/partial/full payment, approved deferred balance after delivery, and exclusion of storage collections; preserve delivered state and exact retry behavior. Cover deterministic paging, denied/revoked BU/location scope, rapid context changes, decimal strings, no GET writes, and one isolated real-backend list → existing collection → refreshed remaining value journey.

**Why it need not wait:** it uses already-approved recorded quote/payment facts and collection semantics, not a new Finance receivable, new payment term, procurement access classification or hold-availability rule. Any request to redefine the existing payment classification or expose additional customer/financial data must be excluded and brought back for approval.

**Historical estimate, not a measured duration or next-task promise:** the
original recommendation estimated 2–4 focused engineering workdays under its
stated tooling/scope assumptions. The worklist is now implemented; use the
recorded outcomes, not that estimate, to assess readiness. It does not complete
Retail, Finance or production acceptance.

Read-only picking preparation is now implemented as the bounded Delivery child
`#/delivery/preparation`; see `DELIVERY_AFTER_SALES_WORKBENCH.md` and the latest
handoff. Quote reads retain existing quote rights; optional identities require
separate existing stock/BU/location authorization. Browser-local reviewed marks
are not picking confirmation. Actual picking/dispatch, partial delivery and
warehouse writes remain unimplemented and must not be fabricated from this UI.

### Multi-line quotation entry — implemented; team acceptance pending

The existing Retail/Purchasing form now submits **one quote with 1–40 lines**
through the unchanged `create_sales_quote` contract. Users can add, edit and
remove any line while keeping at least one, select existing authorized price
variants/stock locations, and enter separate quantities/specifications. Removing
the first line preserves its successor's values and the remaining order.
Per-line price/quantity/specification review and decimal-safe estimated totals
precede submission; the returned server snapshots remain authoritative.

Isolated PostgreSQL, rendered and real-backend browser evidence is recorded in
the latest `REVIEW_HANDOFF.md` entry. It covers independent line facts, exact
rounding, replay, rejected later-line rollback, missing prices, authorization,
BU switching, ordinary-user masking and native unsaved-work protection.
The quote still has a **single existing kind**: stock or special order; mixed
commercial terms/partial delivery are not introduced. See the multi-line section
of `RETAIL_TEAM_ACCEPTANCE.md` for the bounded team exercise.

**Status:** prototype implementation and bounded isolated technical verification
complete; team acceptance and production readiness are not claimed. Shared-demo
records/accounts/grants were not changed or seeded. The original multi-line
batch needed no schema migration; the subsequent approved bilingual snapshot
batch adds optional metadata through migration 0022, tested only in disposable
databases. Nonstaff sales now receive minimal quotation-only choices under
existing BU/role grants; full staff configuration/directory access remains
denied. Physical picking/
dispatch, partial delivery, refunds/credit notes and pending hold/procurement
decisions remain separate blocked requirements.

## Phased team-testing plan

## Started-section acceptance reconciliation after register recovery

Sources: current [roadmap](MILESTONE_ROADMAP.md), section specifications linked
above, the actual shared components/routes and their existing tests, and the
fresh register evidence in [REVIEW_HANDOFF](REVIEW_HANDOFF.md). This is a
reconciliation, **not a fresh exhaustive browser audit of every section**.
Statuses below distinguish implemented prototype scope, technical evidence,
team acceptance and missing business functionality.

| Started section | Usable implemented journey / current status | Concrete missing user-facing functionality and exact dependency | Verification-only or team-acceptance gap (not a missing feature) |
| --- | --- | --- | --- |
| Retail & Sales | Multi-item configured stock/special-order quotes, fixed approved prices/grade discounts, bilingual document preview/legal identity, intent holds, simulated collections/reservations, follow-up and delivery links. Bounded prototype journeys technically verified; team sign-off not recorded. [Pilot review guide (English/Arabic)](RETAIL_PILOT_REVIEW_GUIDE.md). | Operative T&C/contact/Arabic content requires supplied approved content; print/download/export entitlement needs owner decision. Invoice/credit/AR workflows require Finance. Whether holds block stock remains pending. General customer/item administration is not supplied by minimal quotation choices and requires its own agreed scope/authority. | Existing real-backend multiline/payment/handover evidence is recorded, not freshly repeated today. Every Commercial GM/CEO/special-order exception and every long bilingual document configuration is not natively browser-verified. These are not grounds to invent new terms or duplicate registers. |
| Inventory & Warehouses | Ledger-only quantities/location search/detail, reviewed receiving links, independent physical-location capacity/access/audit, authorized read-only inventory/transfer registers. Capacity recovery and this register recovery are completed bounded repairs. | Transfer execution UI/API authority, stock-count/adjustment workflow and allocation/put-away rules are not implemented as approved operations. Need actor/source/destination/approval authority and any adjustment/valuation rules. Barcode identities/formats/hardware and broad catalog/UOM membership remain unconfirmed. Current search is not a scanner protocol or picking/dispatch. | Fresh native scope removal/clear/reselect and previous capacity acceptance exist; not one exhaustive physical warehouse journey or hardware/team acceptance. Out-of-order cases are rendered mocks. No further independently authorized missing substantive function was confirmed in this pass. |
| Procurement & Imports | Multi-line commitments, successive reviewed partial receipts, CSV/XLSX mapping and explicit submission, line progress/replay/rollback, eligible landed allocation and masked registers. Implemented configured prototype path; team feedback/sign-off pending. | Operational procurement/supplier-reference authority remains awaiting Samer. Unit conversions/ambiguous catalog ownership need approved masters/rules. Supplier invoices, FX, late-cost valuation and new shipment/customs lifecycles remain absent/dependent, not implied by the existing receipt screen. | Earlier actual upload-to-receipt cases are documented; not freshly repeated today or a proof of every supplier template. Real supplier files/production data are not needed or authorized for team review. |
| Delivery & After-Sales | Whole-quote readiness/full-payment or existing approved exception gates/handover/replay, post-delivery collection, read-only picking preparation, storage operations and non-posting delivery-based 14-day return intake/inspection. Bounded supported journeys technically verified. | Physical pick confirmation, allocation, dispatch/service scheduling and partial-delivery execution are not implemented. Partial allocation/payment/storage/date rules need approval. Refund/credit-note authority, failed-condition disposition and stock/accounting reversals remain unapproved. Real notification recipients/channels/wording need approval. | Existing handover and return API/concurrency evidence is separate from human team sign-off. Not every return/deferred-exception scenario has real-backend browser evidence. Local “reviewed” marks are not physical picking. |
| Governance & Administration | Existing staff palette, report-manager panel assignments, superuser location-capacity assignments/metadata audit and read-only purchasing configuration are arranged and tested under separate gates. | Broader user/grant administration and delegation/DOFA rules need approved authority/lifecycle; no implicit new powers. Purchasing configuration review is not permission to populate real assignments, price bands or policies. | Isolated configured-role evidence is not actual-user assignment approval or an exhaustive native administrator journey. No real users should receive grants merely to make a team test pass. |
| Reports & Dashboards / controlled exports | Assigned panels, operational-only sales, separate illustrative examples, existing date aggregation, masking/scopes, audited full authorized Excel/Word results and personal views are implemented. | Shared dashboard ownership, location-expense attribution, fiscal/budget ownership and multi-entity FX/eliminations need definitions; not inferred from personal views or test budgets. | Human report acceptance and native Office/LibreOffice visual interoperability remain separate from service/ZIP/XML/installed-reader evidence. Do not reopen completed report implementation solely because some operator validation remains. |
| Purchasing Workbench | Shared approved services/forms, focused quote/actions/records and current context/retry guards, not a separate transaction engine. | Same dependencies as Retail/Procurement/Delivery, not a reason to create another register or alternate policy. | Broader team usability acceptance uses the existing section checklists; current register tests do not newly verify all Purchasing actions. |
| Overview / Favorites | Authorized context/theme/navigation, focused routes, personal section/function Favorites and unsaved-work/pending-operation protection are implemented. | No broader identity, employee role administration or production SSO is implied. | Existing route/browser evidence remains historical; this is not a new all-sidebar acceptance pass. |
| Finance; HR; Factory/Fit-out; CRM | Finance/HR remain explicitly unfinished; later specialist scope is recorded. Synthetic journals/counterparties/commitments do not implement these business sections. | Finance mappings, AP/AR/cash/bank/tax/assets/expenses/close/project rules and approvals; HR personnel/payroll/privacy/legal rules; Factory/project lifecycle/system-of-record boundaries; CRM ownership/consent/lifecycle need approved definitions. | Tests of supporting arithmetic/navigation do not establish those absent workflows or production readiness. No new monitoring utility is recommended as a substitute. |

### Priority decision after this bounded completion

1. Finish **demonstrated user-facing failures** found in configured Retail,
   Inventory and Procurement team journeys; do not automatically manufacture
   another speculative tests-only task. Existing team paths:
   [Retail/Delivery](RETAIL_TEAM_ACCEPTANCE.md),
   [Procurement](PROCUREMENT_TEAM_ACCEPTANCE.md),
   [Inventory review](INVENTORY_WAREHOUSE_WORKSPACE.md).
2. The next substantive Inventory journey is **actual authorized transfers**,
   but actor/source/destination/approval authority must be confirmed before
   implementation. This pending choice blocks that write journey only.
3. Retail's next business exits are supplied operative quotation content/output
   rights and the held-stock decision; Procurement's is intended operational
   authority plus any approved master-data/UOM work. These are **specific
   dependencies**, not a declaration that every started section is finished or
   all independent work must stop.
4. No additional substantive policy-independent missing function is evidenced
   by this pass. Do not invent a feature, grant or approval simply to name the
   next batch. Verification-only limitations above can be chosen deliberately
   for team acceptance, but must not be mislabeled unfinished functionality.

No dates, delivery promises or effort estimates are invented. All current
positive testing uses configured disposable synthetic personas, not new real
assignments. Passing 333 frontend tests and two browser cases does not equal
company acceptance, operational completeness or production readiness.

### Existing phased review plan

| Phase | Entry / activity | Exit evidence |
| --- | --- | --- |
| A — owner prepares isolated review | Use existing synthetic fixtures, required installed tools and privately supplied temporary test credentials. Never seed/reset shared demo or alter team grants to make a test pass. | Identify fixture/persona, allowed scopes/configuration and cleanup procedure. Missing configuration is recorded, not invented. |
| B — Procurement | [Existing Procurement checklist](PROCUREMENT_TEAM_ACCEPTANCE.md): upload/review, commitment progress, partial receipts, denial/over-receipt/retry and cost masking. | Team notes expected versus observed quantities and decisions; unresolved access policy remains explicit. Existing automation cleans up; it does not leave a ready manual session. |
| C — Retail → Delivery | [Existing Retail checklist](RETAIL_TEAM_ACCEPTANCE.md): stock/supported special order, approved discount, intent hold, simulated deposit, readiness, Manager decision and whole-quote handover. | Record price snapshot, one collection/reservation/handover per retry and delivered quantities/status. Hold behavior is tested **as currently implemented**, not accepted as final blocking policy. |
| D — Inventory, returns, Reports and Governance | Review scope-specific quantities, non-posting inspection, assigned report/export cases, and separately authorized configuration/capacity functions. | Role/scope denial and masking evidence plus team feedback. Do not mistake unavailable positive paths for completed acceptance; do not grant access solely for this exercise. |
| E — business/production gates | Resolve policy-dependent requirements, approve real access/cutover, test representative operations and recovery/security. | Explicit section/workflow sign-off, reconciled migration, production safeguards and accountable support. Not authorized to execute by this document. |

The Retail checklist documents an existing **loopback-only**, time-limited manual review mode. Procurement needs an owner-prepared isolated session; no shared team accounts/session were created by its acceptance run. Remote team access is not established by a local URL and must not be solved by publishing/changing privacy here.

### Recap / acceptance record template

For each team pass, record without credentials or real personal/customer data:

- Section and exact workflow; source requirement/checklist step.
- Persona and permitted synthetic BU/location/configuration; no real-user role inference.
- Implemented behavior versus unavailable behavior; expected versus observed result.
- Evidence type: mocked rendered test / real API / real-backend browser / human team observation.
- Referenced test run and count, **or explicitly “not run in this pass.”**
- Feedback, defect or policy question; owner of the decision and recorded acceptance status.
- Next independent action; no promised date without a defined scope/assumptions.

## Explicit unresolved facts / gates

- **Samer:** temporary-hold stock blocking; operational procurement access/history classification. Preserve current behavior pending answers.
- **Finance/owner:** production recognition/valuation/accounts/tax/FX, corrections/periods, return financial authority/disposition/exceptions, late costs, invoicing, payment-policy extensions and partial-delivery allocations.
- **Operations/access owners:** real team role/location assignments, warehouse write authority, catalog membership/UOM/barcode rules, transport/service requirements and Factory/Fit-out continuity. Existing synthetic personas are not approved real people.
- **HR/legal:** personnel/payroll rules and access; return legal validation. The confirmed 14-day delivery-based company rule does not resolve those legal/financial questions.
- **Unverified here:** exact configured HTTPS preview health/status code, current shared-data/configuration/migration provenance, account-level privacy, real team sign-off, exhaustive privileged-screen coverage, native Office rendering, dependency/licence review completeness and production controls. No secrets or production/shared business data were inspected.
- **Runtime evidence:** the platform reports `Start application` **running**. A final unsigned local app-preview capture at `http://127.0.0.1:5000/` rendered the private sign-in screen; browser console showed Vite connected and no app errors. This was not authentication, an HTTP status-code/HTTPS check, shared-data verification or continuous-availability evidence. No workflow restart was performed.

No application behavior, policy, permission, shared record, production connection, privacy or publishing change is part of this checklist.
