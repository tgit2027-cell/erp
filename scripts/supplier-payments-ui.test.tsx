import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import SupplierPayments from "../src/SupplierPayments";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let requests: { url: string; method: string; body: Record<string, unknown> | undefined; headers: Record<string, string> }[];
let state: "open" | "requested" | "approved";
let viewer: { me: string; can_prepare: boolean; can_approve: boolean };
let goodsBill: { id: number | null; number: string; status: string; label: string; date: string | null; payable: boolean };

const supplier = { id: 70, code: "TEST-SUP-IMPORT-01", name: "TEST ONLY Milan furniture supplier" };
const item = (charge_id: number | null, paying_for: string, label: string, currency: string, rate: string, payable: string, waiting = "0.00") => ({
  charge_id, paying_for, label, currency, exchange_rate: rate, payable, paid: "0.00", waiting,
  remaining: (Number(payable) - Number(waiting)).toFixed(2), carried_value: (Number(payable) * Number(rate)).toFixed(2),
  bill: charge_id === null ? goodsBill : null,
});
const receipt = (waiting = "0.00") => ({
  id: 12, supplier, received_on: "2026-10-01", currency: "EUR", rate_date: "2026-10-01",
  items: [
    item(null, "goods", "Goods from TEST ONLY Milan furniture supplier", "EUR", "4.00000000", "2000.00", waiting),
    item(31, "clearance", "Clearance", "QAR", "1.00000000", "1800.00"),
  ],
});
const payment = (status: string, paid_by = "test_finance") => ({
  id: 1, receipt_id: 12, status, status_label: status === "approved" ? "Approved and paid" : "Waiting for approval",
  supplier, paying_for: "goods", paying_for_label: "Goods", charge_id: null, payee_name: supplier.name,
  currency: "EUR", amount: "2000.00", exchange_rate: "3.90000000", rate_source: "TEST ONLY bank advice", rate_date: "2026-10-22",
  paid_value: "7800.00", cleared_value: status === "approved" ? "8000.00" : null,
  exchange_difference: status === "approved" ? "200.00" : null, paid_on: "2026-10-22", bank_reference: "TT-1",
  paid_by, decided_by: status === "approved" ? "test_checker" : null, decision_note: "", journal_entry_id: status === "approved" ? 44 : null,
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}

async function flush() {
  await act(async () => { await new Promise(setImmediate); });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  requests = [];
  state = "open";
  goodsBill = { id: 3, number: "MF-2100", status: "matched", label: "Matches the receipt", date: "2026-10-01", payable: true };
  viewer = { me: "test_finance", can_prepare: true, can_approve: false };
  globalThis.fetch = (async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const url = String(input);
    const method = options.method ?? "GET";
    requests.push({ url, method, body: typeof options.body === "string" ? JSON.parse(options.body) : undefined, headers: (options.headers ?? {}) as Record<string, string> });
    if (method === "POST") {
      const body = JSON.parse(String(options.body));
      if (url.includes("/approve/")) { state = "approved"; return json(payment("approved")); }
      if (url.includes("/reject/")) return json({ ...payment("rejected"), decision_note: body.note });
      if (body.receipt_id === 12 && body.charge_id === null && !body.rate_source) return json({ detail: "Say where the exchange rate comes from (at most 120 characters)." }, 400);
      state = "requested";
      return json(payment("requested"), 201);
    }
    return json({
      unit: { id: 1, code: "TVL", name: "Tivoli" }, ...viewer,
      open_receipts: state === "approved" ? [] : [receipt(state === "requested" ? "2000.00" : "0.00")],
      waiting: state === "requested" ? [payment("requested")] : [],
      payments: state === "approved" ? [payment("approved")] : [],
      totals: state === "approved" ? { exchange_gain: "200.00", exchange_loss: "0.00", net: "200.00" } : { exchange_gain: "0.00", exchange_loss: "0.00", net: "0.00" },
    });
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

async function mount() {
  root = createRoot(container);
  act(() => root.render(<SupplierPayments selectedUnitId={1} csrfToken="TEST-ONLY-CSRF" />));
  await flush();
}

const buttons = (text: string) => [...container.querySelectorAll("button")].filter((item) => item.textContent?.includes(text)) as HTMLButtonElement[];
async function click(element: Element | undefined) {
  assert.ok(element);
  await act(async () => { (element as HTMLElement).click(); });
  await flush();
}
async function type(label: string, value: string) {
  const input = [...container.querySelectorAll("label")].find((item) => item.textContent?.startsWith(label))?.querySelector("input");
  assert.ok(input, label);
  await act(async () => {
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () => { container.querySelector("form")!.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })); });
  await flush();
}

test("preparing a EUR goods payment previews the exchange result and sends it for approval", async () => {
  await mount();
  assert.equal(requests[0].url, "/api/finance/supplier-payments/?unit=1");
  assert.equal(container.querySelectorAll(".sp-table tbody tr").length, 2, "Goods and the clearance charge");
  await click(buttons("Prepare payment")[0]);
  assert.match(container.textContent ?? "", /Still to pay: 2000.00 EUR/);
  assert.equal((container.querySelector("label input[readonly]") as HTMLInputElement).value, supplier.name);
  await type("Bank rate on payment day", "3.9");
  assert.match(container.querySelector(".sp-preview")?.textContent ?? "", /From bank7,800.00 QAR.*receipt rate8,000.00 QAR.*Gain 200.00/);
  await submit();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /Say where the exchange rate comes from/);
  await type("Rate source", "TEST ONLY bank advice");
  await submit();
  const posts = requests.filter((request) => request.method === "POST");
  assert.equal(posts.length, 2);
  assert.equal(posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"], "A retry of the same form reuses its key");
  assert.equal(posts[1].headers["X-CSRFToken"], "TEST-ONLY-CSRF");
  assert.deepEqual(
    { ...posts[1].body, paid_on: undefined, rate_date: undefined },
    { receipt_id: 12, charge_id: null, amount: "2000.00", exchange_rate: "3.9", rate_source: "TEST ONLY bank advice", bank_reference: "", paid_on: undefined, rate_date: undefined },
  );
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /sent for approval/);
  assert.match(container.querySelector(".sp-waiting")?.textContent ?? "", /Waiting for an approver/);
});

