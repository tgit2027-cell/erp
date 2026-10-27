# Milestone 4 — synthetic reporting and controlled exports

## Scope and safety boundary

This increment adds a reporting area to the TIV-ERP prototype using synthetic records only. It is not an approved financial policy, production analytics service, statutory report, or source of real company data. No production database, payment provider, project privacy setting, or publishing flow was used.

Report-panel assignment and report-data permission are independent. A panel grant controls whether the report appears; a separate data capability and business-unit or company scope are required for every report query and export. Role membership is also synthetic and revocable. Staff configuration accepts only synthetic demo users, synthetic report roles, business units, legal entities, locations, and items. Reporting grants do not modify the operational `read_stock` or `create_order` grants.

## Report panels and data sources

- Sales, daily/monthly/quarterly: operational rows and all derived operational counts, summaries, trends, best-seller aggregates, budget actuals, exports, and saved-view results use transactional `SalesReportLine` records only. A separately scoped, prominently labeled illustrative section in the Sales UI reads only `DemoReportSalesFact` and shows its own filtered count and summary; it does not feed operational sales. Sale rows are limited to period, selling price, quantity, item description/code, warehouse location, brand, and derived sales amount. No customer/order identity, supplier price, landed cost, purchase history, inventory history, or gross margin is returned by ordinary sales queries.
- Best sellers and categories: the same synthetic delivery facts. Item/category summaries are only returned inside authorized scope; category names require an additional `category_sales` capability and can be configured only for synthetic items.
- Costs and expenses: synthetic purchase receipt values, purchase-value landed allocations, and existing posted synthetic expense-class journal lines. Supplier cost, landed cost, and expenses are independently scoped.
- Gross margin: delivery revenue and test COGS, with explicit sales, gross-margin, and supplier-cost capabilities. Output is labelled test-only and is not an approved accounting measure.
- Inventory, purchase history, and transfer history: each requires its own capability. Inventory value and transfer value additionally require overlapping supplier-cost and landed-cost permissions; purchase-history fields are separately masked by those permissions.
- Budgets versus actuals: three calendar-granularity budget examples per unit are loaded by an owner-run demo fixture command. Values are fictional, configurable test values, not approved budgets or ownership assignments. Each actual is shown only when both budget access and the relevant underlying data capability cover that budget's business unit.
- Business-unit and consolidated company views: a business-unit report requires a grant for that unit. A consolidated view requires an explicit legal-entity grant; a collection of unit grants is not treated as company-wide authority.

The existing `Location` hierarchy is used for location filters and warehouse/showroom-level detail. Locations are not typed as showrooms, so reports describe them as locations unless the prototype data is later extended with an approved classification.

## Exports and audit

Authorized users can export the same scoped report tables and field set as Excel (`.xlsx`) or Word (`.docx`). Export generation uses small standard-library Office Open XML writers; exports contain only the report service response, including fields separately authorized for that user. Every successful export records the user, panel, file format, scope, filters, exported field manifest, and row count. Denied exports do not create audit records. Session-authenticated POST export and configuration requests retain CSRF protection.

## Current limitations and open policy decisions

