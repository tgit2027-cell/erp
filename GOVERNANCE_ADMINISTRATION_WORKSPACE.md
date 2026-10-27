# Governance & Administration — existing-authority workspace

This synthetic section arranges existing functionality. It introduces no API,
permission, policy engine, account, default assignment or production authority.

## Function destinations

The existing Governance destination/Favorite opens an authority overview.
Sidebar children navigate to actual functions:

| Function | Existing authority and service |
| --- | --- |
| Business-unit palettes/custom colors | Existing staff palette APIs; presentation only |
| Report-panel assignment/revocation | Existing `can_manage_report_panels` authority and `/api/governance/report-panels/` |
| Purchasing configuration review | Staff-only GET `/api/governance/purchasing/{unit_id}/`; selected BU, no edits |
| Physical capacity designation/assignments | Existing superuser-only capacity administration APIs |
| Capacity audit metadata | Existing superuser-only paginated read API, no measurements |

These now open focused function views through browser fragment routes rather
than scroll-only anchors; see `WORKSPACE_FUNCTION_PAGES.md`. No Django routes
or authorization handlers were added. Favorites retain their server-supplied destination choices:
the existing Governance Favorite is staff-only. A nonstaff report manager
continues to use the authorized Reports destination/Favorite; rendering a
manager control does not grant the Governance Favorite or staff authority.

Staff, panel-management and superuser gates remain independent. Being staff
does not grant a location capacity assignment. Assignment administration does
not grant stock/purchasing/report data access or permission to read a physical
location's measurements.

## Reused controls

- The existing palette editor retains built-in/custom colors, contrast
  validation and explicit unit selection. Its global staff unit choices are
  not replaced with a new transaction permission scope.
- Report management reuses the existing panel-only manager, not a new general
  role, data-capability or user-management screen.
- Purchasing review reuses GovernancePanel and the existing API response.
  Missing roles, calendars, prices or bands are shown as unconfigured. No
  receipt, quote, payment or configuration-writing action is embedded.
- Capacity administration reuses the existing designation and assignment
  forms in an administration-only presentation. It does not fetch the
  `/api/warehouse-capacity/` measurement list. Location and recipient choices
  start blank in this presentation; nothing is assigned on mount.
- Audit display remains metadata-only. Inventory's independent, explicitly
  assigned capacity viewing/editing presentation remains unchanged.

Selected-unit configuration clears on the first changed-context render.
Obsolete responses are ignored; denials and staff visibility loss remove prior
configuration. Capacity/admin metadata is masked when superuser authority
changes. A missing stock organization does not hide independently authorized
administration, but the BU-specific purchasing review cannot invent a context.

## Deliberately unavailable

New DOFA/delegation rules, general user/action-grant administration, Finance
posting/refunds, HR/payroll/legal behavior and invented policy configuration
are unavailable. Existing reporting-specific backend API authorities are
unchanged; this screen exposes panel assignment/revocation only and does not
claim to revoke pre-existing API permissions.

No demo grants, role memberships, prices, counterparties or real identities
were populated. Tests create fictional permissions only in disposable
PostgreSQL fixtures or in-memory browser/API mocks.

## Verification

The latest `REVIEW_HANDOFF.md` entry records exact fresh commands/counts,
schema checks, native keyboard/mobile coverage, browser limitations and the
non-failing bundle-size advisory. Narrow Governance grids constrain intrinsic
table/form widths; wide audit content scrolls locally rather than expanding
the document. Existing system fonts and BU themes are preserved, with no new
external font dependency.
