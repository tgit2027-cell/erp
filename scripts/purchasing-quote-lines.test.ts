import assert from "node:assert/strict";
import { test } from "node:test";
import { formatQarCents, validateQuoteLineEstimates } from "../src/purchasing-quote-lines";

const prices = [
  { variant_id: 10, amount: "0.01", currency: "QAR" },
  { variant_id: 20, amount: "125.55", currency: "QAR" },
];
const line = (variantId: string, quantity: string, locationId = "11", specifications = "{}") => ({
  variantId, locationId, quantity, specifications,
});

test("calculates line prices, discount and totals with BigInt ROUND_HALF_UP cents", () => {
  const result = validateQuoteLineEstimates(
    [line("10", "0.50"), line("20", "2.25")], prices, "12.50", "stock", [11],
  );
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(result.lines.map((row) => [
    formatQarCents(row.listTotalCents),
    formatQarCents(row.netUnitCents),
    formatQarCents(row.netTotalCents),
  ]), [["0.01", "0.01", "0.01"], ["282.49", "109.86", "247.19"]]);
  assert.equal(formatQarCents(result.totalCents), "247.20");
});

test("special-order lines do not require a stock location", () => {
  const result = validateQuoteLineEstimates([line("20", "1.00", "")], prices, "0.00", "special_order", []);
  assert.equal(result.ok, true);
});

test("rejects excess quantity precision with actionable line-numbered error", () => {
  const result = validateQuoteLineEstimates([line("10", "1.001")], prices, "0.00", "stock", [11]);
  assert.equal(result.ok, false);
  if (!result.ok) assert.match(result.error, /Line 1: Quantity.*two decimal places/);
});

test("rejects unavailable prices, unauthorized locations and invalid specifications by line", () => {
  const missingPrice = validateQuoteLineEstimates([line("", "1.00")], prices, "0", "stock", [11]);
  assert.equal(missingPrice.ok, false);
  if (!missingPrice.ok) assert.match(missingPrice.error, /Line 1/);
  const denied = validateQuoteLineEstimates([line("10", "1.00", "99")], prices, "0", "stock", [11]);
  assert.equal(denied.ok, false);
  if (!denied.ok) assert.match(denied.error, /Line 1: select an authorized stock location/);
  const invalid = validateQuoteLineEstimates([line("10", "1.00", "11", "[]")], prices, "0", "stock", [11]);
  assert.equal(invalid.ok, false);
  if (!invalid.ok) assert.match(invalid.error, /Line 1: specifications must be a JSON object/);
});

test("enforces quote line count and exact discount precision", () => {
  const tooMany = validateQuoteLineEstimates(Array.from({ length: 41 }, () => line("10", "1.00")), prices, "0", "stock", [11]);
  assert.equal(tooMany.ok, false);
  const precision = validateQuoteLineEstimates([line("10", "1.00")], prices, "0.001", "stock", [11]);
  assert.equal(precision.ok, false);
});

test("enforces Decimal(12,2) quantity and Decimal(14,2) unit-price bounds", () => {
  const maxQuantity = validateQuoteLineEstimates([line("20", "9999999999.99")], prices, "0", "stock", [11]);
  assert.equal(maxQuantity.ok, true);
  const quantityOverflow = validateQuoteLineEstimates([line("20", "10000000000.00")], prices, "0", "stock", [11]);
  assert.equal(quantityOverflow.ok, false);
  if (!quantityOverflow.ok) assert.match(quantityOverflow.error, /Line 1: Quantity.*10-digit/);
  const maxPrice = [{ variant_id: 30, amount: "999999999999.99", currency: "QAR" }];
  assert.equal(validateQuoteLineEstimates([line("30", "1.00")], maxPrice, "0", "stock", [11]).ok, true);
  const priceOverflow = [{ variant_id: 30, amount: "1000000000000.00", currency: "QAR" }];
  const overflow = validateQuoteLineEstimates([line("30", "1.00")], priceOverflow, "0", "stock", [11]);
  assert.equal(overflow.ok, false);
  if (!overflow.ok) assert.match(overflow.error, /Line 1: Current unit price.*12-digit/);
  const numericPrice = [{ variant_id: 30, amount: 125.55, currency: "QAR" }];
  const imprecise = validateQuoteLineEstimates([line("30", "1.00")], numericPrice, "0", "stock", [11]);
  assert.equal(imprecise.ok, false);
  if (!imprecise.ok) assert.match(imprecise.error, /exact decimal string/);
});

test("rejects aggregate quote overflow even when each line fits its field", () => {
  const nearLimit = [{ variant_id: 40, amount: "999999999999.99", currency: "QAR" }];
  const result = validateQuoteLineEstimates(
    [line("40", "100.00"), line("40", "100.00")], nearLimit, "0", "stock", [11],
  );
  assert.equal(result.ok, false);
  if (!result.ok) assert.match(result.error, /Line 2: Quote list total.*14-digit/);
});
