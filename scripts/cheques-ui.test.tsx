import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Cheques from "../src/Cheques";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const row = (id: number, extra = {}) => ({
  id, direction: "received", number: `10020${id}`, drawn_on: "TEST ONLY Commercial Bank", account: null, received_on: "2026-10-30",
  party: "TEST ONLY Al Waab Villas", customer_id: 5, order: { id: 41, label: "Order 41" }, supplier_payment_id: null, amount: "4500.00",
  cheque_date: "2026-11-02", days: 0, status: "held", status_label: "Held, not yet due", note: "", reason: "", deposited_on: null,
  finished_on: null, replaces: null, replaced_by: null, journal_entry_id: null, recorded_by: "Admin", updated_by: "", actions: ["deposit", "return"], ...extra,
});
const tile = (count: number, total: string) => ({ count, total });
const overview = () => ({
  unit: { id: 7, name: "Tivoli" }, today: "2026-11-02",
  tiles: { held: tile(1, "4500.00"), due_this_week: tile(1, "4500.00"), deposited: tile(1, "2000.00"), bounced: tile(1, "800.00"), to_cash: tile(1, "2500.00") },
  received: [row(1), row(2, { status: "deposited", status_label: "Deposited, waiting for the bank", amount: "2000.00", actions: ["clear", "bounce"] }),
    row(3, { status: "bounced", status_label: "Bounced", amount: "800.00", reason: "TEST ONLY insufficient funds", actions: ["replace"] })],
  issued: [row(4, { direction: "issued", party: "TEST ONLY Roche Bobois", order: null, customer_id: null, supplier_payment_id: 9, amount: "2500.00",
    cheque_date: "2026-11-23", days: 21, status: "issued", status_label: "Written, not yet cashed", actions: ["clear", "cancel"] })],
  finished: [],
  weeks: [{ start: "2026-11-02", end: "2026-11-08", in: "4500.00", out: "0.00", in_count: 1, out_count: 0 }],
  accounts: [{ id: 2, name: "TEST ONLY QNB current", bank_name: "QNB" }, { id: 3, name: "TEST ONLY Dukhan current", bank_name: "Dukhan" }],
  orders: [{ id: 41, customer_id: 5, customer: "TEST ONLY Al Waab Villas", label: "Order 41", unpaid: "9000.00", held: "6500.00", open: "2500.00" },
    { id: 42, customer_id: 6, customer: "TEST ONLY Pearl Towers", label: "Order 42 · invoice INV-7", unpaid: "1200.00", held: "0.00", open: "1200.00" }],
  supplier_payments: [{ id: 9, label: "Payment 9 to TEST ONLY Roche Bobois", amount: "2500.00", paid_on: "2026-11-02", status_label: "Waiting for approval" }],
  can_manage: true, policy: "TEST ONLY policy",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string, scope: ParentNode = container) => [...scope.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.trim().startsWith(label))?.querySelector("input, select") as HTMLInputElement | HTMLSelectElement;
function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
  input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}
