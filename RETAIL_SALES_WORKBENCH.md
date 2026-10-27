# Retail & Sales — synthetic operational workspace

## Owner-approved nonstaff quotation choices and bilingual presentation

Authorized synthetic nonstaff sales users now use the GET-only
`/api/purchasing/quotation-choices/?business_unit_id=…` read. The existing
active `create_order` BU grant, synthetic marker and approval-role assignment
are required. This exposes existing current approved selling prices, item
presentation metadata, existing synthetic customer choices/contact information
and the requesting user's own discount authority only. Unreserved quantities
additionally require the independent existing `read_stock` grant and explicit
location-to-BU/legal-entity membership. Empty stock matches mean unavailable
information, not an invented zero or an availability guarantee.

It is **not** access to purchasing governance, the supplier directory, histories,
cost, landed cost, margins or exports. Those original gates remain unchanged.
Counterparties have no BU ownership field in this prototype: choices are the
synthetic customer records already accepted by quote creation, never suppliers;
no customer ownership policy or broader customer-management scope is inferred.

Ordinary nonstaff submissions above their existing grade ceiling are rejected
server-side, not merely disabled in the UI. Existing staff-mediated requests
for a higher discount may still await the existing authorized approval; staff
status does not approve that discount. Explicit price/item overrides are
rejected. No stock/special-order item creation API or new administrator powers
are introduced; sales select already-created items only.

Each newly created quote retains an immutable public presentation snapshot:
item name/category/description/image reference, exact quantity, approved unit
selling price, discount/line/quote amounts, client name/contact, salesperson
identity, BU reference, date and optional department, validity, displayed
payment terms and delivery wording. The last fields are optional explicit
display text, **not** payment-plan, delivery or approval overrides. No validity
period, delivery promise, payment wording or legal text is supplied by default.
Terms and Conditions are prominently **pending the owner's supplied text**.

English/Arabic labels and supplied text are rendered with Arabic direction.
Missing translations/images/contact content are explicitly flagged; no real
company translations or images are fabricated. Existing English descriptions
remain the fallback name where an approved separate English name is absent.
Images are restricted to safe local raster paths; no external image fetch is
introduced. Historical quotes are not backfilled with new presentation facts.
Migration `0022_quotation_presentation` adds only optional metadata and an empty
legacy snapshot; it does not change grants, prices, transactions or Favorites.
It has been applied/rehearsed only in disposable test databases for this batch.

## Implemented scope

### Multi-line entry

The existing quote form accepts 1–40 explicitly selected lines. Each line has
its own authorized variant, quantity, specifications and (stock quotes only)
location. Users may add/edit/remove lines; at least one remains. Removing the
first row promotes the next row without discarding its values or other drafts.
The review lists approved selling prices, quantities, specifications, rounded
line estimates and a QAR quote estimate. Calculations use integer decimal units,
not binary floating-point money; server price snapshots and totals are final.
Invalid decimal precision, malformed specifications, unavailable prices/locations
and schema-size overflow receive line-specific validation before submission.

The existing backend has **one kind per quote**, not a per-line stock/special
selector. Stock lines require authorized locations; special-order lines omit
locations and retain existing terms/approval/configuration requirements. No
mixed commercial rules, new price sources, bands, grants or assignments are
created. Missing prerequisites remain unavailable; the former staff-only
quotation setup dependency is replaced by the narrow read described above. A quotation is
not stock allocation or evidence that each line is currently available.

The quote request, retry identity, pending owner/context guards and normal
unsaved-work protection are reused. BU/staff context changes keep incompatible
local drafts disabled until explicitly reset, rather than silently posting them
in another context. This is not persistent server draft storage.

The existing Retail & Sales destination now opens a retail presentation of the
same Purchasing Workbench, transaction forms and read-side components. This
replaces its planned-page placeholder, not the existing business services.
Purchasing and Delivery retain their existing modes.

