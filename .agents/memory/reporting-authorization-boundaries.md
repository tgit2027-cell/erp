---
name: Reporting authorization boundaries
description: User-approved separation of report panels, row scope, sensitive fields, and controlled exports.
---

Report-panel assignment does not grant the underlying data capability. Every report and export must enforce server-side authorization for each capability and business-unit/company scope. A set of individual business-unit grants is not company-wide authority; consolidated views require an explicit company grant. Sensitive supplier cost, landed cost, purchase history, inventory/transfer value, categories, expenses, and gross margin require their own explicit capabilities. Exports must contain only authorized screen fields/rows and record an audit.

Use synthetic fixtures and fictional role assignments only. Do not seed real identities, budgets, customer/supplier data, or unapproved financial rules.

**Why:** The user explicitly separated report assignment from data access, limited ordinary sales fields, required sensitive-data grants and audited exports, and prohibited real data or inferred financial policy.

**How to apply:** For future dashboard, query, or export work, fail closed on missing or non-overlapping scopes; keep company-wide access explicit, field masking row-aware, and all sample financial values clearly test-only. Supplemental purchasing reads use the matching existing grants: purchase-history plus separate cost capabilities for receipts, inventory for reservations, sales for deliveries, create-order for quotes/storage, and staff-only synthetic directory access for counterparties.

Personal reporting workspaces are presentation preferences, not reports or access assignments. Retain only user-owned names, panel order and existing filters; never persist rows or treat a saved view as authorization.

**Why:** The user explicitly required personal saved views without new grants, expanded scopes, cached sensitive data or changes to manager assignment authority.

**How to apply:** Reauthorize saved-view loads and exports through existing reporting rules, clear old data while revalidating or when access fails, and show unavailable panels rather than substituting a more permissive scope.

Operational sales reports use transactional `SalesReportLine` facts only. `DemoReportSalesFact` examples must never contribute to operational rows, counts, summaries, trends, best-seller aggregates, budget actuals, exports, or saved-view results. If exposed in future, render them only in a clearly separate, separately counted synthetic-example section.

**Why:** The user explicitly distinguished report-only demo examples from operational sales and prohibited mixing or double-counting them in any sales output.

**How to apply:** Keep the shared operational report service, exports, and saved-view executions on `SalesReportLine`; any future sample display must have its own heading/count and must not feed operational filters or totals.

Transaction authority or staff visibility is not sensitive-financial reporting
authority. Apply the public response projection to both initial action
acknowledgements and idempotent retries; keep the private stored result intact.

**Why:** Real-backend acceptance found internal valuation facts in a transaction
response even though the UI did not render them. Hiding fields in a component
does not protect the HTTP response.

**How to apply:** Check raw action responses as well as report GETs. Reuse the
route's established disclosure boundary without new grants or changes to
private ledger calculations, audit facts or retry identity.
