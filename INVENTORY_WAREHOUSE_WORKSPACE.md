# Inventory & Warehouses — synthetic operational workspace

## Capacity access/refresh recovery — bounded team review

Menu: **Inventory & Warehouses → Warehouse capacity** (`#/inventory/capacity`).
Capacity is assigned independently **per physical location**, not inferred
from the selected brand, staff status, stock access or system-administrator
status. A brand switch does not itself revoke a physical-location assignment.
No assignments are seeded for team users; an unassigned account must see the
existing unassigned state. Do not change shared grants/accounts for testing.

- Use **Refresh capacity access** to recheck existing assignments. This reads
  only; it never repeats a save. An unchanged authorized refresh preserves
  another location's draft.
- Confirmed loss of viewing removes that location's measurements and controls.
  Losing EDIT alone while an explicit VIEW remains shows stored read-only
  measurements, not a local draft as a confirmed warehouse measurement.
- A temporary read failure explicitly identifies prior loaded measurements.
  After a denied save, saving stays blocked until a successful access check;
  local drafts are retained while the read is uncertain.
- Later accepted snapshots cannot erase edits made after submission or during
  refresh. Success feedback does not claim newer unsaved edits were saved.
- Floor area remains **m²**; received-goods volume remains **m³**. No conversion,
  volumetric availability, stock movement or finance behavior was introduced.

Revocation/denial/concurrent-response steps run **only in the isolated automated
fixture**, not by changing shared team assignments. Fresh evidence: **23 focused
capacity frontend + 50 related frontend, 23 focused PostgreSQL, 3 real-backend
browser and 175 contrast checks passed**, with exact commands/limits in
`REVIEW_HANDOFF.md`. Native checks covered actual nonstaff session/CSRF,
desktop/320px view loss, desktop EDIT-only loss, validation, a lost allowed
read acknowledgement and completed BU switching. This is reactive recovery,
not continuous revocation detection, shared team sign-off or production readiness.

## Existing operational workspace scope

### Register location-filter recovery

Menu: **Inventory & Warehouses → Inventory & transfer registers**
(`#/inventory/registers`); choose **Inventory** or **Transfer history** using
the existing Register selector. A refreshed scope may remove a location choice.
The selected ID now remains visibly **unavailable**, with no cached rows or
automatic all-location request. Choose a currently permitted location or use
**Clear unavailable location filter** deliberately; clearing requests the
current BU's authorized report at page one while preserving dates. A temporary
scope failure retains the selection/dates rather than misrepresenting them as All.

Scope still comes from independent report-panel/data rights, not `read_stock`,
capacity access or sidebar visibility. Explicit old-location requests are denied
by the backend. Clearing is not a permission grant. A fresh BU-wide report can
still contain authorized historical references to that location under existing
report semantics; it is not the discarded old response. No new historical
visibility rule or transfer-posting action was implemented.

Fresh evidence: **23 focused / 333 full frontend**, **8 focused PostgreSQL 16.15**,
**2 actual authenticated browser cases** (1280px Inventory and 320px transfer
history), **175 contrast**, type/build/schema/system/diff checks passed. Exact
commands and limits are in REVIEW_HANDOFF. Scope changes and grants are exercised
only in disposable fixtures; do not change shared assignments/membership for
manual team testing. No new app migration or shared seed is required.

This section reuses the existing ledger, receiving and capacity foundations.
It is not approval of production valuation, warehouse allocation, transfer
authority, or scanner hardware/encoding.

## Stock and item/location identity

- The operational quantity source is **StockBalance**, never StockExample.
  Unreserved quantity is the exact server decimal string for on hand minus
  reserved; it is not an approved available-to-promise calculation.
- Search uses existing item code/description, variant SKU/description, brand
  and location code/name semantics. A keyboard-wedge device can enter plain
  text and Enter into the same search form. No barcode format is parsed,
  barcode field inferred, label generated or hardware integration claimed.
- Explicit location selection narrows the current authorized business unit.
  Search, counts and pagination remain server-scoped. A business-unit change
  must not send the previous unit's location in the new context. Changing a
  location immediately clears old rows/counts/detail, preserves search and
  page size, and loads page one. Obsolete results cannot restore the old page.
