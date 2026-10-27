# Consolidated Finance readiness — isolated drafts, not complete Finance

## Interpretation and sources

Source requirements: owner-supplied Finance scope preserved in
[requirements reconciliation](FINANCE_REQUIREMENTS_RECONCILIATION.md#requirement-to-implementation-matrix)
and [roadmap](MILESTONE_ROADMAP.md). Exact current evidence:
[review handoff](REVIEW_HANDOFF.md#latest-completed-batch--selected-finance-editor-loading-and-recovery-decision).
Existing architecture/spec/business-readiness links remain in that reconciliation.
Current shared-versus-isolated review paths and integration dependencies:
[owner/team integration guide](INTEGRATION_REVIEW_GUIDE.md).
One legal entity; BU dimensions, not presumed multi-company accounting.
English internal UI; bilingual issued customer quotation/invoice presentation
remains separately bounded by existing output rights.

**Isolated implemented/verified ≠ approved shared access ≠ team acceptance ≠
operational/production readiness.** No row below is a claim that Finance is
complete. Explicit QAR proposals are not real balances, payments or posting.
Existing controlled operational reports/exports are not Finance statement
issuance or new worksheet output rights.

**Current shared landing:** `#/finance/main` now explains existing operational
workspaces versus isolated Finance proposals. It is informational only: no
Finance forms, data requests, lab routes or new access. Conditional report-panel
management uses the existing independent manager flag. HR stays unfinished.
The authorized recovery applied only reviewed foundation0023–0027; schema is
ready, but shared Finance authority/operational integration are not activated.

Historical technical consolidation: [selected editor loading](FINANCE_EDITOR_LOADING.md)
now verified with131 affected Finance UI/helper tests,23 quotation regressions,
4 real-backend synthetic browser journeys and349 contrast checks. No new Finance
workflow/permission or shared upgrade. Earlier173 backend/22 focused below
were not rerun for that frontend-only packaging change. Its earlier pending
recovery decision was superseded by
[executed recovery evidence](PREVIEW_FINANCE_RECOVERY_2026_10_21.md).
Current integration-readiness checks are separately recorded in REVIEW_HANDOFF.md:
55 focused rendered/helper regressions, two mocked-API browser cases and349
contrast checks. They are not new real-backend lab or shared-account acceptance.

| Requirement / area | Implemented and technically verified in isolation | Operational scope still missing | Specific dependency before dependent activation |
| --- | --- | --- | --- |
| GL setup, manual journals, dimensions | Draft account/dimension/value setup; explicit journal lines, QAR balance review, saved histories/conflicts. `foundation/finance_drafts.py`, `foundation/test_finance_draft_workflow.py`. | Approved/live chart, classifications/mappings, shared journal authority, approval and posting. | Finance-approved chart/mappings and dimension meanings/mandatory rules; genuine view/edit/review/post scope and deliberate assignments. Lab actor is not that permission. |
| GL recurring, opening balances, accrual/prepayment, allocation | Explicit-input persisted proposals; dates/occurrences/manual destinations and exact review. Existing `test_finance_recurrence.py`, `test_finance_opening.py`, `test_finance_adjustments.py`, lab allocation tests. | Scheduled/recognized journals, operational opening migration, rule-driven allocation. | Calendar/recognition/allocation/rounding conventions, source balances/migration controls, mappings and approvals. No defaults inferred. |
| GL source-linked reversal review — **new bounded completion** | Specific saved journal revision, read-only source, explicit proposed lines/date/explanation; exact balance and grouped-net offset comparison; immutable revisioned save/edit/relink/conflicts and request recovery. `finance_allocation_lab/reversals.py`, `test_reversals.py`, `src/FinanceReversalLab.tsx`, actual browser test. | Automatic reversal creation, effective dates/period selection, approvals and posting. | Explicit approved reversal/date/period/authority/mapping rules for activation. An arithmetic offset is not approval or a journal. |
| GL periods, FX, future intercompany | Existing synthetic QAR journal protections and diagnostics; no operational capability implied. | Period setup/locks/reopen, FX/revaluation, future intercompany. | Fiscal calendar/close authority, rate sources/treatment and explicit future entity requirements. Do not assume several companies. |
| AP invoices, credit/debit, advances | Separate persisted supplier-reference proposals, exact amounts, optional validated invoice links, unpaid/unapplied advances, duplicate-reference advisories. `test_invoices.py`, `test_supplier_adjustments.py`, `test_supplier_advances.py`. | Supplier/bank master approval, operational invoices/subledger, matching, payment/application/settlement and aging. | Approved supplier/bank access/verification, terms, matching tolerances, tax/mappings, approval/payment authority; authoritative operational source integration. Existing receipts are not invoice matching. |
| AR invoices, credits, collections | Internal invoice/credit proposals with exact validated links. Existing Sales simulated collections and merchandise-balance follow-up remain separate operational prototypes. `test_customer_invoices.py`, `test_customer_credits.py`; Retail checklist. | Issued/subledger invoices, limits/credit holds, overpayments/write-offs, promises/disputes, aging/FX, real settlement/refunds. | Customer master/visibility, credit/settlement/refund/write-off/tax/recognition/output authority and source mappings. No new terms or operational balances. Pending stock-hold clarification unchanged. |
| Cash/bank reconciliation | Explicit balances/rows/manual matches, exact differences, no double matching, saved proposals/conflicts. `test_reconciliation.py`. | Cashbook/bank setup/import/connections, operational reconciliation, adjustments/sign-off. | Approved sources/cutoffs, formats, tolerance/matching, adjustment/mapping and authorization rules. |
| Fixed assets | Proposed asset records/cost/account/BU/dimensions and references, persisted register/revisions. `test_assets.py`. | Capitalization, asset subledger/evidence, depreciation, custody, transfers/disposal/posting. | Identity/classes/thresholds/lives/methods/residual values, custody/disposal/tax/mapping and approval policy. Typed location text is not custody or warehouse access. |
| Budgets | Explicit dated QAR scenarios, exact totals, advisory overlap/duplicate warnings, persisted review. `test_budgets.py`. | Approved budgets, fiscal distribution, ownership/enforcement and actuals integration. | Approved calendar/distribution, ownership/authority and actuals classification/sources; warnings are not acceptance policy. |
| Expenses/petty cash | Explicit draft claims/custodian references and receipt text, exact review/revisions. `test_expenses.py`. | Real evidence handling, claims approval, custody/limits, reimbursement/disbursement/settlement. | Eligibility/evidence/account categories/mappings, cash limits/custody, tax and approval/payment authority. |
| Forecasts; facilities/loans | Explicit opening balance and dated QAR inflow/outflow scenario drafts with exact running balances. `test_forecasts.py`. Facilities/loans retained as supplied additional scope only. | Operational forecasting inputs, facility/loan records/drawdowns/repayment/interest and accounting. | Authoritative cash/AP/AR/bank sources; actual agreed facility terms/rates/mappings/authority. No inferred timing or credit availability. |
| Future tax | Required scope retained, no VAT/rate activation. | Configurable operational applicability/rates/tax engine/control. | Finance/legal-approved applicability, rates and mappings; no presumed Qatar-law rule. |
| Period close, Finance reporting and controlled outputs | Existing report authorization/masking/export audit and balance diagnostics; not trial-balance/statement/close acceptance. | Accepted financial statements/trial balance, close checks/locks/reopen/sign-off and Finance output authority. | Approved chart classifications/calendar/closing and export authority, complete operational sources/reconciliation and mappings. Existing Sales Excel/Word rights do not grant Finance exports. |
| Project accounting; BU shared costs; factory costing | BU dimensions and explicit allocation proposals only. Project accounting, production shared-cost/factory costing remain required scope. | Project structures/commitments/actuals, cost attribution, accepted factory costing and integrations. | Authoritative project/factory sources, attribution/allocation/valuation conventions, mappings, scope and approval boundaries. |
| Payroll integration; approval/workflow engine | Retained requirements; existing specific business approvals are not a generic Finance/HR engine. | Payroll source integration and generic Finance approval/delegation controls. | HR/legal/payroll sources/rules and approved Finance authorities/thresholds/delegation/security. No automatic staff/admin Finance authority. |
| Sales/Procurement/Inventory integration; migration | Existing synthetic business workflows and accounting test protections remain intact; draft proposals are separate. | Accepted operational posting connections, real migration/reconciliation and rollout. | Approved source-of-truth events/mappings, valuation/tax/recognition, duplicate/cutover/rollback controls and access. Procurement/hold/transfer questions block only their dependent behavior. |
| Cross-draft review and uncertain-create recovery |13 endpoint families with original editors; metadata-only review index, actor/entity/request identity, same-input replay, conflicting reuse rejected, current authorization and revision safeguards. `FINANCE_DRAFT_RECOVERY.md`. | Approved shared-user Finance access, data lifecycle/retention/disaster recovery and operational hardening. | Deliberate Finance access model/assignments and separately approved shared setup. Tested at-most-one create per retained identity, not universal exactly-once. |

## Evidence and finite backlog

Previously completed reversal batch: **22 focused PostgreSQL tests** (13 reversal +9 recovery);
**173 full relevant Finance backend tests** (includes focused22);
**125 affected frontend/helper tests**, **2 actual authenticated synthetic
browser journeys** at1280/390px, **349 contrast pairs**; typecheck/build,
Django/schema/migration and syntax/diff checks passed. Exact command outcomes
and initial test-harness failures are in REVIEW_HANDOFF.md, not hidden.
Native flows prove source revision1 preservation after current source became2,
committed acknowledgement loss/replay, proposal reopen/edits/conflict to3,
unchanged sources/history, denied source lookup and review-index navigation.
They do not prove all devices, screen readers, shared/ordinary Finance login,
every existing worksheet journey or production accounting/security.

The three explicitly bounded consolidation candidates are now implemented
and verified **in isolation**: review/reopen, uncertain-create recovery and
source-linked reversal review. **No fourth worksheet is presumed approved.**
Next is owner/Finance review of this coverage and concrete access/source/
policy decisions for selected operational integration, not invented defaults
or blanket “Finance complete.” The later explicitly authorized shared-preview
recovery **completed on 2026-10-21**, with preserved business data/authority and
reviewed foundation schema only; see [execution evidence](PREVIEW_FINANCE_RECOVERY_2026_10_21.md).
Its restored unsigned entry does not provide Finance lab access or team acceptance.
The existing lab review register now has local type/label/exact-ID filtering;
this is review/reopen usability, not another worksheet or operational Finance.
