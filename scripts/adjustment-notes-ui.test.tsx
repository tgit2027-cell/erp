import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import AdjustmentNotes from "../src/AdjustmentNotes";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const note = (id: number, extra = {}) => ({
  id, kind: "customer_credit", kind_label: "Credit note to a customer", side: "customer", lowers: true, effect: "Lowers what the customer owes",
  number: "", their_reference: "", date: "2026-10-31", counterparty: "TEST ONLY Al Thani", document: "TVL-INV-2026-00004",
  invoice_id: 4, receipt_id: null, reason: "price", reason_label: "Price agreed after the sale or purchase",
  description: "TEST ONLY 5% for late delivery", currency: "QAR", amount: "450.00", value: "450.00", status: "submitted",
  status_label: "Waiting for approval", prepared_by: "Synthetic Admin", prepared_at: "2026-10-31T09:00:00Z", decided_by: "",
  decided_at: "", decision_note: "", journal_entry_id: null, can_decide: true, ...extra,
});
const supplierNote = note(7, {
  kind: "supplier_debit", kind_label: "Debit note from a supplier", side: "supplier", lowers: false, effect: "Raises what we owe",
  their_reference: "DN-12", counterparty: "TEST ONLY Poltrona Frau", document: "Receipt 3 · bill INV-88", invoice_id: null, receipt_id: 3,
  reason: "extra", reason_label: "Extra charge agreed", currency: "EUR", amount: "120.00", value: "480.00", can_decide: false,
});
const overview = (side: string) => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" }, side,
  notes: side === "customer" ? [note(1), note(2, { number: "TVL-CN-2026-00003", status: "approved", status_label: "Approved and posted", decided_by: "Omar", can_decide: false })] : [supplierNote],
  waiting: { customer: 1, supplier: 0, for_me: 1 },
  invoices: [{ id: 4, number: "TVL-INV-2026-00004", customer: "TEST ONLY Al Thani", date: "2026-10-02", total: "9000.00", owed: "1500.00", waiting: "450.00", room: "1050.00" }],
  receipts: side === "supplier" ? [{ id: 3, supplier: "TEST ONLY Poltrona Frau", date: "2026-09-12", bill_number: "INV-88", currency: "EUR", rate: "4.0", payable: "5000.00", owed: "5000.00", waiting: "0.00", room: "5000.00" }] : [],
  kinds: [], reasons: [{ value: "price", label: "Price agreed after the sale or purchase" }, { value: "damaged", label: "Damaged or faulty goods kept" }],
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
    if (url.pathname === "/api/finance/notes/" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return json(body.kind === "customer_credit" ? note(5) : { ...supplierNote, id: 8, kind: body.kind }, 201);
    }
    if (url.pathname === "/api/finance/notes/") return json(overview(url.searchParams.get("side") ?? "customer"));
    if (url.pathname === "/api/finance/notes/1/" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return json(body.action === "approve"
        ? note(1, { number: "TVL-CN-2026-00004", status: "approved", status_label: "Approved and posted", decided_by: "Omar", can_decide: false })
        : note(1, { status: "rejected", status_label: "Rejected", decided_by: "Omar", decision_note: body.reason, can_decide: false }));
    }
    if (url.pathname === "/api/finance/notes/1/") return json({ ...note(1), document_now: { total: "9000.00", owed: "1500.00", currency: "QAR" } });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("customer credit notes list what waits for approval and an approver posts one", async () => {
  act(() => root.render(<AdjustmentNotes selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".an-tabs")?.textContent ?? "", /Customers1Suppliers/);
  const table = container.querySelector(".an-table")?.textContent ?? "";
  assert.match(table, /Note 1Price agreed after the sale or purchaseAl ThaniTVL-INV-2026-000042026-10-31−450\.00 QARWaiting for approvalprepared by Synthetic Admin · for you to approve/);
  assert.match(table, /TVL-CN-2026-00003.*Approved and postedby Omar/);
  await act(async () => button("Note 1")!.click());
  await flush();
  assert.match(container.querySelector(".an-detail")?.textContent ?? "", /Still owed now1,500\.00 QAR of 9,000\.00/);
  await act(async () => button("Approve and post")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { action: "approve" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /TVL-CN-2026-00004 is approved and posted/);
});

test("a credit note cannot be more than what can still be credited on the invoice", async () => {
  act(() => root.render(<AdjustmentNotes selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Prepare a credit note")!.click());
  await setValue(field("Invoice"), "4");
  assert.match(container.querySelector(".an-form")?.textContent ?? "", /still owed 1,500\.00, 450\.00 already in notes waiting for approval/);
  await setValue(field("Amount"), "1200");
  assert.match(container.querySelector(".an-form [role=alert]")?.textContent ?? "", /more than the 1,050\.00 QAR that can be credited/);
  assert.equal(button("Send for approval")!.disabled, true);
  await setValue(field("Amount"), "300");
  await setValue(field("What happened"), "TEST ONLY goodwill");
  await act(async () => button("Send for approval")!.click());
  await flush();
  const body = JSON.parse(String(posts()[0].init?.body));
  assert.deepEqual([body.kind, body.invoice_id, body.amount, body.reason], ["customer_credit", 4, "300", "price"]);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /waiting for someone else to approve it/);
});

test("the supplier tab records a supplier's note in the receipt's currency", async () => {
  act(() => root.render(<AdjustmentNotes selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Suppliers")!.click());
  await flush();
  assert.equal(calls.at(-1)?.url.searchParams.get("side"), "supplier");
  assert.match(container.querySelector(".an-table")?.textContent ?? "", /DN-12Debit note from a supplier · Extra charge agreed.*\+120\.00 EUR/);
  await act(async () => button("Record a supplier note")!.click());
  await setValue(field("Goods received"), "3");
  assert.match(container.querySelector(".an-form")?.textContent ?? "", /Counted in QAR at the receipt rate of 4\.0/);
  assert.ok(field("Amount (EUR)"));
  await act(async () => (container.querySelector('input[value="our_debit"]') as HTMLInputElement).click());
  assert.equal(field("Supplier's note number"), undefined, "We number our own debit notes");
  await setValue(field("Amount"), "120");
  await setValue(field("What happened"), "TEST ONLY two chairs scratched");
  await act(async () => button("Send for approval")!.click());
  await flush();
  const body = JSON.parse(String(posts()[0].init?.body));
  assert.deepEqual([body.kind, body.receipt_id, body.amount], ["our_debit", 3, "120"]);
});