- The owner-controlled `seed_demo_reports` command seeds a fictional Managing Partner/report-panel-manager role and three BU-scoped sales roles for the existing synthetic demo accounts. The manager role has only sales and budget-data grants for TVL/RBB/NAT; each sales role has only sales data for its own BU. No sensitive financial-data capabilities are seeded.
- The fixture does not change or set passwords, operational stock/order grants, or user identities. It can be refreshed or reset without deleting manual configuration, users, passwords, or export audit records. It refuses non-demo and non-local databases.
- `DemoReportSalesFact` remains a separate report-only synthetic fixture source; it never contributes to operational SalesReportLine reports or appears in their totals, counts, exports, trends, pagination, or saved-view results. The Sales UI has a separate `Synthetic examples — not operational sales` section with its own filtered example count and illustrative revenue/quantity summary, using the existing Sales panel and sales-data scope authorization. It exposes no cost fields and has no export path. There is no real or historical sales import; report-only examples do not create inventory, payments, delivery, or journals.
- The prototype has no generic expense-entry workflow. The expense panel can report existing synthetic posted expense-class journal lines only; it does not infer which accounts should count as operating expenses. Expense attribution to a location is absent, so location-filtered expense reports fail closed.
- Gross margin, COGS grouping, expense classification, report definitions, and accounting periods have not been approved by Finance. The current output is test-only.
- The sample sales facts have fictional category labels, but sales roles do not receive the extra `category_sales` capability. Budget amounts are explicitly fictional demo values and can be configured in the test environment; no budget owner or real target is inferred.
- Locations are not classified as showrooms; report labels do not assert they are.
- The OOXML packages are structurally tested as ZIP/XML, but have not been interoperability-tested in Microsoft Office or LibreOffice.
- Subsequent increments ran isolated synthetic load baselines and bounded performance checks, as recorded in `REVIEW_HANDOFF.md`. This is not a production load/SLA proof, security audit, or production-readiness claim. No project privacy setting or publishing state was changed.
- Remaining policy decisions include a fiscal calendar and close-period rules; approved expense-account mapping and location attribution; finance-approved gross-margin/COGS definitions and treatment of returns or adjustments; real budget owners/periods; and currency/consolidation rules. The prototype does not invent these. The UI labels seeded figures as fictional and identifies the existing QAR display as prototype-only, not approved currency policy. Daily/monthly/quarterly windows use editable calendar dates.

## Original increment verification (historical)

These counts describe the original increment only. Current implementation, saved-workspace/source-isolation regressions and latest whole-suite results are recorded in `REVIEW_HANDOFF.md`.

- Full PostgreSQL command: `APP_ENV=demo python manage.py test foundation --verbosity 1`
- Exact result: `Found 94 test(s)`; `Ran 94 tests in 22.236s` / `OK` — **94 tests, 0 failures/errors**, PostgreSQL 16.15. Django created `test_tivoli_erp_demo`, applied migrations through `foundation.0011_demoreportsalesfact_reportbudget_demo_fixture_key_and_more`, then dropped the temporary database.
- Reporting/Favorites focused run: `Found 22 test(s)`; `Ran 22 tests in 5.804s` / `OK`.
- Coverage includes fixture reload/reset isolation; seeded role/grant scopes; panel assignment and revocation for fictional users and roles; denial of panel management for ordinary users; cross-BU denial; ordinary-sales field masking; sensitive-field denial; populated daily/monthly/quarterly sales and budget examples for all three units; session-based Excel/Word exports with matching contents and audit scope/fields/counts.
- `npm run typecheck`: **Pass**. `npm run build`: **Pass**, Vite 8.3.4 transformed 19 modules and produced JS (282.67 kB) and CSS (38.18 kB) in 326 ms.
- `npm run test:contrast`: **Pass**, all 13 existing palette/accessibility assertions. `python manage.py check`: **Pass**, no issues. `python manage.py makemigrations --check --dry-run`: **Pass**, no changes detected.
- The existing `Start application` workflow applied migration `0011`, ran the base synthetic seed, and served Vite on port 5000. The owner-run `APP_ENV=demo python manage.py seed_demo_reports` then loaded 15 report-only facts, 9 budget examples, 4 synthetic roles, 9 panel grants, and 9 data grants. The local development database remains PostgreSQL 16.15.
- The preview screenshot rendered the unauthenticated synthetic sign-in screen. The browser capture cannot sign in, so the authenticated report/panel-manager UI was not visually captured; no auth bypass or password change was made. The live workflow log recorded HTTP 200 for the report-panel configuration, report catalog, and sales-report endpoints. Session-authenticated API, export scope, contents, and audits are covered by PostgreSQL tests.
- At this original verification point, the Overview location tree logged an invalid nested-`<li>` warning. A subsequent authorized location-tree repair and rendered regression suite resolved that markup issue; see `REVIEW_HANDOFF.md`.
- No project privacy setting or publishing state was changed.
