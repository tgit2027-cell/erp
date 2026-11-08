import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Payables from "../src/Payables";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: URL[];
let empty = false;
let canPay = true;

const buckets = [
  { key: "current", label: "0–30 days" }, { key: "days_31_60", label: "31–60 days" },
  { key: "days_61_90", label: "61–90 days" }, { key: "days_91_120", label: "91–120 days" }, { key: "over_120", label: "Over 120 days" },
];
const zero = { current: "0.00", days_31_60: "0.00", days_61_90: "0.00", days_91_120: "0.00", over_120: "0.00" };
const aging = () => ({
  unit: { id: 1, code: "TVL", name: "Tivoli" }, as_of: "2026-10-31", buckets, currency: "QAR", can_pay: canPay, policy_label: "TEST ONLY policy.",
  suppliers: empty ? [] : [{
    id: 5, code: "TEST-SUP-1", name: "TEST ONLY Milano Furniture", total: "12894.00", oldest_days: 125, waiting_approval: "1800.00",
    buckets: { current: "0.00", days_31_60: "0.00", days_61_90: "0.00", days_91_120: "0.00", over_120: "12894.00" },
    items: [
      { receipt_id: 8, received_on: "2026-07-28", age_days: 125, bucket: "over_120", paying_for: "goods", label: "Goods", currency: "EUR", booked: "8000.00", cleared: "0.00", owed: "8000.00", owed_in_currency: "2000.00", waiting_approval: "0.00" },
      { receipt_id: 8, received_on: "2026-07-28", age_days: 125, bucket: "over_120", paying_for: "clearance", label: "Clearance", currency: "QAR", booked: "1800.00", cleared: "0.00", owed: "1800.00", owed_in_currency: null, waiting_approval: "1800.00" },
    ],
  }],
  totals: empty ? zero : { ...zero, over_120: "12894.00" }, total: empty ? "0.00" : "12894.00", waiting_approval: empty ? "0.00" : "1800.00",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const cells = (selector: string) => [...container.querySelectorAll(selector)].map((cell) => cell.textContent);

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  empty = false;
  canPay = true;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(url);
    if (url.pathname === "/api/finance/payables/") return json(aging());
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("Finance sees what is owed to each supplier by age, with payments waiting for approval", async () => {
  act(() => root.render(<Payables selectedUnitId={1} />));
  await flush();
  assert.equal(calls[0].searchParams.get("unit"), "1");
  assert.deepEqual(cells(".rv-tiles strong"), ["12,894.00", "—", "—", "—", "—", "12,894.00", "1,800.00"]);
  assert.equal(container.querySelector('[aria-label="Age by"] [aria-pressed="true"]')?.textContent, "By days");
  assert.match(container.querySelector(".rv-table tbody th")?.textContent ?? "", /Milano Furniture.*TEST-SUP-1 · 2 unpaid items · oldest 125 days · 1,800.00 waiting for approval/);
  assert.ok(container.querySelector("td.rv-late"));
  assert.equal(container.querySelector('a[href="#/finance/supplier-payments"]')?.textContent, "Go to supplier payments");
});

test("opening a supplier lists the goods and each charge, in QAR and in their own currency", async () => {
  act(() => root.render(<Payables selectedUnitId={1} />));
  await flush();
  const toggle = container.querySelector(".rv-link") as HTMLButtonElement;
  await act(async () => toggle.click());
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.deepEqual(cells(".rv-invoices tbody td:last-child"), ["EUR 2,000.00", "—"]);
  assert.match(cells(".rv-invoices tbody td:nth-child(2)")[1] ?? "", /Clearance1,800.00 QAR waiting for approval/);
});

test("nothing owed, no payment link without the permission, and refusals are said plainly", async () => {
  empty = true;
  canPay = false;
  act(() => root.render(<Payables selectedUnitId={1} />));
  await flush();
  assert.match(container.querySelector(".rv-empty")?.textContent ?? "", /Tivoli owes nothing/);
  assert.equal(container.querySelector('a[href="#/finance/supplier-payments"]'), null);
  globalThis.fetch = (async () => json({ detail: "You need permission to view the general ledger or to prepare or approve supplier payments in this business unit." }, 403)) as typeof fetch;
  act(() => root.render(<Payables key="other" selectedUnitId={3} />));
  await flush();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /You need permission/);
});
