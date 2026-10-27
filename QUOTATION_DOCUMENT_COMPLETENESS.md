# Quotation completeness — authorized synthetic preview

## Source and scope

Owner's supplied field inventory and descriptions of two-page bilingual
quotation/invoice references are the requirements for this increment. The
actual PDFs were **not available in the workspace** during implementation:
no claim is made to have visually matched their typography or pagination.
No real reference identifiers, customer records, signatures or product data
were copied. The invoice description is reference-only, not invoice/posting
implementation authorization.

This extends the existing authorized quote entry/detail preview. There is
**no new print, download, PDF or export control/endpoint/permission**. Existing
report-export authority does not automatically establish quotation-print
authority. Native browser/operating-system facilities are not a new application
entitlement.

## Required fields and implementation

| Owner requirement | Source / current behavior |
| --- | --- |
| Company identity/contact header | New quotes snapshot the owner-confirmed registered name **Tivoli Group L.L.C** separately from the **Tivoli** trademark/wordmark, on every page. Arabic registered name is not supplied or inferred. Safe configured contact text remains optional. Historical snapshots retain their recorded identity. |
| Quotation reference/date/salesperson | Existing quote ID, original server-local quote date and authenticated username. Same ID repeats on all preview pages; no new numbering/date policy. |
| Customer account/name | Existing authorized selected customer code and bilingual name snapshot. No master-data creation/editing by sales. |
| Contact person, project, address | Optional explicit English/Arabic quote display fields, separate from existing unstructured customer contact. No inferred CRM/project linkage or translation. |
| Phone/mobile/email | Optional bounded quote display text; email is validated. No messages, address lookup or external calls. |
| Item ID/name/category/image/description | Existing approved item snapshot, safe same-origin image rules and explicit missing-image/translation states. No catalog, supplier or cost expansion. |
| Specifications/variant | Existing submitted specifications and existing variant SKU; English/Arabic specification text has appropriate direction. No invented specifications. |
| Quantity/unit | Existing exact transaction quantity. Optional explicit English/Arabic **display-unit** labels aligned to line order; no UOM conversion, packaging multiplier, catalog edits or posting changes. Missing unit is not silently “pcs”. |
| Fixed price/authorized discount/totals | Existing immutable approved-price revision and existing role ceiling, decimal-safe backend amounts. Preview shows saved values, not recomputed floating-point totals. |
| BU/department | Existing selected BU snapshot; optional explicit department display text. |
| Validity/delivery estimate | Explicit existing date and optional display-only delivery estimate. Blank remains unspecified; no promise or fulfillment gate is created. |
| Display payment terms/notes | Optional English/Arabic text only. Existing payment, reservation, readiness and delivery rules remain authoritative. |
| Signatures | Blank customer/salesperson signature and name/date spaces, not electronic-signature capture, acceptance or approval. |
| Long quotations/page numbering | Four item lines per fluid-height **screen preview page**; repeated header/reference and bilingual page numbers. Grand totals, terms-status and signature spaces appear once on the final page. No fixed A4/physical-print pagination claim. |

All entry text is optional presentation metadata under the existing quote
capability: missing commercial inputs are visible, not invented or made
mandatory as a new policy. Quotes without a presentation snapshot retain their
existing operational detail; sparse historical snapshots show unavailable
fields. There is no fabricated historical backfill and no schema migration.

### Operator configuration, not a new administrator power

`QUOTATION_COMPANY_HEADERS` is an optional Django settings mapping keyed by
existing legal-entity code. Its safe snapshot allowlist is now only `contact_en`
and `contact_ar`; arbitrary settings are not copied. An absent mapping means
missing contact content. The new-document registered name is exactly
**Tivoli Group L.L.C** and the trademark is **Tivoli**, as confirmed by the owner.
Neither a sales request nor this contact configuration can override them.
An old `name_ar` setting is not adopted as an Arabic translation of the newly
confirmed registered name. There is no new web configuration API or assignment.
Accounting legal-entity records are not renamed by this presentation correction.

Example shape only (synthetic placeholders):

```python
QUOTATION_COMPANY_HEADERS = {
    "TEST-ENTITY-CODE": {
        "contact_en": "TEST ONLY configured company contact",
        "contact_ar": "",
    },
}
```

Contact fields and notes are snapshotted with the quote; later master-data or
configuration edits do not rewrite it. Idempotent retry returns the original
result; a changed presentation payload conflicts with a reused key.

## Explicit pending decisions — not operative template terms

- The reference's **30-day validity** is a **proposed configurable template
  default**, not applied or approved as a business rule. Users still supply an
  explicit valid-until date; no automatic period is inserted.
- **Resolved:** the owner confirmed **Tivoli Group L.L.C** as the exact English
  registered name and **Tivoli** as the trademark/logo name. Arabic registered
  name and approved contact content remain unsupplied; do not invent them.
- Terms and Conditions remain **pending owner-supplied approved text**.
  Legacy reference clauses concerning seven-day purchase returns,
  store-credit-only refunds, 100% reservation and 3% weekly storage conflict
  with newer instructions. They must not be copied into operative terms.
- Existing owner-approved business controls do not themselves become approved
  legal T&C copy. Displayed payment text cannot override their gates.
- Hold-stock blocking, procurement authority, actual user assignments,
  special-order value bands and Finance rules are unchanged/pending.

There is no implemented invoice document screen/API in the current prototype.
The owner-confirmed identity must also be used when such a view is approved
and implemented, but this correction does not create invoicing or posting.

## Suggested enhancements — owner review only

Not implemented or treated as approved requirements:

1. Authorized PDF/print/download after explicit quotation-document capability
   and audit requirements are confirmed.
2. Approved reusable company/contact/translation profiles and item master units,
   with separately approved editing authority.
3. Physical A4 page-break/template controls once output entitlement is settled.
4. Applying a configurable 30-day proposed validity default only after approval.
5. Digital signatures only after consent, authority and evidence requirements
   are supplied; current spaces are blank only.

## Evidence and team review

See the newest [handoff](REVIEW_HANDOFF.md) for exact fresh command outcomes and
the [Retail checklist](RETAIL_TEAM_ACCEPTANCE.md) for synthetic review steps.
PostgreSQL snapshot/validation/access tests are in
`foundation/test_quotation_choices.py`; rendered entry/document tests in
`scripts/purchasing-quote-lines-ui.test.tsx`. Real session/API preview scenarios
live in `scripts/retail-real-browser.test.mjs` and run only through the verified
disposable Retail harness. Shared authenticated acceptance is separate.
