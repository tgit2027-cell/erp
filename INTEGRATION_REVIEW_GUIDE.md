# Owner/team integration review — synthetic prototype

**Scope:** existing shared ERP journeys plus a separately operated Finance draft
lab. Implemented does not mean team-accepted or production-ready. Internal ERP
controls are English; only customer quotation/invoice documents are bilingual.
Do not use real customer, supplier, employee, banking or accounting data.

## 1. Start safely

Use the existing private development Preview. Current configured origin:
`https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000`.
Development availability is session-bound. If the origin changes, use Preview
and the fragment paths below instead of an old URL.

Sign in only with an **existing synthetic account whose credentials the owner
already holds**. This guide supplies no credentials and creates no users/grants.
If credentials, private workspace access or permissions are unavailable, record
that limitation; do not reset credentials, seed data or assign yourself access.

This shared review is **read-only**: inspect existing records and states. Do not
submit quotations, collections, receipts, handovers, returns, capacity edits,
palette changes, assignments, or Favorite changes. Exporting creates an audit
record, so do not execute shared exports in this read-only review.
Use the existing isolated acceptance facilities for transaction scenarios.

If organization/BU context cannot be verified, the existing workspace block
remains authoritative, including for Finance status. Do not bypass it.
Navigation visibility is not a data capability.

## 2. Shared section review paths

Links point to the current private development origin, not production. Each
destination enforces its existing permissions and current BU/location scope.
Denied, unassigned and unconfigured states are valid outcomes for accounts
without the stated prerequisites—not instructions to grant access.

