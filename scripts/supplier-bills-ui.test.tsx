import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import SupplierBills from "../src/SupplierBills";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let me = "test_checker";
let canDecide = true;

const lines = [{ sku: "TEST-SOFA", description: "TEST ONLY sofa", received_quantity: "2.00", unit_cost: "1000.00", value: "2000.00",
  ordered_quantity: "2.00", ordered_unit_cost: "1000.00", ordered_currency: "EUR" }];
const supplier = { id: 5, code: "TEST-SUP-1", name: "TEST ONLY Milano Furniture" };
const held = {
  id: 7, receipt_id: 8, supplier, bill_number: "MF-2210", bill_date: "2026-10-20", currency: "EUR", amount: "2050.00",
  expected_amount: "2000.00", difference: "50.00", status: "on_hold", status_label: "Difference waiting for a second person",
  note: "Price rise", recorded_by: "test_maker", decided_by: null, decision_note: "", difference_value: null,
  journal_entry_id: null, received_on: "2026-10-18", can_void: true, lines,
  settlement: "", settlement_label: "", paid: null, waiting: null, outstanding: null,
};
const overview = () => ({
  unit: { id: 1, code: "TVL", name: "Tivoli" }, can_record: true, can_decide: canDecide, me, policy_label: "TEST ONLY policy.",
  needing_bill: [{ id: 9, supplier, received_on: "2026-10-25", currency: "EUR", expected_amount: "2000.00", lines }],
  waiting: [held],
  bills: [{ ...held, id: 3, receipt_id: 4, bill_number: "MF-2100", status: "matched", status_label: "Matches the receipt",
    amount: "2000.00", difference: "0.00", lines: undefined,
    settlement: "unpaid", settlement_label: "Not paid yet", paid: "0.00", waiting: "0.00", outstanding: "2000.00" },
    { ...held, id: 2, receipt_id: 1, bill_number: "MF-2000", status: "matched", status_label: "Matches the receipt",
      amount: "2000.00", difference: "0.00", lines: undefined, can_void: false,
      settlement: "part_paid", settlement_label: "Part paid", paid: "750.00", waiting: "500.00", outstanding: "750.00" }],
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  me = "test_checker";
  canDecide = true;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/supplier-bills/" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return json({ ...held, id: 11, bill_number: body.bill_number, status: body.amount === "2000.00" ? "matched" : "on_hold" }, 201);
    }
    if (url.pathname === "/api/finance/supplier-bills/") return json(overview());
    if (url.pathname.endsWith("/accept/")) return json({ ...held, status: "accepted" });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("a second person sees the difference against what was ordered and received, and accepts it", async () => {
  act(() => root.render(<SupplierBills selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  const row = container.querySelector(".sb-waiting tbody tr")!;
  assert.match(row.textContent ?? "", /MF-2210.*Milano Furniture.*2.00 × TEST-SOFA at 1,000.00 EUR · ordered 2.00 at 1,000.00 EUR/);
  assert.match(row.textContent ?? "", /Bill higher by 50.00 EUR/);
  await act(async () => button("Accept difference").click());
  await flush();
  const accept = calls.find((call) => call.url.pathname === "/api/finance/supplier-bills/7/accept/")!;
  assert.ok(new Headers(accept.init?.headers).get("Idempotency-Key"));
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /accepted and posted/);
});

test("the person who recorded a bill cannot decide it, and matched bills can be voided with a reason", async () => {
  me = "test_maker";
  act(() => root.render(<SupplierBills selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Accept difference"), undefined);
  assert.match(container.querySelector(".sb-waiting")?.textContent ?? "", /You recorded this; someone else decides/);
  const voids = [...container.querySelectorAll(".sb-bills button")].filter((node) => node.textContent === "Void…");
  assert.equal(voids.length, 1);
});

test("recording a bill shows whether it matches before it is saved", async () => {
  act(() => root.render(<SupplierBills selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Record bill").click());
  const form = container.querySelector("form")!;
  assert.match(form.textContent ?? "", /booked at 2,000.00 EUR/);
  const [number, , amount] = [...form.querySelectorAll("input")] as HTMLInputElement[];
  await act(async () => { type(number, "MF-2300"); type(amount, "1990.00"); });
  assert.match(form.querySelector(".sb-check")?.textContent ?? "", /Bill lower by 10.00 EUR.*second person/);
  await act(async () => type(amount, "2000.00"));
  assert.match(form.querySelector(".sb-check")?.textContent ?? "", /Matches/);
  await act(async () => form.requestSubmit());
  await flush();
  const post = calls.find((call) => call.init?.method === "POST")!;
  assert.deepEqual(JSON.parse(String(post.init?.body)), { receipt_id: 9, bill_number: "MF-2300", bill_date: JSON.parse(String(post.init?.body)).bill_date, amount: "2000.00", note: "" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /MF-2300 matches receipt 9/);
});

test("each payable bill shows what is paid and still to pay, and can be paid in part", async () => {
  act(() => root.render(<SupplierBills selectedUnitId={1} csrfToken="csrf" />));
  await flush();
  const rows = [...container.querySelectorAll(".sb-bills tbody tr")];
  assert.match(rows[1].textContent ?? "", /Part paidPaid 750.00Waiting approval 500.00Still to pay 750.00 EUR/);
  assert.equal(rows[1].querySelector("a.sb-pay")?.getAttribute("href"), "#/finance/supplier-payments");
  assert.match(rows[0].textContent ?? "", /Not paid yetPaid 0.00Still to pay 2,000.00 EUR/);
});
