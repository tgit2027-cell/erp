# All-sections navigation — synthetic team review

**2026-10-21 integration-readiness update:** Finance's existing main destination
now renders a focused informational status page, not operational Finance or a
lab launcher. The 43-child catalog is unchanged. The shared read-only owner
review paths and separate operator-run Finance lab instructions are in
[INTEGRATION_REVIEW_GUIDE.md](INTEGRATION_REVIEW_GUIDE.md).
Current focused rendering checks exercise all43 maximum-permission fixture
destinations; two new Finance browser cases use mock APIs, not real sign-in.
Existing deeper real-backend acceptance remains earlier evidence.

This is a function-page checklist, **not** full business-section or production
acceptance. Use only an owner-prepared **disposable synthetic environment**
with existing fictional test personas. Do not grant shared users extra access
to make a row pass. Never enter real customer information or credentials in
feedback. The managed shared preview is not this test environment.

## How to review

1. Sign in with the privately supplied disposable persona. Expand a sidebar
   group with keyboard Enter/Space and choose the child. Only applicable
   children appear; **43 is the maximum across existing independent gates**,
   not a promise that every user sees all 43.
2. Compare the page heading, active child and actual visible function below.
   A changed URL/label alone is not a pass. Unrelated transaction forms must
   not be visible. Empty, denied and unconfigured states are legitimate when
   the persona lacks independently required scope/configuration.
3. Navigate Back/Forward. Pin an allowed function, open it from My Page and
   refresh; existing section Favorites must still open their section landing
   page. A Favorite never grants data/transaction authority.
4. Switch to another authorized BU: confirm selected label/theme and that
   previous-BU data is cleared. A denied direct link must not reveal hidden
   content. Do not add a BU/grant to bypass this.
5. Use the existing [Retail](RETAIL_TEAM_ACCEPTANCE.md) and
   [Procurement](PROCUREMENT_TEAM_ACCEPTANCE.md) checklists for transactions.
   Preserve explicit draft-discard decisions and pending-submission departure
   protection. Do not submit test transactions to the shared demo.

Routes below are `#/section/function`. Mark each applicable row **pass / fail /
unavailable-as-expected**, with the persona, BU and observed behavior.

| Section / child | Route | Expected focused content |
| --- | --- | --- |
| Retail & Sales | retail/main | Sales function launcher, not combined transaction forms |
| Create a quote | retail/quote | Existing multi-item quotation form and setup/permission states |
| Quote collection & holds | retail/actions | Quote selection and permitted existing actions |
| Authorized quote register | retail/register | Quote rows/detail or authorized empty/denied state |
| Collection follow-up | retail/collections | Separate simulated merchandise collection balances |
| Procurement & Imports | procurement/main | Receiving/commitment function launcher |
| Create purchase commitment | procurement/commitments | Existing multi-line commitment entry |
| Commitment receipt progress | procurement/progress | Ordered/received/remaining line quantities |
| Prepare imported-goods receipt | procurement/receipt | Existing authorized receipt preparation/submission gates |
| Packing-list mapping & review | procurement/packing | Local CSV/XLSX mapping/review, no upload posting |
| Receipt & landed-charge registers | procurement/registers | Scoped registers with existing cost masking |
| Staff counterparty directory | procurement/suppliers | Existing staff-only counterparty tab |
| Inventory quantities | inventory/main | Ledger unreserved quantities, not illustrative stock |
| Permitted locations | inventory/locations | Authorized location hierarchy only |
| Prepare imported-goods receipt | inventory/receipt | Shared focused receiving function, same permissions |
| Purchase commitments | inventory/commitments | Shared focused commitment function |
| Commitment receipt progress | inventory/progress | Shared focused line-progress function |
| Packing-list preview | inventory/packing | Shared local packing review function |
| Inventory & transfer registers | inventory/registers | Existing read-only inventory/transfer register selection |
| Warehouse capacity | inventory/capacity | Separately assigned location-capacity view or denial |
| Delivery & After-Sales | delivery/main | Delivery function launcher |
| Authorized quote lookup | delivery/quote | Quote detail and contextual sales link |
| Picking preparation | delivery/preparation | Local **reviewed only** checklist, not physical picking/dispatch |
| Readiness & handover actions | delivery/handover | Selected quote and existing gated actions |
| Quote register | delivery/quotes | Quote register selected, not another register |
| Storage-charge register | delivery/storage | Storage register selected |
| Reservation register | delivery/reservations | Reservation register selected |
| Delivery register | delivery/deliveries | Delivery register selected |
| Sales-return intake | delivery/returns | Synthetic non-posting intake/inspection, Finance pending |
| Finance & Accounting | finance/main | **STATUS ONLY · NOT OPERATIONAL FINANCE**; existing shared-workspace links, isolated draft coverage and precise unavailable boundary; no lab routes/forms |
| HR & Payroll | hr/main | **PLANNED · NOT IMPLEMENTED**, no functioning HR claim |
| Governance & Administration | governance/main | Independent authority overview |
| Business-unit palettes | governance/presentation | Existing staff-gated palette controls |
| Purchasing configuration review | governance/purchasing-config | Read-only selected-BU configuration |
| Reporting governance | governance/reporting | Accurate authority explanation; management link only for existing managers |
| Manage report panels | governance/report-panels | Existing panel-manager assignment/revocation, not data grants |
| Capacity locations & assignments | governance/capacity-admin | Existing superuser-only controls |
| Warehouse capacity audit | governance/capacity-audit | Existing superuser-only event metadata, no measurement values |
| Reports & Dashboards | reports/main | Assigned panels/saved views/controlled exports, or unavailable state |
| Purchasing Workbench | purchasing/main | Launcher, not an aggregate workspace |
| Quote lookup | purchasing/quote | Authorized quote lookup/detail |
| Transaction workbench | purchasing/transactions | Existing action forms and quote selector, no register/configuration aggregate |
| Purchasing records | purchasing/registers | Existing scoped read-side registers |

Shared receiving/quote workflows intentionally reuse components and APIs; this
does not create a second posting engine or new permission. A function page
can contain its existing related forms or register selector.

## Evidence and feedback

See [destination matrix](ALL_SECTIONS_DESTINATION_MATRIX.md) and the newest
[handoff](REVIEW_HANDOFF.md). Automated real-backend navigation uses
`RUN_FULL_REGRESSION=1 bash scripts/verify-real-navigation.sh`: isolated
PostgreSQL 16.15, generated temporary synthetic credentials, real Django
session/CSRF APIs and browser input; the database/server are discarded afterward.
This command runs an automated journey; it is not a persistent team sandbox.

The shared demo's existing ordinary accounts currently have read-stock grants
but no quotation roles/prices/customers. Their quote-creation unavailable state
must not be worked around by inventing assignments. Shared authenticated
acceptance is separate from disposable browser evidence.

| Persona / BU / viewport | Route | Expected | Observed | Pass / fail / unavailable | Feedback |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

Hold-stock blocking, procurement authority, Finance/refunds, HR/legal, actual
user assignments and company Terms and Conditions remain unchanged/pending.
