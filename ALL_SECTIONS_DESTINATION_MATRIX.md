# All-sections destination matrix — synthetic prototype

Navigation acceptance is not production or business-section completion.
Finance/HR remain unfinished. This matrix covers every current sidebar child,
including conditional staff, report-manager and superuser entries.

**Current catalog: 43 possible permission-dependent child destinations.**
This includes Collection follow-up (`#/retail/collections`) and Picking
preparation (`#/delivery/preparation`). The earlier all-41 run below predates
both additions; it is historical, not current all-43 proof. The newest handoff
records fresh visible-content, title and selected-child verification, including
authorized/unconfigured states. Team steps: [all-sections review checklist](ALL_SECTIONS_TEAM_ACCEPTANCE.md).

## Contract and verification

**Historical real-backend acceptance supplement:** the then-current 41 child destinations were
opened with an actual disposable synthetic Django session on desktop. Three
real-browser tests also cover BU/theme changes, native history, function/legacy
Favorites, representative server denials, a collection/readiness/delivery
journey, and safe draft/pending departure. Existing-fixture and fresh Favorites
migration checks pass. **3/3 real-browser, 43 focused/281 full PostgreSQL,
252 frontend, 32 mocked native, 116 contrast** checks pass; see the latest
handoff for exact commands. The per-route navigation PASS values are not claims
that every business feature or policy is complete. Shared authenticated
navigation remains unverified. The subsequent recovery inspection found the
preview already healthy, serving current modules with migration 0019 applied
during its pre-inspection startup; see the latest handoff for backup/seeding limits.

The earlier 41 child destinations passed the isolated native route/content matrix
at 1280, 390 and 320 px. Final commands/results are recorded in
`REVIEW_HANDOFF.md`. Placeholder rows pass only for honest unavailable content,
not business functionality. Permission-dependent controls remain restricted.

Routes use `#/section/function`. Each child selects its function, not a scroll
target in an aggregate page. Within a workspace, inactive forms remain mounted
but hidden so navigation preserves drafts, pending outcomes and retry identities.
These are browser views reusing existing APIs, not new transaction services.
History restores the selected function in the current authorized BU, not an old
BU or old report/stock data.

Function Favorites use `section.function` keys; existing section keys and stored
pins remain unchanged. Main-view stars use the existing section key, avoiding
duplicate `section.main` pins. No new Finance/HR function keys exist. Favorites
never grant data/transaction access. The server filters restricted choices on
every list/save; revoked choices are hidden, not silently converted into grants.

## Destination matrix

Each route below is prefixed by `#/`. "Focused" means unrelated forms/registers
are hidden, not destroyed. Existing denied, empty and unconfigured states remain
valid outcomes. The following pass/fail result table maps every row to its
isolated checks; see the handoff for exact command/count evidence.

