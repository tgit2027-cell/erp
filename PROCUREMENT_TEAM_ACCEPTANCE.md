# Procurement & Imports — team acceptance checklist

**Synthetic test session only. Do not use real supplier files or post to the shared demo.**
These steps were checked in Chromium with real sign-in and backend responses,
using a disposable database. The automated session is erased when it finishes.
It does not create shared team accounts or leave a manual testing session open.
For manual review, use an owner-prepared isolated session and privately supplied
synthetic receiving credentials. If access or supplier configuration is missing,
stop: do not invent values or grant yourself access.

## Small sample inputs

Download the accompanying **TEST-ONLY-partial.csv** and **TEST-ONLY-partial.xlsx**.
The workbook has a cover sheet: explicitly choose **TEST ONLY Receiving**.
Both files contain only fictional descriptions, quantities and source units.

| Synthetic commitment line | Ordered | CSV receipt | After CSV: received / remaining | XLSX receipt | After both: received / remaining |
| --- | ---: | ---: | --- | ---: | --- |
| TVF-002-LIN | 10.00 | 4.00 | 4.00 / 6.00 | 3.00 | 7.00 / 3.00 |
| TVF-002-LIN-DEMO2 | 5.00 | 2.00 | 2.00 / 3.00 | 1.00 | 3.00 / 2.00 |

## Review steps and expected outcomes

1. **Sign in and select Tivoli.** Open **Procurement & Imports → Packing-list mapping & review**.
   An authorized receiving tester can continue; a brand-only stock reader must
   see unavailable transaction controls, not gain receipt authority.
2. **Choose the CSV file.** Map Supplier / item code, Item description, Quantity,
   Quantity unit and Supplier line reference to the five corresponding columns.
   Preview shows two rows. Uploading and mapping must create **no receipt**.
3. **Choose “Existing purchase commitment”.** Explicitly select the synthetic
   commitment, its matching line for each row, and the allowed receiving location.
   Confirm 4.00 and 2.00 with source unit **EA**. Read the source-unit acknowledgement.
   No item match, commitment or unit conversion is inferred.
4. **Prepare, then submit separately.** Tick the review acknowledgement and choose
   **Use reviewed lines in receipt draft**. Open **Prepare imported-goods receipt**.
   Check both prepared lines and fixed supplier, then choose **Record synthetic receipt**.
   Preparation alone must not post anything. Open **Commitment receipt progress**
   and compare each line with the “After CSV” column above.
5. **Repeat with the XLSX file.** Choose its receiving worksheet explicitly, remap
   columns/commitment lines and confirm 3.00 and 1.00 EA. Submit explicitly.
   Progress must match “After both”: exactly **two receipts**, not one per file row.
6. **Check retry and over-receipt safety.** Automation deliberately drops the first
   acknowledgement after the server commits, then retries the unchanged draft:
   it retains the same request identity and creates **no duplicate receipt**.
   With only 3.00 left on the first line, another CSV asking for 4.00 must be
   blocked during review. A separate direct-API rejection test also proves that
   one excessive line rolls back the entire request.
7. **Check boundaries.** Another BU's commitment or receiving location must be
   rejected. The receipt register must not show supplier/landed cost amounts
   without separate sensitive-data authority. Brand-only stock access must not
   permit receipt submission.

## Verified versus unavailable

**Verified:** actual CSV and multi-worksheet XLSX file selection, explicit mapping,
review-to-draft handoff, authorized successive partial receipts, per-line progress,
ledger quantities, unchanged retry identity, over-receipt rollback, access denials,
CSRF enforcement and cost-masked register reads. No shared grants were changed.

**Not added or approved:** automatic matching/conversion; saving source units,
references or warehouse measurements on receipts; foreign currency, supplier
invoices, new shipment/customs rules, late-cost valuation or new approvals.
Finance/HR policy-dependent work remains unchanged. These tests do not approve
real accounting or supplier policy, certify Office interoperability, establish
shared-preview team access or guarantee continuous preview availability.

Repeatable automated check: `bash scripts/verify-procurement-acceptance.sh`.
It uses existing installed tools and isolated synthetic fixtures, not shared credentials.
