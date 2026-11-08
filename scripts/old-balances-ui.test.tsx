import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import OldBalances from "../src/OldBalances";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const balance = (id: number, extra = {}) => ({
  id, party: { id: 3, name: "TEST ONLY Ahmed Al-Kuwari", number: "C-004512" }, reference: `INV-08${id}`, date: "2026-09-20", age_days: 45,
  currency: "QAR", amount: "18500.00", value: "18500.00", owed: "18500.00", owed_in_currency: "18500.00", waiting: "0.00", can_pay_more: true, ...extra,
});
const settlement = (id: number, extra = {}) => ({
  id, balance_id: 1, reference: "INV-081", party: "TEST ONLY Ahmed Al-Kuwari", status: "requested", status_label: "Waiting for approval",
  currency: "QAR", amount: "8500.00", exchange_rate: "1", paid_value: "8500.00", cleared_value: null, paid_on: "2026-11-03",
  cash_account: "TEST ONLY QNB current", bank_reference: "TT 4471", prepared_by: "Sales", decided_by: "", decision_note: "", can_decide: true, ...extra,
});
const overview = (side: string) => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" }, side,
  balances: side === "customer" ? [balance(1, { waiting: "8500.00" }), balance(2, { amount: "1500.00", owed: "1500.00", owed_in_currency: "1500.00" })]
    : [balance(5, { party: { id: 9, name: "TEST ONLY Gulf Timber", number: "TEST-SUP-0042" }, reference: "GT-55120", currency: "EUR", amount: "12000.00", value: "48600.00", owed: "48600.00", owed_in_currency: "12000.00" })],
  total_owed: side === "customer" ? "20000.00" : "48600.00",
  waiting: side === "customer" ? [settlement(31)] : [], recent: [],
  accounts: [{ id: 4, name: "TEST ONLY QNB current", kind: "bank" }, { id: 5, name: "TEST ONLY Cash box", kind: "cash" }],
  can_prepare: true, can_approve: true, policy: "TEST ONLY policy",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const buttons = (text: string) => [...container.querySelectorAll("button")].filter((node) => node.textContent?.startsWith(text)) as HTMLButtonElement[];
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
    if (url.pathname === "/api/finance/old-balances/" && init?.method === "POST") return json(settlement(40), 201);
    if (url.pathname === "/api/finance/old-balances/") return json(overview(url.searchParams.get("side") ?? "customer"));
    if (init?.method === "POST") return json(settlement(31, { status: "approved" }));
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("old invoices are listed with what is left, and money received goes for approval", async () => {
  act(() => root.render(<OldBalances selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Still owed20,000\.00QAR on 2 invoices.*Waiting for approval1/);
  assert.match(container.querySelector("table[aria-label='Unpaid old invoices']")?.textContent ?? "", /INV-081Ahmed Al-KuwariC-004512.*18,500\.00 QAR8,500\.00 waiting/);
  await act(async () => buttons("Record money received")[0].click());
  assert.equal(field("Amount (QAR)").value, "10000.00", "Only what is not already waiting");
  await setValue(field("Amount (QAR)"), "10000.01");
  assert.equal(buttons("Send for approval")[0].disabled, true);
  await setValue(field("Amount (QAR)"), "4000");
  await setValue(field("Into"), "5");
  await act(async () => buttons("Send for approval")[0].click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { balance_id: 1, amount: "4000", paid_on: new Date().toLocaleDateString("en-CA"), cash_account_id: 5, bank_reference: "" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Nothing is booked yet/);
});

test("a waiting receipt is approved, and a rejection needs a reason", async () => {
  act(() => root.render(<OldBalances selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => buttons("Reject")[0].click());
  assert.equal(buttons("Reject it")[0].disabled, true);
  await act(async () => buttons("Approve and book")[0].click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/finance/old-balances/31/");
  assert.equal(JSON.parse(String(posts()[0].init?.body)).action, "approve");
});

test("supplier bills in a foreign currency ask for the rate", async () => {
  act(() => root.render(<OldBalances selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await setValue(field("Show"), "supplier");
  await flush();
  assert.match(container.querySelector("table[aria-label='Unpaid old bills']")?.textContent ?? "", /GT-55120Gulf Timber0042.*12,000\.00 EUR48,600\.00 QAR/);
  await act(async () => buttons("Record a payment")[0].click());
  await setValue(field("QAR per 1 EUR"), "4.1");
  await setValue(field("Where the rate comes from"), "TEST ONLY QNB advice");
  assert.match(container.querySelector(".an-form")?.textContent ?? "", /Leaves the bank as 49,200\.00 QAR/);
  await act(async () => buttons("Send for approval")[0].click());
  await flush();
  const body = JSON.parse(String(posts()[0].init?.body));
  assert.deepEqual([body.balance_id, body.amount, body.exchange_rate, body.cash_account_id], [5, "12000.00", "4.1", 4]);
});