const posts = () => calls.filter((call) => call.init?.method === "POST");
const submit = () => act(async () => (container.querySelector("form[aria-label='Record a cheque']") as HTMLFormElement).requestSubmit());

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/cheques/" && init?.method === "POST") return json(row(9, { number: "555001", amount: "2500.00" }), 201);
    if (url.pathname === "/api/finance/cheques/") return json(overview());
    if (url.pathname === "/api/finance/cheques/2/") return json(row(2, { status: "cleared", status_label: "Cleared", journal_entry_id: 77 }));
    if (url.pathname === "/api/finance/cheques/2/" || /\/api\/finance\/cheques\/\d+\/$/.test(url.pathname)) return json(row(4, { status: "cancelled", status_label: "Cancelled or stopped" }));
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("tiles, the weeks ahead and a customer's cheque for an order", async () => {
  act(() => root.render(<Cheques selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector("[aria-label='Cheques at a glance']")?.textContent ?? "", /Held, not yet due4,500\.00.*Bounced800\.00.*Written, not cashed2,500\.00/);
  assert.match(container.querySelector("[aria-label='Next 8 weeks']")?.textContent ?? "", /4,500\.00 \(1\)/);
  assert.match(container.querySelector("[aria-label='Cheques from customers']")?.textContent ?? "", /100201.*today/);

  await act(async () => setValue(field("Customer"), "5"));
  await act(async () => setValue(field("Order"), "41"));
  assert.match(container.textContent ?? "", /Order 41: QAR 9,000\.00 unpaid, QAR 6,500\.00 already covered by cheques/);
  await act(async () => {
    setValue(field("Drawn on"), "TEST ONLY Commercial Bank");
    setValue(field("Cheque number"), "555001");
    setValue(field("Amount"), "2500");
    setValue(field("Date on the cheque"), "2026-12-01");
    setValue(field("Received on"), "2026-11-01");
  });
  assert.equal(field("Deposit to"), undefined, "The bank account is chosen when the cheque clears");
  await submit();
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { direction: "received", order_id: 41, drawn_on: "TEST ONLY Commercial Bank",
    number: "555001", amount: "2500", cheque_date: "2026-12-01", received_on: "2026-11-01", note: "" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /booked when the bank clears it/);
});

test("clearing, bouncing with a reason, and a replacement", async () => {
  act(() => root.render(<Cheques selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const table = container.querySelector("[aria-label='Cheques from customers']")!;
  assert.match(table.textContent ?? "", /Received 2026-10-30/);
  await act(async () => button("Cleared", table)!.click());
  assert.equal(posts().length, 0, "Asks which bank account first");
  const bank = container.querySelector("select[aria-label='Bank account the money went into']") as HTMLSelectElement;
  assert.equal(button("Confirm cleared")!.disabled, true);
  await act(async () => setValue(bank, "3"));
  await act(async () => button("Confirm cleared")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/finance/cheques/2/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "clear", reason: "", account_id: 3 });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Booked as Al Waab Villas's payment \(journal 77\)/);

  const again = container.querySelector("[aria-label='Cheques from customers']")!;
  await act(async () => button("Bounced", again)!.click());
  assert.equal(posts().length, 1, "Asks for the reason first");
  await act(async () => setValue(container.querySelector("input[aria-label='Reason']") as HTMLInputElement, "TEST ONLY account closed"));
  await act(async () => button("Confirm", container.querySelector("[aria-label='Cheques from customers']")!)!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[1].init?.body)), { action: "bounce", reason: "TEST ONLY account closed" });

  await act(async () => button("Record a replacement")!.click());
  assert.match(container.textContent ?? "", /Replacement for cheque 100203/);
  assert.equal(field("Customer"), undefined, "Same customer and order");
  await act(async () => {
    setValue(field("Drawn on"), "TEST ONLY QIIB");
    setValue(field("Cheque number"), "900001");
    setValue(field("Date on the cheque"), "2026-11-10");
  });
  await submit();
  await flush();
  const sent = JSON.parse(String(posts()[2].init?.body));
  assert.deepEqual([sent.order_id, sent.replaces_id, sent.amount, sent.account_id], [41, 3, "800.00", undefined]);
});

test("a cheque we write to a supplier", async () => {
  act(() => root.render(<Cheques selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => setValue(field("Cheque"), "issued"));
  assert.equal(field("Customer"), undefined);
  await act(async () => {
    setValue(field("Pay to"), "TEST ONLY Roche Bobois");
    setValue(field("For supplier payment"), "9");
    setValue(field("Cheque number"), "000123");
    setValue(field("Amount"), "2500");
    setValue(field("Date on the cheque"), "2026-11-23");
  });
  await submit();
  await flush();
  const sent = JSON.parse(String(posts()[0].init?.body));
  assert.deepEqual([sent.direction, sent.payee, sent.supplier_payment_id, sent.account_id], ["issued", "TEST ONLY Roche Bobois", 9, 2]);
  assert.match(container.querySelector("[aria-label='Cheques we wrote']")?.textContent ?? "", /Supplier payment 9.*in 21 days/);
});
