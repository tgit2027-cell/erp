# Milestone 2 transaction prototype — test-only assumptions

This increment is a deterministic backend prototype for synthetic data in `APP_ENV=demo`. It adds no transaction API or user interface. The accounting mappings and calculations below are test fixtures only; they are **not Tivoli-approved policy**, accounting advice, or approval to post real transactions.

## Explicit synthetic fixture

1. Receive 10 synthetic chairs at QAR 1,000 each: purchase value QAR 10,000.
2. Allocate QAR 2,000 of eligible synthetic landed charges by purchase value: total inventory QAR 12,000, or QAR 1,200 per chair.
3. Deliver two chairs at QAR 2,000 each: revenue QAR 4,000, cost of goods sold QAR 2,400, and remaining inventory QAR 9,600.

For this fixture only, calculations assume no tax, one cost layer, QAR, and revenue recognition at delivery. Monetary values are rounded to two decimals using decimal arithmetic and half-up rounding; allocation remainder cents use stable receipt-line order.

## Prototype boundaries

- Services refuse execution unless `APP_ENV=demo` and the supplied catalog/location records are synthetic.
- Test chart-of-account codes and receipt-clearing mappings exist only to make synthetic journals balance per legal entity in QAR.
- A reservation reduces available quantity but does not create a stock movement. Delivery consumes the active reservation.
- Same-business-unit transfers between locations carry the current proportional share of balance value; they do not create a journal entry. The prototype does not model transit stock or cross-unit/entity transfers.
- Delivery and transfer valuation prorate current aggregate balance value, effectively a simple average-cost calculation. The service has no cost-layer ledger or configurable, finance-approved FIFO/weighted-average policy.
- Posted journals and stock movements are immutable. This increment has no reversal, stock-count correction, reservation-release/expiry, approval, or reconciliation workflow.
- These services have no HTTP endpoint, UI, external integration, supplier/customer/payroll data, or production database migration path.

## Decisions required before any real use

An accountable finance/inventory owner must approve the chart of accounts and posting semantics; eligible landed-charge categories and allocation basis; inventory valuation and cost-layer policy; reservation lifecycle; delivery/fulfillment and revenue-recognition timing; tax and currency treatment; transfer rules, including inter-unit/entity and in-transit accounting; and correction/reversal controls. None of these test assumptions should be copied into real operations without separate review and approval.
