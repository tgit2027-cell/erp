import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Expenses from "../src/Expenses";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let canApprove: boolean;

const row = (id: number, extra = {}) => ({
  id, number: `EXP-TVL-2026-0000${id}`, date: "2026-10-12", category: "utilities", category_label: "Electricity and water",
  description: "TEST ONLY showroom electricity", payee: "TEST ONLY Kahramaa", amount: "350.00",
  paid_from: { id: 1, name: "TEST ONLY Showroom till", kind: "cash" }, claimant: null, has_receipt: true,
  receipt_url: `/api/finance/expenses/${id}/receipt/`, status: "submitted", status_label: "Waiting for approval",
  submitted_by: "demo_admin", decided_by: "", rejection_reason: "", journal_entry_id: null, reimbursed_from: "",
  can_decide: canApprove, ...extra,
});
const overview = () => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" }, month: "2026-10", can_record: true, can_approve: canApprove, can_reimburse: true,
  rows: [
    row(1),
    row(2, { status: "approved", status_label: "Approved", paid_from: null, claimant: { id: 5, name: "Dana Buyer" }, category_label: "Travel",
      description: "TEST ONLY taxi", amount: "120.00", decided_by: "demo_multi", journal_entry_id: 44, can_decide: false }),
  ],
  totals: { booked: "120.00", waiting: 1, waiting_amount: "350.00", owed_to_staff: "120.00", owed_count: 1 },
  by_category: [{ category: "travel", label: "Travel", total: "120.00", count: 1 }],
  categories: [{ value: "utilities", label: "Electricity and water" }, { value: "travel", label: "Travel" }],
  accounts: [{ id: 1, name: "TEST ONLY Showroom till", kind: "cash", opening_date: "2026-01-01" }, { id: 2, name: "TEST ONLY QNB current", kind: "bank", opening_date: "2026-01-01" }],
  people: [{ id: 5, name: "Dana Buyer" }, { id: 6, name: "Omar Sales" }],
  policy_label: "TEST ONLY provisional rules",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const field = (label: string) => [...container.querySelectorAll("label")].find((node) => node.textContent?.startsWith(label))?.querySelector("input, select") as HTMLInputElement | HTMLSelectElement;
function setValue(input: HTMLInputElement | HTMLSelectElement, value: string) {
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value")?.set?.call(input, value);
  input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}
const posts = () => calls.filter((call) => call.init?.method === "POST");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  canApprove = true;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/expenses/" && init?.method === "POST") return json(row(3), 201);
    if (url.pathname === "/api/finance/expenses/") return json(overview());
    if (/^\/api\/finance\/expenses\/\d+\/$/.test(url.pathname)) {
      const body = JSON.parse(String(init?.body));
      if (body.decision === "repay" && body.account_id === 1) return json({ detail: "October 2026 is reconciled and approved for TEST ONLY Showroom till. Use a later date." }, 400);
      return json(row(1, { status: "approved" }));
    }
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the month shows what was booked, what waits for approval and what is owed to staff", async () => {
  act(() => root.render(<Expenses selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const tiles = container.querySelector(".ex-tiles")?.textContent ?? "";
  assert.match(tiles, /Booked in October 2026120\.00/);
  assert.match(tiles, /Waiting for approval1350\.00 QAR/);
  assert.match(tiles, /Owed to staff120\.00QAR across 1 expense/);
  assert.match(container.querySelector(".ex-categories")?.textContent ?? "", /Travel 120\.00/);
  const table = container.querySelector(".ex-table")?.textContent ?? "";
  assert.match(table, /Showroom till.*petty cash/);
  assert.match(table, /Dana Buyerstaff, to be repaid/);
  assert.match(table, /by demo_multi · journal #44/);
  assert.equal((container.querySelector(".ex-table a") as HTMLAnchorElement).getAttribute("href"), "/api/finance/expenses/1/receipt/?unit=7");

  await act(async () => setValue(field("Status"), "submitted"));
  await flush();
  assert.equal(calls.at(-1)!.url.searchParams.get("status"), "submitted");
});

test("an expense is entered with its receipt and sent for approval", async () => {
  act(() => root.render(<Expenses selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Enter an expense")!.click());
  await act(async () => {
    setValue(field("Amount"), "350.00");
    setValue(field("What it was for"), "TEST ONLY showroom electricity");
  });
  const file = new File(["%PDF-1.4"], "receipt.pdf", { type: "application/pdf" });
  const input = field("Receipt") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [file] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await act(async () => (container.querySelector("form[aria-label='Enter an expense']") as HTMLFormElement).requestSubmit());
  await flush();
  const body = posts()[0].init?.body as FormData;
  assert.equal(body.get("paid_by"), "account");
  assert.equal(body.get("account_id"), "1", "Petty cash is the first choice");
  assert.equal(body.get("amount"), "350.00");
  assert.equal((body.get("receipt") as File).name, "receipt.pdf");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /EXP-TVL-2026-00003 entered\. It is booked once a second person approves it/);

  await act(async () => button("Enter an expense")!.click());
  await act(async () => setValue(field("Paid by"), "staff"));
  assert.equal((field("Who paid") as HTMLSelectElement).value, "5");
});

test("approvers approve or reject with a reason, staff are repaid, and errors show", async () => {
  act(() => root.render(<Expenses selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Reject")!.click());
  await act(async () => setValue(container.querySelector("input[aria-label^='Why']") as HTMLInputElement, "TEST ONLY no receipt"));
  await act(async () => (container.querySelector(".ex-inline") as HTMLFormElement).requestSubmit());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { decision: "reject", reason: "TEST ONLY no receipt" });

  await act(async () => button("Approve")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[1].init?.body)), { decision: "approve" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /approved and booked/);

  await act(async () => button("Repay")!.click());
  await act(async () => (container.querySelector(".ex-inline") as HTMLFormElement).requestSubmit());
  await flush();
  assert.equal(JSON.parse(String(posts()[2].init?.body)).account_id, 1);
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /reconciled and approved/);
});

test("people without approval rights see no approve button", async () => {
  canApprove = false;
  act(() => root.render(<Expenses selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(button("Approve"), undefined);
  assert.ok(button("Repay"), "Repaying needs the cash permission, not approval");
});