- Quantity detail uses the existing GET-only
  `/api/stock-balances/<id>/?unit=<id>&location=<optional-id>` endpoint.
  Like the list, it now validates explicit location membership before
  filtering. An allowed but different location returns 404; a location
  outside the selected unit returns 403; a malformed ID returns 400.
- Detail has the same exact 13-field allowlist as the quantity list:
  `id`, `item_code`, `item_description`, `brand_name`, `brand_code`,
  `variant_sku`, `variant_description`, `location_code`, `location_name`,
  `unit_code`, `unit_name`, `quantity`, `is_synthetic`.
  No cost, inventory value, reserved amount, supplier or movement history is
  introduced into this read_stock response.

## Receiving, capacity and registers

Receiving reuses the existing commitment/receipt and packing-list preparation
components rather than implementing another transaction service. Existing
staff-only configuration/directory reads, create_order and approval-role gates,
location checks, explicit commitment/item mappings, quantities/source units,
review invalidation, idempotency and rollback remain authoritative.
Its warehouse presentation hides unrelated quote creation/lookup/actions and
returns. A successful current-context receipt clears prior bucket detail and
reloads the current ledger filter; a late other-context completion cannot
refresh the wrong BU.

Uploads, worksheet selection, item/commitment mapping and reviewed local drafts
are not receipt posting. Only the existing explicit receipt submit can call the
existing authorized endpoint. No supplier invoice, currency conversion,
automatic catalog match or unit conversion is added.

Warehouse capacity retains its own independent per-physical-location
view/edit assignment. Stock/report access and Django staff status do not grant
it. System-administrator assignment remains superuser-only; nothing is seeded.
Capacity assignments are independent of the selected ledger BU: the existing
capacity API returns only explicitly assigned physical locations. Floor area
in m² and received-goods volume in m³ remain separate manually maintained
measures, without receipt-driven updates or volumetric availability claims.
A verified authenticated capacity assignee can reach this section even when
no stock/purchasing business-unit context is available. That does not enable
stock, receiving, reports or a fallback unit.

Inventory and transfer registers use the existing report-panel assignments
**and** report-data capabilities. read_stock does not imply either. Queries
select the current BU explicitly, never widen to company scope, and use the
existing report location/date/pagination contract. Cost/value fields are not
part of this workspace's register presentation; reserved quantities remain
hidden here too. Explicitly assigned Inventory reports may show their existing
on-hand quantity, independently of the ordinary read_stock unreserved view.
The inventory report is a
current balance snapshot; transfer date filters use the existing created-at
semantics, not an invented fiscal or movement-effective calendar.
The scoped Refresh control rechecks existing report APIs; these registers
introduce neither saved report results nor a separate export path.

The sidebar retains Inventory's identity and Favorite destination, with
existing-function child targets for quantities, permitted locations, receiving,
packing preview, registers and capacity. Receiving/register disclosures open
when their corresponding child is selected. On narrow screens the existing
active-parent flyout can be explicitly collapsed before using page controls.
New light-surface labels use the existing content-muted token, not the
sidebar-muted token. Contrast regression checks cover all four built-in
palettes and a generated custom palette; native browser checks also measure
the actual rendered detail labels.

## Deliberately unavailable behavior

- Transfer posting: the existing deterministic transfer service is tested,
  but there is no approved warehouse transfer REST/UI authorization contract.
  This batch adds no transfer write endpoint, button or grant.
- General barcode decoding, hardware integration and barcode catalog
  ownership are unconfigured; existing codes remain plain search input.
- Unstocked master-catalog/BU matching, inferred supplier or commitment,
  unit conversion and automatic storage/volume allocation remain unavailable.
- New valuation methods, late landed-cost treatment, approval assignments,
  real user access, financial posting policy and production stock promises
  require explicit company decisions, not synthetic UI assumptions.

## Verification

Fresh exact commands, totals, initial failures and final outcomes are recorded
in the Inventory & Warehouses entry in `REVIEW_HANDOFF.md`. Backend tests use
an explicitly verified disposable PostgreSQL 16.15 cluster, never the shared
preview database. Rendered/browser fixtures are fictional and mocked.
No signed-in shared-preview or physical-scanner acceptance is implied.
