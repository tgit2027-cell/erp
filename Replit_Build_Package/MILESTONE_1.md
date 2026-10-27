# Milestone 1 — Private executable foundation

## Deliverables

A working React/TypeScript interface served with a Django/DRF API and PostgreSQL persistence. Establish modules for organization, access and catalog; provide empty clearly labeled module boundaries for later work. Use one application, not separate brand databases or legal companies.

Create database migrations for LegalEntity, BusinessUnit, ProductBrand, hierarchical Location, Item/Variant, user-to-unit action grants, and synthetic read-only StockExample records. StockExample is explicitly a demo fixture, not authoritative inventory. Seed exactly one legal entity and three units. Use unique constraints and appropriate foreign keys; reject cyclic location hierarchies and cross-company parent relationships.

Initial synthetic physical locations: Street 41 buildings 1–4, Birkat Awamer warehouse, Tivoli showroom ground floor, mezzanine left/right, Roche Bobois showroom and Natuzzi showroom. Building numbering and product quantities are illustrative. Do not assume ownership/access merely from where stock is stored. Model an explicit responsible business unit for each StockExample.

Provide at least two fictional sample items per brand with prefix TVF, RBB or NAT, descriptions and variants. Mark all examples Synthetic. Product-brand identity is independent of access scope. Provide fictional users for each single unit and one authorized multi-unit user. Create passwords through private runtime setup; never publish fixed credentials in source or documentation. No payroll or real customer records.

## Visible application

- Login/logout with server authentication; secure session and CSRF handling for mutations. Keep same-origin routing where feasible. No credentials in browser storage.
- Dashboard displaying Synthetic prototype and the signed-in user's permitted context.
- Unit selector listing only authorized units.
- Searchable sample item/stock view scoped on the server to current grants.
- Location tree and organization view restricted to authorized context; avoid leaking stock from shared locations.
- Seven core navigation sections. Label unfinished functions Planned; no fake success buttons or fabricated analytics.
- Responsive readable layout, accessible form labels and clear validation/errors. Do not spend the milestone on decorative animations.

## Test requirements

Use Django's test runner or pytest with a compatible Django integration, against PostgreSQL. Independently derive expected scope from fixture grants.

1. Anonymous requests cannot read protected records or exports.
2. A Roche Bobois user sees permitted records only, and cannot retrieve Natuzzi/Tivoli records by direct ID, query parameter, search, export or forged unit header.
3. Multi-unit users can switch among their permitted units; a revoked grant takes effect without trusting the frontend selection.
4. Brand prefix changes do not change record access.
5. Location cycles and invalid cross-company nesting are rejected.
6. Seed is repeatable without duplicate records and refuses a non-demo environment; it never deletes existing records.
7. Migrations succeed from an empty PostgreSQL database; records remain after application restart.
8. Login/logout and CSRF protection work; frontend build/type checks pass.
9. Inspect the source and generated client assets for accidental credentials. Report the scope of this check without claiming a full security audit.

All implemented record-access routes require coverage. If export is deferred, omit the endpoint and mark it Not implemented; do not claim export isolation was tested. Show evidence for both allowed and denied actions. Include a view-only cross-unit fixture if separate stock-view versus order-write grants are introduced; order creation itself is deferred.

## Exit

Runnable private preview, reproducible source, lock files, schema migrations, synthetic seeding, actual test output and completed REVIEW_HANDOFF.md. Report unresolved issues. No publishing, live data, accounting certification or production readiness implied. Stop here.