| Section / direct destination | Review now | Prerequisites and boundary |
| --- | --- | --- |
| [Overview](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/dashboard/main) | Confirm current BU/theme and section navigation. | Existing sign-in and verified organization scope. Do not change palettes. |
| [Retail quotation](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/retail/quote) | Inspect existing item/customer choices, fixed selling prices and editable entry controls without submitting. Inspect an existing authorized quote/document through the register. | Existing quote access, scoped choices and approved pricing configuration; discount authority remains enforced. Sales cannot create items or override price. Bilingual customer preview is not new output permission; T&C remain pending. |
| [Collection follow-up](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/retail/collections) | Compare quoted amount, recorded merchandise collections and remaining balance on an existing permitted quote. | Existing collection/quote permissions. Prototype records are not real payments, overdue receivables or credit terms. Storage collections remain separate. |
| [Procurement progress](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/procurement/progress) | Inspect ordered/received/remaining quantities and existing partial receipts. Packing mapping/review is available through its existing child, without confirmation. | Current commitment/receipt and location authority; supplier/cost/history restrictions remain. A file preview must not post stock. No invoices, FX or late-cost valuation. |
| [Inventory quantities](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/inventory/main) | Search authorized ledger buckets by item/location code; inspect exact unreserved quantity and permitted location. | Active stock capability plus explicit selected-BU/location membership. No illustrative fallback, cost or valuation. Capacity requires its separate explicit physical-location assignment, not stock access. |
| [Delivery preparation](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/delivery/preparation) | Inspect one authorized quote's specifications, per-line quantities and readiness; follow existing handover links without submitting. | Existing quote/readiness/handover gates. Local checklist means reviewed, not physical picking/allocation/dispatch. Reservation history is not proof of current availability. |
| [Delivery/returns](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/delivery/returns) | Read existing synthetic intake, delivery-based eligibility and inspection/Finance-pending states. | Existing return authority. Configurable 14 calendar days from actual delivery is the owner rule, not a verified legal opinion. No refund, credit note, stock put-back or posting. |
| [Reports](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/reports/main) | Review assigned panels, daily/monthly/quarterly operational summaries, scopes and masked fields. Inspect export availability only. | Panel assignment and data capability are separate. SalesReportLine alone supplies operational sales; synthetic examples remain separately labeled/countable. Excel/Word require explicit output rights and create an audit record. These are not operational Finance statements. |
| [Governance](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/governance/main) | Inspect only controls the current account is already entitled to see. | Staff palette/configuration review, report-panel manager and superuser capacity administration gates remain independent. Do not assign/revoke/edit for this review. |
| [Finance status](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/finance/main) | Read the accurate shared-versus-isolated status and follow existing contextual section links. | Informational only, no Finance records/forms/lab launcher. Schema readiness is not Finance authority. Manager link appears only with existing panel-manager authority. |
| [Purchasing compatibility workspace](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/purchasing/quote) | Inspect an authorized quote lookup; use the business sections for arranged journeys. | Reuses the same scoped services/forms; not an alternate route around permissions. |
| [My Page](https://a4c96d33-2378-4ad9-981e-34ad51db3300-00-3ofpyzwgxkcia.sisko.replit.dev:5000/#/my-page/main) | Open existing section/function Favorites and reload/back/forward. | No Favorite writes during shared read-only review; no new Finance function keys. |

HR remains explicitly unfinished. Factory/Fitout/CRM remain documented roadmap
scope, not newly implemented functions. See the
[business-readiness checklist](BUSINESS_READINESS_CHECKLIST.md) for the wider scope.

Existing detailed scenarios and feedback sheets:
[Retail pilot](RETAIL_PILOT_REVIEW_GUIDE.md),
[Retail/Delivery acceptance](RETAIL_TEAM_ACCEPTANCE.md),
[Procurement acceptance](PROCUREMENT_TEAM_ACCEPTANCE.md),
[all-section destinations](ALL_SECTIONS_TEAM_ACCEPTANCE.md).
Their transaction steps belong in isolated test facilities, not this shared
read-only pass.

## 3. Review Finance separately — operator prerequisites and actual limits

The shared Preview does **not** host a Finance lab session/API or confer Finance
access. Do not append lab paths to it. Existing report-manager, staff and
superuser status do not supply an approved operational Finance role.

An operator with workspace terminal access can use the **existing** facility:

```sh
FINANCE_LAB_SKIP_BACKEND=1 FINANCE_LAB_REVIEW=1 \
  bash scripts/verify-finance-draft-workflow.sh
```

Prerequisites: installed project Node/Python dependencies, the existing
PostgreSQL **16.15** binaries (or `RETAIL_PG_BIN` pointing to an already installed,
verified equivalent directory), sufficient temporary disk/process capacity and
unused local ports **55167/18069**. Stop if unavailable; this guide authorizes
no installs, port changes, shared migration, credentials or grant changes.
Do not run another copy concurrently.

The script creates an owned `/tmp/tivoli-finance-lab.*` cluster and
`test_finance_lab` database, verifies its actual data directory, applies schema
and minimal synthetic fixtures **only there**, and serves loopback **18069**.
It unsets shared secrets only inside its own child environment. It does not
reset/reseed the shared demo. Ctrl-C terminates and deletes this facility.

Its local entry is `http://127.0.0.1:18069/lab-ui/`. The explicit synthetic
test-session button has **no password** and is not normal ERP authentication.
Use the existing setup controls to create/select synthetic draft accounts and
dimensions; they do not create an operational chart or real accounts.
Review index then opens existing saved drafts in their original editor.

**Remote review limitation:** loopback is not a working external team URL.
No owner-accessible lab forwarding or published route was established in this
batch. An operator can use the existing in-workspace browser harness; direct
owner/team live access remains unavailable unless an already approved local
review facility exists. Do not proxy the lab through the shared app or weaken
authentication to make a link work. Operator screenshots are evidence, not
hands-on team acceptance.

Separate automated operator demonstration, after closing manual review:

```sh
FINANCE_LAB_SKIP_BACKEND=1 FINANCE_LAB_LOADING_BROWSER_ONLY=1 \
  bash scripts/verify-finance-draft-workflow.sh
```

This creates a new disposable environment, runs the existing authenticated
real-backend loading/reopen/reversal journeys and deletes it; it is not a
continuation of manual review data. These commands were documented, **not run**
in this integration-readiness batch.

### Suggested isolated reviewer steps

1. Open the explicit test session; enter synthetic account/dimension choices.
2. Enter a balanced QAR journal with explicit date/accounts/dimensions. Save,
   reopen/reload within the same facility and check the saved revision.
3. Inspect representative existing recurring/opening/adjustment, budget,
   reconciliation, expense/assets/forecast and AP/AR proposal editors. Enter
   only explicit inputs; no automatic dates, allocations, mappings or approvals.
4. Use Review index type/label/exact-ID filters. IDs can repeat across types;
   reopen the intended type and inspect immutable history.
5. In two isolated views, edit the same saved revision. The competing save
   must report a conflict and preserve local work rather than silently replace it.
6. If a create acknowledgement is uncertain, inspect the existing request-status
   recovery before retrying. Do not create a fresh identity to evade uncertainty.
   Request metadata is not a durable copy of every unsaved payload; see
   [recovery guarantees/limits](FINANCE_DRAFT_RECOVERY.md).
7. Confirm all results are proposals: no posting, payment, issued invoice,
   approval, account reconciliation, tax activation or operational balance change.

Saves persist to the disposable database across page reloads, **not** beyond
facility shutdown. Previously verified test behavior is not a new claim that
these steps were performed by the team or in the shared demo.

## 4. Integration reconciliation and next deliverable

Full supplied Finance scope and beneficial additions are preserved in
[requirements coverage](FINANCE_REQUIREMENTS_RECONCILIATION.md) and the
[readiness matrix](FINANCE_READINESS_MATRIX.md). One legal entity with BU
dimensions remains the basis, not assumed multi-company operation.

| Concrete missing end-to-end step | Independent technical work | Decision before operational activation |
| --- | --- | --- |
| Procurement receipt → AP proposal evidence | Candidate below: explicit source-reference review in the existing isolated AP editor, through unchanged authorized receipt reads. No automatic matching. | Operational AP master/acceptance, matching tolerances, invoice/cost treatment, settlement and posting authority/mappings. |
| Sales quote/delivery/collections → AR/GL | Inspect current source contracts and preserve references; synthetic proposals remain separate. Do not relabel collections as accounting AR. | Invoice issuance/recognition, credit/payment/refund rules, account mappings and Finance access. |
| Inventory/receipt/transfer → valued accounting | Existing quantity ledger and read-only diagnostics remain usable; source consistency checks do not set values. | Valuation/late-cost/recognition/mapping/correction rules; transfer-write authority remains pending separately. |
| Draft accounts/dimensions → live GL, periods, reversals | Existing explicit input/revision validation is implemented in isolation. Do not add another worksheet to imply integration. | Approved chart classifications, fiscal periods, segregation of review/approve/post, mappings and opening balances. |
| Bank, expenses/petty cash, assets, forecasts and budgets → actuals | Existing proposal workflows can be reviewed together. No real bank/employee/document imports or automatic realization. | Bank authority/sources/tolerances, reimbursement/cash controls, depreciation/capitalization, budget ownership/actual sources, forecast realization. |
| Projects, shared BU costs, factory costing, facilities/loans | Required scope retained; no new pretend functioning section. | Authoritative project/factory/facility lifecycle, attribution/allocation/valuation, terms/rates and access. |
| Tax, close/statements, approvals and payroll | Existing diagnostics/operational reports do not satisfy these missing systems. | Approved tax applicability/configuration, closing/statement classifications/output rights, Finance workflow authority and HR/legal/payroll sources/rules. |
| Shared Finance lab/review access | This status/guide is complete; no technical route is exposed. A secure implementation can follow approved capability boundaries. | Explicit Finance capabilities, entity/BU/location scope and reviewers; no inherited staff/admin/panel grant or actual user assignment presumed. |

**Next substantive candidate, not implemented here:** source-linked AP
supplier-invoice **receipt-reference review** in the isolated lab, reusing its
existing saved invoice proposal rather than another worksheet. Let a reviewer
explicitly select permitted receipt/line references, inspect existing authorized
metadata, save the reference with the proposal and reopen it. No copying hidden
supplier/cost fields, automatic amount/account choice, matching tolerance,
approval, payable or posting.

The current receipt service already requires synthetic actor checks,
`PURCHASE_HISTORY` panel **and** data capability plus resolved BU/location scope;
supplier/landed cost are separately optional capabilities. Source:
`foundation/reporting_views.py::PurchaseReceiptDetailView` and
`foundation/reporting_services.py::build_purchase_receipt_register`.
The lab session must never replace these checks. First prove both existing
access contexts can be composed in a disposable fixture; if not, stop that
dependent path and report it rather than inferring a new permission.
Shared activation still needs the Finance access decision above.

Acceptance for that candidate: real disposable-backend selection/save/reopen,
source/entity/BU/location consistency, independent source denial/revocation and
cost masking, exact proposal revisions/recovery, source records unchanged and
no match/posting claim. Existing hold blocking, procurement authority, transfer
authority, T&C/output and Finance policy questions remain unchanged.

## 5. Review feedback sheet

For each scenario record: **account's existing role/capabilities** (no secrets),
BU/location, destination, synthetic reference, expected/actual result,
Pass / Fail / Blocked / Not tested, and a sanitized screenshot if useful.

| Severity | Meaning |
| --- | --- |
| Critical | Protected information or unauthorized action exposed; stop the scenario. |
| High | Wrong scope, duplicate operation, lost draft or incorrect quantity/amount. |
| Medium | A supported journey is blocked or recovery/navigation is misleading. |
| Low | Readability/wording issue without a data or workflow effect. |

Keep **implemented**, **technically verified in isolation**, **awaiting shared-team
acceptance**, and **policy-dependent/not activated** as separate feedback fields.
No section becomes operationally complete merely because a page or test exists.
