import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import CustomerRefunds from "../src/CustomerRefunds";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let decided = false;
let outstanding = "0.00";
let ownReturn = false;

const base = {
  id: 7, quote_id: 41, request_date: "2026-10-20", recorded_by: "Showroom Admin",
  customer: { id: 3, name: "TEST ONLY Al Sadd family", qid: "28463400123" }, location: { code: "STIV-G", name: "Tivoli showroom" },
  proposed_refund_amount: "2000.00", requested_refund_method: "card", original_payment_method: "card",
  lines: [{ quote_line_id: 9, item_id: "SOF-001", description: "TEST ONLY sofa", brand: "TEST Brand", quantity: "1.00", condition: "good", inspection_notes: "No marks" }],
};
const pending = () => ({
  ...base, status: "finance_pending",
  review: { invoice_number: "TVL-INV-2026-00012", returned_value: "2000.00", returned_cost: "1200.00", outstanding_balance: outstanding, problem: "", own_return: ownReturn,
    lines: [{ quote_line_id: 9, quantity: "1.00", net_unit_price: "2000.00", value: "2000.00", cost: "1200.00", condition: "good" }] },
});
const refunded = {
  ...base, status: "refunded",
  credit_note: { number: "TVL-CN-2026-00001", issued_at: "2026-10-27T09:00:00Z", issued_by: "Finance Controller", invoice_number: "TVL-INV-2026-00012",
    credit_total: "1500.00", applied_to_balance: "0.00", refunded_amount: "1500.00", refund_method: "card", returned_value: "2000.00", returned_cost: "1200.00", note: "Scratched leg" },
};
const queue = (status: string) => ({
  status, unit: { name: "Tivoli" }, waiting: decided ? 0 : 1, policy_label: "TEST ONLY",
  returns: status === "pending" ? (decided ? [] : [pending()]) : [refunded],
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const buttons = (label: string) => [...container.querySelectorAll("button")].filter((button) => button.textContent === label) as HTMLButtonElement[];
async function type(element: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
const input = (label: string) => [...container.querySelectorAll("label")].find((item) => item.textContent?.startsWith(label))!.querySelector("input") as HTMLInputElement;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  decided = false;
  outstanding = "0.00";
  ownReturn = false;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/refunds/") return json(queue(url.searchParams.get("status") ?? "pending"));
    if (url.pathname === "/api/finance/refunds/7/approve/") { decided = true; return json(refunded); }
    if (url.pathname === "/api/finance/refunds/7/reject/") { decided = true; return json({ ...base, status: "rejected" }); }
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("Finance sees the inspected return with its value and cost and approves a full refund", async () => {
  act(() => root.render(<CustomerRefunds selectedUnitId={1} csrfToken="test-token" />));
  await flush();
  assert.equal(calls[0].url.searchParams.get("unit"), "1");
  assert.match(container.querySelector(".cr-card h3")?.textContent ?? "", /Al Sadd family.*QID 28463400123/);
  assert.deepEqual([...container.querySelectorAll(".cr-table tbody td.cr-num")].map((cell) => cell.textContent), ["1", "2,000.00", "1,200.00"]);
  assert.match(container.querySelector(".cr-split")?.textContent ?? "", /2,000\.00 is refunded by card\. The invoice is already paid\./);
  await act(async () => buttons("Approve and issue credit note")[0].click());
  await flush();
  const approve = calls.find((item) => item.url.pathname === "/api/finance/refunds/7/approve/")!;
  const headers = approve.init?.headers as Record<string, string>;
  assert.equal(headers["X-CSRFToken"], "test-token");
  assert.ok(headers["Idempotency-Key"]);
  assert.deepEqual(JSON.parse(String(approve.init?.body)), { credit_total: "2000.00", refund_method: "card", note: "" });
  assert.match(container.querySelector(".cr-notice")?.textContent ?? "", /Credit note TVL-CN-2026-00001 issued/);
  assert.match(container.textContent ?? "", /No returns are waiting for Finance/);
});

test("a lower credit needs a note, and the split shows what comes off the balance", async () => {
  outstanding = "500.00";
  act(() => root.render(<CustomerRefunds selectedUnitId={1} csrfToken="test-token" />));
  await flush();
  await type(input("Credit amount"), "1500.00");
  const approve = buttons("Approve and issue credit note")[0];
  assert.equal(approve.disabled, true, "A lower credit needs a note");
  assert.match(container.querySelector(".cr-split")?.textContent ?? "", /500\.00 reduces what the customer still owes on TVL-INV-2026-00012, and 1,000\.00 is refunded/);
  await type(input("Note"), "Scratched leg");
  assert.equal(approve.disabled, false);
  await type(input("Credit amount"), "2500.00");
  assert.equal(approve.disabled, true, "Never more than the goods were sold for");
  assert.match(container.querySelector(".cr-problem")?.textContent ?? "", /cannot be more than/);
});

test("rejecting needs a reason", async () => {
  act(() => root.render(<CustomerRefunds selectedUnitId={1} csrfToken="test-token" />));
  await flush();
  await act(async () => buttons("Reject…")[0].click());
  const submit = buttons("Reject return")[0];
  assert.equal(submit.disabled, true);
  await type(input("Why is the refund rejected?"), "The item was used at home");
  await act(async () => submit.click());
  await flush();
  const reject = calls.find((item) => item.url.pathname === "/api/finance/refunds/7/reject/")!;
  assert.deepEqual(JSON.parse(String(reject.init?.body)), { reason: "The item was used at home" });
  assert.match(container.querySelector(".cr-notice")?.textContent ?? "", /Return 7 rejected/);
});

test("the person who recorded the return cannot decide it, and decided returns show the credit note", async () => {
  ownReturn = true;
  act(() => root.render(<CustomerRefunds selectedUnitId={1} csrfToken="test-token" />));
  await flush();
  assert.equal(buttons("Approve and issue credit note").length, 0);
  assert.match(container.textContent ?? "", /someone else in Finance must decide it/);
  await act(async () => buttons("Decided")[0].click());
  await flush();
  assert.equal(calls.at(-1)!.url.searchParams.get("status"), "decided");
  const facts = container.querySelector(".cr-note-facts")?.textContent ?? "";
  assert.match(facts, /Credit noteTVL-CN-2026-00001/);
  assert.match(facts, /NoteScratched leg/);
});
