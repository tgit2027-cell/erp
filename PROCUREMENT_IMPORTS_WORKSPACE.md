# Procurement & Imports — synthetic operational workspace

This is an arranged entry point over existing purchasing services, not a new
procurement engine or approval/access policy. All example transactions and
fixtures are synthetic; the existing QAR accounting assumptions are test-only.

## Existing functionality reused

- Create one purchase commitment with independently described variant lines,
  ordered quantities and manually supplied QAR purchase inputs.
- View server-returned ordered, received-to-date and remaining quantities per
  line. Select an existing commitment/line explicitly; infer neither supplier,
  variant, commitment nor unit conversion.
- Prepare successive receipts against selected lines, preserving per-line
  remaining limits, original request identity, locking and atomic rollback.
- Upload CSV or XLSX, select worksheet/map columns and explicitly map already
  authorized commitment lines. Review source quantity/unit and local warehouse
  enrichment before handing a draft to the existing receipt form.
- Upload, selection and review alone do not post a receipt, stock, reservation,
  cost or accounting record. Posting requires the existing explicit receipt
  submission and server-side permission/configuration checks.
- Existing eligible landed charges retain purchase-value allocation,
  decimal rounding and reconciliation. No new charge eligibility, late-cost
  or accounting rule is introduced.
- Existing paginated receipt/landed-charge views retain their independent
  report-panel/data scope and cost masking. A receiving permission does not
  grant a report or financial-data capability.
- Supplier references and directory choices follow existing API permissions;
  the staff-only directory does not become ordinary-user catalog access.

## Presentation and navigation

The existing `procurement` destination and per-user Favorite open the focused
workspace. Sidebar children target existing commitment, progress, receipt,
packing-review and register functions. They are presentation navigation, not
new URL routes or permission grants. Existing BU themes and Favorites storage
are unchanged.

The presentation reuses PurchasingWorkbench, PurchasingTransactions and
PurchasingReadside in procurement mode. Unrelated sales quote, payment,
delivery, return and policy-administration forms are omitted. The general
Purchasing Workbench remains available independently.

## Authorization and request ownership

Selected business unit, legal entity and explicit location checks stay on the
server. Missing grants, approval/configuration prerequisites or scoped data
yield existing denied/unavailable/empty states. No shared assignments, users,
prices or counterparties are created to make the screen artificially writable.

Existing unit/staff context guards, GET invalidation, POST completion ownership,
idempotency replay/conflict handling and busy indicators remain authoritative.
Pending transactions are not cancelled on navigation. Neither a late result
nor a Favorite can grant access or restore another unit's content.

## Deliberately excluded

Foreign currency, supplier invoices, shipment/customs processes, late-cost
valuation, new accounting posting, approval rules or role assignments are not
implemented here. Those require their own approved scope and, where relevant,
Finance/access decisions. This workspace does not validate Qatar law or claim
production readiness.

Exact commands, focused/full results, browser checks and limitations for this
batch are recorded in the latest `REVIEW_HANDOFF.md` entry.
