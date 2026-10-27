# Tivoli ERP roadmap

Updated 21 October 2026. Synthetic data only; nothing here is production-ready. The detailed batch-by-batch record from 16 to 21 October is kept in [docs/history](docs/history/MILESTONE_ROADMAP_2026-10-16_to_2026-10-21.md). Decision IDs (I1, P1, F1 and so on) refer to the owner's [decision pack](docs/DECISION_PACK.md).

## Built (synthetic, tested)

| Area | What exists |
| --- | --- |
| Organization and access | Legal entity, three business units, brands, location tree, effective-dated grants re-checked on every request |
| Stock ledger | Balances, immutable movements, reservations; on-hand, held-on-quote and free-to-sell quantities |
| Stock transfer requests | Showroom admin requests, showroom manager approves or rejects with a reason, warehouse sets the date, sends and confirms arrival; goods in transit cannot be sold (I1, I2) |
| Procurement receiving | Purchase commitments, partial receipts, landed charges allocated by purchase value |
| Retail quote to delivery | Stock and special-order quotes, price snapshots, discount ceilings and approvals, 3-working-day holds that block other sales and transfers, simulated payments, reservation at 50% paid, readiness, delivery, storage charges |
| Journals | QAR journals on receipt and delivery under test-only accounting assumptions |
| Returns | Intake and inspection within 14 days; no refund, credit or stock put-back |
| Reports and exports | Permission-separated panels, XLSX/DOCX exports with audit, saved views |
| Warehouse capacity | Per-location capacity with separate assignments and audit |
| Quotation document | Bilingual screen preview; no PDF or download |
| Finance lab | 14 isolated draft editors that post nothing; frozen, no new editors |

## Next: finish the first executable proof

The architecture spec's section 6 scenario is the current authorized scope. Use the existing synthetic test policy until Finance approves real rules.

1. **Multi-currency purchase.** Transaction currency and exchange rate with recorded source on purchase commitments and receipts; journals still balance in QAR. Replace the QAR-only constraints with a currency table. Final rules wait on P1 and F5. *Built, provisional:* QAR, USD, EUR and GBP; the rate, its source and its date are typed on each document (no rate feed yet); a receipt against a commitment keeps the commitment's currency but takes its own rate on the arrival day; stock values and journals are in QAR; each landed charge has its own currency: shipping may be USD or EUR at a manual rate, customs clearance is always QAR (owner decision, 2026-10-21).
2. **Customer invoice.** Issued at delivery under the test policy, linked to the quote, delivery and journal. Final rules wait on F3, F4 and F8. *Built, provisional:* one numbered invoice per delivered quote (`<unit>-INV-<year>-<n>`, gap-free per unit and year), lines linked to the delivery journals, payments before delivery applied and the rest shown as balance due, tax rate 0%. Read-only list and printable invoice under Retail → Customer invoices. Owner decision F4 (2026-10-21): customer payments before delivery post Dr cash / Cr customer deposits (a liability); at delivery the deposits are applied against the receivable; later payments settle the receivable. Storage payments post nothing yet.
3. **Reconciliation report.** Ties receipts, landed cost, stock value, deliveries, invoices and payments for the scenario to the expected fixture values in the technical foundation spec. *Built, provisional:* 22 read-only checks per business unit, each comparing two independently stored records (receipts, stock ledger, journal, sales facts, invoices, payments). Staff-only page under Reports → Scenario reconciliation. A test runs the spec's 10-chair fixture and asserts QAR 12,000 received, 9,600 remaining, 4,000 revenue and 2,400 cost of goods sold.
4. **First full proof run.** *Built:* `manage.py seed_first_proof` runs the whole proof on the demo data through the normal services: EUR purchase order, receipt with USD shipping and QAR customs, sale with deposit and balance, delivery with invoice, a deposit held on an open order, and reconciliation (22 of 22). Fixed idempotency keys make it safe to run again; `foundation/test_first_proof.py` asserts the values.

Each item needs PostgreSQL tests, including rollback and duplicate-request tests, before it is called working.

## Blocked on owner decisions

| Work | Waiting on |
| --- | --- |
| Cross-business-unit transfers, per-location transfer permissions | Operations head |
| Partial delivery, cancellation, quote expiry | I5, R3 |
| Procurement access, item-to-BU catalog scope, units of measure | P2, P3, P4 |
| Terms and Conditions, Arabic company identity on documents | R1, R2 |
| Quotation PDF output | R4 |
| Real customer master | R5 |
| Real notifications | R6 |
| Operational GL, AP/AR, refunds, period close, approvals | F1 to F10 |
| Hosting, production login, production settings | T2, T3, T4 |

## Later

HR and payroll, project sales, wood factory, fit-out, full CRM, and the move from synthetic to real data with a Dynamics parallel run.
