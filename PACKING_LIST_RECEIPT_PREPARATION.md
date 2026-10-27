# Synthetic packing-list receipt preparation

This extends the existing browser-local CSV/XLSX preview, not the purchasing/accounting rules. Use fictional documents and test values only.

## Workflow

1. Open **Purchasing Workbench → Transaction workbench → Receive imported goods → Prepare a receipt from a synthetic packing list**.
2. Select a synthetic CSV or `.xlsx` workbook and explicitly map columns using the existing preview. Required source fields are item/supplier code, quantity and quantity unit. Description, supplier line reference, dimensions and separate supplier m²/m³ values are optional.
3. In **Prepare receiving lines**, explicitly choose a receiving mode:
   - **Direct receiving**: enter an exact catalog item code or variant SKU if the supplier code differs. Choose the exact authorized variant for **every** row; even a single candidate is never automatically accepted. Multiple variants are labeled ambiguous until explicitly selected.
   - **Existing purchase commitment**: choose an existing authorized commitment, then explicitly map each source row to one of that commitment's exact lines. The source code never selects a line. Ordered, received-to-date and remaining quantities are shown. Duplicate mappings are blocked rather than silently merged; consolidate such source rows manually before review. No global catalog or ledger-candidate lookup is used to determine commitment-line choices.
4. Choose an existing authorized receiving location and enter the confirmed receipt quantity, with at most two decimal places. No source quantity is silently copied or converted. Review source units yourself: the current receipt API has no unit-of-measure master/compatibility check.
5. Optional warehouse-confirmed m³ remains local preview enrichment. It is not a capacity edit, receipt field, stock valuation or m² conversion.
6. Resolve all invalid/unmatched/ambiguous/pending/denied rows, then explicitly acknowledge quantity/unit review and click **Use reviewed lines in receipt draft**. This sends no POST and creates no record. No row is silently omitted; the existing 80-line receipt limit applies.
7. In the existing receipt form, review any existing eligible landed charges. For direct receiving, select the configured synthetic supplier and enter each synthetic QAR unit value. For committed receiving, the chosen commitment fixes the supplier and the server reuses immutable committed unit values without exposing them in progress, the prepared draft or editable cost controls. Costs are never imported from the file.
8. Only **Record synthetic receipt** submits to the existing receipt endpoint, using its existing CSRF, capability/configuration, atomic posting, landed allocation, rollback and idempotency rules. A failed unchanged request retains its exact payload and retry key. Accepted receipt completion clears the copied packing draft.

Editing/replacing source data, column mapping, warehouse enrichment, receiving mode/commitment/line/variant/location/quantity choices, or withdrawing the review invalidates previously copied lines. BU/user/staff/refresh context changes mask the old local draft and require current choices/review, including A→B→A. Updated commitment progress resets preparation review. Old candidate responses are aborted or suppressed; they cannot replace a selected commitment or restore another context's options. Loading, empty or denied commitment progress never falls back to a direct receipt.

## Authorization and storage boundary

- Files are parsed in the browser, never sent to Django, external storage or another network service. Only exact lookup codes and the current unit are sent to read-only relative API endpoints.
- `GET /api/stock-receipt-candidates/` returns `variant_id`, `variant_sku`, `variant_description`, `item_code`, `item_description`, and `brand_name`, deduplicated and deterministically ordered by SKU/variant ID.
- The endpoint reuses current active `read_stock` authorization and selected-BU/legal-entity/explicit location membership. Optional out-of-scope location requests and revoked/cross-BU access are denied server-side. Candidate lookup does not confer `create_order` or any other action permission.
- Up to 80 exact codes per lookup, at most 80 characters each; more than 200 matching variants fail explicitly with advice to use exact SKUs rather than silently truncating an ambiguous choice set.
- Required receiving permission/configuration is the existing workbench availability check. No user, role, price, counterparty, approval threshold, membership or grant is seeded or changed. Existing read-only demo accounts may correctly see configuration-needed states.
- Commitment choices reuse the existing authorized `/api/purchasing/commitments/` response. The frontend loads its pages without changing the endpoint or its business-unit/role authorization. The prepared payload carries the explicitly selected `purchase_commitment_id` and each `commitment_line_id`/`variant_id`/`location_id`/confirmed `quantity`, omitting editable unit values. The existing receipt service remains authoritative for current remaining quantity, exact ownership, locks, atomic posting, rollback and idempotency.
- Source file, source reference/units/dimensions, worksheet contents and warehouse measurements are not persisted in receipt/stock/cost/capacity records. The explicit final POST contains only the already supported receipt fields.
- No stored supplier cost, landed cost, value, reservation amount, movement history or capacity contents are returned by the new lookup. User-entered synthetic receipt cost inputs follow the existing form behavior.

## Deliberate limits

Preparation now supports **direct and explicitly linked commitment receipts**. All mapped lines must belong to one commitment/supplier, with at most one source row per commitment line and at most 80 lines. No supplier/commitment/item match or quantity conversion is inferred. A concurrent receipt can reduce remaining quantity after review; the unchanged server rejects an excessive submission atomically. Refresh progress and review again rather than silently changing the posted quantities.

Direct catalog matching remains limited to authorized current ledger variants, not the global master catalog. A missing match may mean there is no authorized ledger bucket, not that an item does not exist. Broader master-catalog matching needs an explicit BU-to-catalog access relationship. Commitment mode may select only variants already disclosed by the existing authorized commitment progress, including variants without a ledger bucket; this is an explicit document reference, not new catalog access.

There is no automatic UOM conversion, supplier template, barcode interpretation, measurement persistence, external file storage, receipt approval policy or posting-policy change. Live signed-in browser verification was not performed; rendered synthetic mocked-HTTP and disposable PostgreSQL tests provide the functional evidence recorded in `REVIEW_HANDOFF.md`.
