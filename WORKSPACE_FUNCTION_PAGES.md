# Focused function pages — synthetic prototype

## Navigation contract

Implemented functions in all sidebar workspaces use local fragment routes of the form
`#/section/function`. A child destination selects a focused view rather than
scrolling through every workspace function. Main section identities, existing
controls and server APIs remain unchanged.

| Section | Existing function destinations |
| --- | --- |
| Retail & Sales | `main`, `quote`, `actions`, `register` |
| Procurement & Imports | `main`, `commitments`, `progress`, `receipt`, `packing`, `registers`, staff-only `suppliers` |
| Inventory & Warehouses | `main`, `locations`, `receipt`, `commitments`, `progress`, `packing`, `registers`, `capacity` |
| Delivery & After-Sales | `main`, `quote`, `handover`, `quotes`, `storage`, `reservations`, `deliveries`, `returns` |
| Governance & Administration | authority overview `main`, staff `presentation`/`purchasing-config`/`reporting`, existing report-manager `report-panels`, superuser `capacity-admin`/`capacity-audit` |
| Purchasing Workbench | `main` launcher, `quote`, `transactions`, `registers` |
| Reports | `main`; management uses the canonical Governance `report-panels` destination |
| Finance & Accounting / HR & Payroll | `main` only: explicitly planned, not functioning business modules |

The exhaustive labels, Favorite keys, compatibility aliases and acceptance
status are recorded in `ALL_SECTIONS_DESTINATION_MATRIX.md`.

Examples: `#/procurement/progress`, `#/inventory/locations`,
`#/delivery/returns`, `#/governance/capacity-audit`.
Unknown functions or unavailable administrative destinations must show an
explicit unavailable state, not silently display the previous function.
An existing function without transaction/data authority retains its existing
denied or configuration-needed state; navigation never creates authority.

Fragments stay in the browser and require no new Django routes or proxy
configuration. A copied function URL or refresh selects that view after the
existing authentication/context loading. Browser back/forward restores the
function destination. No item rows, customer details, costs, credentials or
permission assignments are stored in a function URL.

## State and scope boundaries

- Function navigation within a workspace must preserve existing form identity,
  drafts, pending POST outcomes and retry keys. Inactive authorized controls
  can remain mounted, but are not visible, keyboard-focusable or presented as
  part of the active page.
- BU/location changes still invalidate data through the existing context
  guards. History changes the function, **not** the selected business unit:
  going back after selecting another BU must not restore old-unit records.
- Capacity measurements retain independent explicit physical-location
  assignments. Governance designation/assignment/audit remains metadata-only.
  Neither navigation nor staff/superuser display implies measurement access.
- Existing section-level Favorites and My Page remain per-user navigation
  preferences. Implemented non-main functions can also be pinned using
  server-allowlisted `section.function` keys. The same API persists keys, not
  report rows, drafts or authority. Main views reuse section keys.
- Legacy links normalize to focused canonical destinations: Reports management
  to Governance, the redundant authority page to Governance main, Inventory
  receiving to receipt, and the old Delivery register group to Quotes.
  The Purchasing Workbench is no longer an aggregate scroll page.
- Finance/HR placeholders and unapproved functions do not become invented
  operational pages. This navigation acceptance is not production readiness.

## Verification boundary

Rendered tests and isolated native Chromium tests use only synthetic mocked
APIs. Backend permission/regression checks run on disposable PostgreSQL 16.15,
not shared-preview or production data. Exact fresh results, implementation
details and remaining limitations are recorded in the latest
`REVIEW_HANDOFF.md` entry.