| Section / child label | Route | Focused content or honest limitation | Favorite key |
| --- | --- | --- | --- |
| Retail & Sales | retail/main | Synthetic context and existing-function navigation | retail |
| Create a quote | retail/quote | Existing stock/special-order quote creation, configuration gated | retail.quote |
| Quote collection & holds | retail/actions | Quote selection/detail and permitted sales actions | retail.actions |
| Authorized quote register | retail/register | Authorized quote list/detail | retail.register |
| Collection follow-up | retail/collections | Scoped simulated merchandise collections/remaining balance; link to existing quote actions, no posting | retail.collections |
| Procurement & Imports | procurement/main | Synthetic context and existing-function navigation | procurement |
| Create purchase commitment | procurement/commitments | Multi-line commitment form | procurement.commitments |
| Commitment receipt progress | procurement/progress | Ordered, received and remaining per line | procurement.progress |
| Prepare imported-goods receipt | procurement/receipt | Receipt form with existing landed-charge/commitment checks | procurement.receipt |
| Packing-list mapping & review | procurement/packing | Local CSV/XLSX mapping, explicit review, draft preparation | procurement.packing |
| Receipt & landed-charge registers | procurement/registers | Receipt register and masked detail; landed charges are within authorized receipt detail, not a new standalone ledger | procurement.registers |
| Staff counterparty directory | procurement/suppliers | Staff-only existing Counterparties register; not falsely supplier-only | procurement.suppliers |
| Inventory quantities | inventory/main | Ledger unreserved quantity, scoped search/page/detail; no illustrative quantities | inventory |
| Permitted locations | inventory/locations | Authorized location hierarchy | inventory.locations |
| Prepare imported-goods receipt | inventory/receipt | Only the existing receiving form; contextual Procurement link | inventory.receipt |
| Purchase commitments | inventory/commitments | Only commitment creation | inventory.commitments |
| Commitment receipt progress | inventory/progress | Only existing commitment quantity progress | inventory.progress |
| Packing-list preview | inventory/packing | Existing local reviewed preparation; contextual Procurement link | inventory.packing |
| Inventory & transfer registers | inventory/registers | Explicit Inventory/transfer selection identifies the active read-only report; existing report grants remain separate | inventory.registers |
| Warehouse capacity | inventory/capacity | Explicit assigned-location measurements; denied/unassigned states without stock-derived authority | inventory.capacity |
| Delivery & After-Sales | delivery/main | Synthetic context and existing-function navigation | delivery |
| Authorized quote lookup | delivery/quote | Authorized quote detail/delivery progress; contextual Retail link | delivery.quote |
| Picking preparation | delivery/preparation | Quote-scoped local reviewed-only checklist, not physical picking/dispatch or inventory writes | delivery.preparation |
| Readiness & handover actions | delivery/handover | Quote selection and existing permitted delivery-stage actions | delivery.handover |
| Quote register | delivery/quotes | Quotes only | delivery.quotes |
| Storage-charge register | delivery/storage | Storage charges only | delivery.storage |
| Reservation register | delivery/reservations | Reservations only | delivery.reservations |
| Delivery register | delivery/deliveries | Deliveries only | delivery.deliveries |
| Sales-return intake | delivery/returns | Existing synthetic, non-posting intake/inspection and pending Finance review | delivery.returns |
| Finance & Accounting | finance/main | Explicit planned/not-implemented placeholder | Existing finance section pin only |
| HR & Payroll | hr/main | Explicit planned/not-implemented placeholder | Existing hr section pin only |
| Governance & Administration | governance/main | Single authority overview; no second ordinary-user authority page | governance |
| Business-unit palettes | governance/presentation | Existing staff palette/custom-color controls | governance.presentation |
| Purchasing configuration review | governance/purchasing-config | Staff-only selected-BU read-only configuration | governance.purchasing-config |
| Reporting governance | governance/reporting | Accurate permission explanation and canonical management link when already authorized; not invented new admin powers | governance.reporting |
| Manage report panels | governance/report-panels | Existing panel-manager-only assignment/revocation | governance.report-panels |
| Capacity locations & assignments | governance/capacity-admin | Superuser-only capacity designation/assignment metadata, not general location editing or measurement authority | governance.capacity-admin |
| Warehouse capacity audit | governance/capacity-audit | Superuser-only metadata audit, no measurement values | governance.capacity-audit |
| Reports & Dashboards | reports/main | Existing assigned reporting, saved views, scoped controlled exports and separate examples; management links to Governance, no inline duplicate manager | reports |
| Purchasing Workbench | purchasing/main | Focused launcher for the three existing workbench functions | purchasing |
| Quote lookup | purchasing/quote | Authorized quote lookup/detail only | purchasing.quote |
| Transaction workbench | purchasing/transactions | Existing action forms and explicit quote selector, not registers/return intake/configuration aggregate | purchasing.transactions |
| Purchasing records | purchasing/registers | Existing read-side register selection/detail only | purchasing.registers |

## Pass/fail results