Retail includes:

- Authorized paginated quote/order register and explicit quote detail lookup.
  Orders remain the existing quote workflow records, not a new order entity.
- Existing stock/special-order quote creation, variant/specification inputs,
  approved selling-price snapshots and price-decision reason.
- Existing assigned discount-ceiling feedback, discount approval, temporary
  holds, simulated quote collections and server-returned reservation feedback.
- Read-only readiness/delivery timestamps and exact per-line quantity progress.
  Stored reservation coverage is historical, not active reserved inventory.
- A button opening Delivery & After-Sales through the existing navigation
  callback, preserving selected BU/theme. Quote lookup there remains explicit;
  the link does not automatically carry or post a quote.
- Existing clear pending, denied, empty and configuration-needed states.

Procurement, commitments, receipts/landed charges, counterparty management,
returns and delivery/storage write controls are excluded from Retail. Those
remain in their existing workspaces. Retail does not run unrelated receipt,
commitment, storage or report-history queries merely to render the section.

## Existing authorization and contracts

The original reuse-only workspace added no backend behavior or grants.
The later owner-approved quotation batch adds the narrow read and optional
presentation schema above; the existing transaction handlers remain authoritative:

- `GET/POST /api/purchasing/quotes/`
- `GET /api/purchasing/quotes/<id>/?business_unit_id=<id>`
- Existing quote `discount-approval/`, `holds/` and `payments/` POSTs.

Quote list/detail use their existing active `create_order` BU grant and
synthetic-actor/approval-role checks, **not a newly introduced report grant**.
Default Purchasing/Delivery ancillary report registers keep their distinct
report authorization. Staff status alone does not grant quote authority.

The existing staff-only purchasing configuration and read-only directory
dependencies remain available only under their current gates. Ordinary sales
receive only the separately authorized quotation choices described above, not
the full configuration or supplier/customer administration directory.
When the UI cannot confirm required configuration, actions remain unavailable;
it does not infer prices, counterparties, approval roles or calendar rules.
Ordinary users retain existing separately permitted detail/action paths.
The new choices API is quotation-specific, not a configuration administration API.

Price/specification snapshots, existing role ceilings, stock versus special-
order payment terms, deposit/reservation gates and idempotency contracts are
unchanged. No automatic transaction retry is added. Existing context, selection,
request-version, per-action feedback/pending ownership and retry guards are
shared rather than reimplemented. Client retry memory retains its existing
component lifetime; durable cross-reload recovery is not introduced.

Retail sidebar children target real areas of this workspace. Existing
`retail` Favorites/My Page destination, user persistence and server checks are
unchanged: pinning is navigation only, never transaction or data authority.
No new sensitive field is requested or displayed. Report rows, exports and
personal report views are untouched.

## Acceptance and limits

New disposable PostgreSQL session/API tests verify price/specification retention
after a source-price change, original-result replay/conflicting-key rejection,
CSRF, stock deposit threshold/reservation, special-order 40% without stock
reservation, hold limit, cost-field exclusion, BU denial and immediate grant
revocation. Existing fixtures/roles are reused only in Django test databases.

Rendered tests exercise creation for both quote kinds, configuration/denial,
register/detail field rendering, delivery navigation, first-commit BU isolation,
and obsolete GET/POST success/denial. Isolated built-App Chromium checks cover
Favorites/My Page, keyboard quote lookup, BU theme/context, narrow layout,
Delivery navigation and denied access without real transactions.

Exact fresh commands/results and runtime limitations are in `REVIEW_HANDOFF.md`.
No signed-in shared-preview acceptance, real payment, company accounting policy,
full Phase 1 completion or production readiness is implied.

No company decision is required for this reuse-only scope. Expanding ordinary
sales configuration/directory visibility, assigning real users, special-order
value bands, invoicing/financial posting, partial-delivery terms and financial
return behavior remain separate decisions/work.