test("goods without a matching bill send Finance to record it, while charges can still be paid", async () => {
  goodsBill = { id: null, number: "", status: "missing", label: "No bill recorded yet", date: null, payable: false };
  await mount();
  assert.match(container.querySelector(".sp-table tbody tr")?.textContent ?? "", /No bill recorded yet/);
  const link = container.querySelector('a[href="#/finance/supplier-bills"]');
  assert.equal(link?.textContent, "Record bill");
  assert.equal(buttons("Prepare payment").length, 1, "Only the clearance charge");
});

test("a charge is paid to a named party and sends no exchange rate in QAR", async () => {
  await mount();
  await click(buttons("Prepare payment")[1]);
  assert.match(container.textContent ?? "", /Pay the clearance/);
  assert.equal([...container.querySelectorAll("label")].some((label) => label.textContent?.startsWith("Bank rate")), false);
  await type("Paid to", "TEST ONLY Customs broker");
  await submit();
  const body = requests.find((request) => request.method === "POST")?.body ?? {};
  assert.equal(body.charge_id, 31);
  assert.equal(body.payee_name, "TEST ONLY Customs broker");
  assert.equal("exchange_rate" in body, false);
});

test("a second person approves; the person who prepared it cannot", async () => {
  state = "requested";
  viewer = { me: "test_finance", can_prepare: true, can_approve: true };
  await mount();
  assert.match(container.querySelector(".sp-waiting")?.textContent ?? "", /You prepared this; someone else approves/);
  assert.equal(buttons("Approve and pay").length, 0);
  act(() => root.unmount());
  viewer = { me: "test_checker", can_prepare: false, can_approve: true };
  await mount();
  assert.equal(buttons("Prepare payment").length, 0, "A checker without prepare rights sees no prepare buttons");
  await click(buttons("Reject…")[0]);
  assert.equal(buttons("Reject")[0].disabled, true, "A reason is needed to reject");
  await click(buttons("Back")[0]);
  await click(buttons("Approve and pay")[0]);
  const approve = requests.find((request) => request.url.includes("/approve/"))!;
  assert.equal(approve.url, "/api/finance/supplier-payments/1/approve/?unit=1");
  assert.ok(approve.headers["Idempotency-Key"]);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /approved and posted/);
  assert.match(container.textContent ?? "", /journal #44/);
  assert.match(container.querySelector(".sp-totals")?.textContent ?? "", /Realized exchange gain200.00 QAR/);
});