**Fresh current result: 43/43 destination contracts PASS** in an isolated
real-session browser run with PostgreSQL 16.15: visible function content,
matching outer/inner titles, active child, unrelated-form/heading exclusion,
BU/theme context and section/function Favorites. The tracked oracle is
`scripts/navigation-content-contract.mjs`; a successful run emits
`generated-artifacts/real-navigation-browser/content-matrix.json` (ignored).
Finance/HR are two explicitly unfinished placeholder contracts, not functioning
business sections. Historical evidence is not substituted for these fresh checks.

| Explicit destinations from the matrix above | Result |
| --- | --- |
| retail/main, retail/quote, retail/actions, retail/register, retail/collections | PASS 5/5 |
| procurement/main, procurement/commitments, procurement/progress, procurement/receipt, procurement/packing, procurement/registers, procurement/suppliers | PASS 7/7 |
| inventory/main, inventory/locations, inventory/receipt, inventory/commitments, inventory/progress, inventory/packing, inventory/registers, inventory/capacity | PASS 8/8 |
| delivery/main, delivery/quote, delivery/preparation, delivery/handover, delivery/quotes, delivery/storage, delivery/reservations, delivery/deliveries, delivery/returns | PASS 9/9 |
| finance/main | PASS 1/1 — unfinished placeholder, no functioning Finance claim |
| hr/main | PASS 1/1 — unfinished placeholder, no functioning HR claim |
| governance/main, governance/presentation, governance/purchasing-config, governance/reporting, governance/report-panels, governance/capacity-admin, governance/capacity-audit | PASS 7/7 |
| reports/main | PASS 1/1 — existing report area, canonical manager link |
| purchasing/main, purchasing/quote, purchasing/transactions, purchasing/registers | PASS 4/4 |

The current catalog has **34 non-main function keys** (the earlier catalog had
32). Current rendered regressions test allowlisted function keys for pin/open,
exact active destination and unpin, with user isolation/CSRF/revocation and
independent capability gates tested on PostgreSQL. Main pins keep section keys.
Native tests additionally cover cold links, history, active parents, visible
keyboard focus, tab exclusion of collapsed children, themes and current-BU
requests, denied/unknown routes, and no transaction/grant writes from navigation.
The My Page regression closes obsolete section flyouts so they cannot cover
Favorite links on narrow screens.

There are no known unresolved navigation failures in this tested matrix.
This does not close the business-policy or real-session limitations below.

## Other navigation and compatibility

- Overview and My Page retain their existing non-fragment state navigation.
  Overview shows implemented section status accurately; My Page describes the
  Inventory source as ledger data, not illustrative sample quantities.
- Section headers are disclosure controls, not page actions. The active child
  identifies its title/content and reveals its parent. Stars are separate
  keyboard controls. Hidden child links are not in the Tab sequence.
- `#/reports/panels` redirects to `#/governance/report-panels`; unauthorized
  accounts see an unavailable state without opening management APIs.
- `#/governance/authority` redirects to the single `#/governance/main`.
- `#/inventory/receiving` redirects to focused `#/inventory/receipt`.
- `#/delivery/registers` redirects to focused `#/delivery/quotes`.
- Procurement/Inventory preparation is the same existing workflow in the
  appropriate journey, explicitly cross-linked. Retail quote creation and
  Delivery quote review/action contexts are named separately and cross-linked.
  Links do not transfer or grant authority.

## Deliberate limits

- Existing transaction workbenches can group related actions on their selected
  quote; this does not invent new endpoints, payment policy or partial delivery.
- Inventory registers remain a clearly identified selector over two existing
  reports. Reports & Dashboards remains a configurable report area; separate
  report panels are not invented into new business modules.
- A destination working with a denied/empty response does not prove its user
  has transaction access or that company configuration is complete.
- No cross-section durable draft storage or persistent transaction history is
  introduced. Existing within-workspace mounted-state/retry safeguards remain.
- Browser evidence uses synthetic fixture authentication and mocked API
  responses; real shared-preview authentication and screen-reader speech must
  not be claimed from that evidence.
