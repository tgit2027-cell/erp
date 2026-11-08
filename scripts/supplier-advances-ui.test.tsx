import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import SupplierAdvances from "../src/SupplierAdvances";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const use = (id: number, extra = {}) => ({
  id, receipt_id: 9, status: "requested", status_label: "Waiting for approval", currency: "EUR", amount: "600.00", paid_value: "2400.00",
  cleared_value: null, exchange_difference: null, paid_on: "2026-10-30", paid_by: "demo_admin", decided_by: null, decision_note: "",
  can_decide: true, ...extra,
});
const advance = (id: number, extra = {}) => ({
  id, number: `ADV-TVL-2026-0000${id}`, supplier: { id: 3, name: "TEST ONLY Poltrona Frau" }, paid_on: "2026-09-01",
  purpose: "TEST ONLY 30% deposit on PO-118", currency: "EUR", amount: "1000.00", exchange_rate: "4", rate_source: "TEST ONLY QNB",
  value: "4000.00", bank_reference: "TT-9", status: "approved", status_label: "Approved and paid", used: "0.00", waiting: "600.00",
  left: "400.00", prepared_by: "Synthetic Admin", decided_by: "Omar", decision_note: "", journal_entry_id: 4, can_decide: false,
  uses: [use(21)], ...extra,
});
const overview = () => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" },
  advances: [advance(1), advance(2, { status: "requested", status_label: "Waiting for approval", currency: "QAR", amount: "5000.00", value: "5000.00",
    left: "0.00", waiting: "0.00", decided_by: "", can_decide: true, uses: [], supplier: { id: 4, name: "TEST ONLY Doha Upholstery" } })],
  totals: { held_value: "1600.00", open: 1, waiting: 2 },
  open_goods: [{ id: 9, supplier_id: 3, supplier: "TEST ONLY Poltrona Frau", bill_number: "INV-88", currency: "EUR", exchange_rate: "4.1", payable: "3000.00", remaining: "250.00", received_on: "2026-10-20" }],
  suppliers: [{ id: 3, name: "TEST ONLY Poltrona Frau" }, { id: 4, name: "TEST ONLY Doha Upholstery" }], currencies: ["QAR", "EUR", "USD"],
  can_prepare: true, can_approve: true, currency: "QAR", policy: "TEST ONLY policy",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input, select") as HTMLInputElement;
async function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
const posts = () => calls.filter((call) => call.init?.method === "POST");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/advances/" && init?.method === "POST") return json(advance(3, { status: "requested", uses: [] }), 201);
    if (url.pathname === "/api/finance/advances/") return json(overview());
    if (init?.method === "POST") return json({ ok: true });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the list shows what suppliers hold and what is left on each advance", async () => {
  act(() => root.render(<SupplierAdvances selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Held by suppliers1,600\.00.*Advances with money left1.*Waiting for approval2/);
  const table = container.querySelector(".sa-table")?.textContent ?? "";
  assert.match(table, /ADV-TVL-2026-0000130% deposit on PO-118Poltrona Frau2026-09-011,000\.00 EUR400\.00 EUR600\.00 waitingApproved and paidby Omar · for you to approve/);
  await act(async () => button("ADV-TVL-2026-00002")!.click());
  await act(async () => button("Approve and pay")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/finance/advances/2/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "approve" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /ADV-TVL-2026-00002 is approved and paid/);
});

test("a foreign-currency advance needs a rate and its source", async () => {
  act(() => root.render(<SupplierAdvances selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Pay an advance")!.click());
  await setValue(field("Supplier"), "3");
  await setValue(field("Currency"), "EUR");
  await setValue(field("Amount (EUR)"), "1000");
  await setValue(field("QAR per 1 EUR"), "3.98");
  await setValue(field("Where the rate comes from"), "TEST ONLY QNB advice");
  await setValue(field("What it is for"), "TEST ONLY deposit");
  assert.match(container.querySelector(".an-form")?.textContent ?? "", /Leaves the bank as 3,980\.00 QAR/);
  await act(async () => button("Send for approval")!.click());
  await flush();
  const body = JSON.parse(String(posts()[0].init?.body));
  assert.deepEqual([body.supplier_id, body.currency, body.amount, body.exchange_rate, body.rate_source], [3, "EUR", "1000", "3.98", "TEST ONLY QNB advice"]);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Nothing has left the bank yet/);
});

test("an advance is set against goods up to what is left, and its use is approved", async () => {
  act(() => root.render(<SupplierAdvances selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("ADV-TVL-2026-00001")!.click());
  assert.equal(field("Amount (EUR)").value, "250.00", "The smaller of what is left and what is still to pay");
  await setValue(field("Amount (EUR)"), "300");
  assert.equal(button("Set against the goods")!.disabled, true);
  await setValue(field("Amount (EUR)"), "250");
  await act(async () => button("Set against the goods")!.click());
  await flush();
  const [first] = posts();
  assert.deepEqual(JSON.parse(String(first.init?.body)), { action: "use", receipt_id: 9, amount: "250" });
  assert.match(String((first.init?.headers as Record<string, string>)["Idempotency-Key"]), /^advance-use-/);
  await act(async () => button("Approve")!.click());
  await flush();
  assert.equal(posts()[1].url.pathname, "/api/finance/supplier-payments/21/approve/");
});
