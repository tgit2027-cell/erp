import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import GeneralLedger from "../src/GeneralLedger";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let canClose = true;
let septemberClosed = false;
let bankOpen = false;
const checklist = () => [
  { key: "bank_rec", label: "Bank and cash reconciled", done: !bankOpen, detail: bankOpen ? "Not approved yet: TEST ONLY QNB current." : "All 2 accounts reconciled and approved.", required: true },
  { key: "approvals", label: "Nothing waiting for approval", done: true, detail: "Nothing dated in September 2026 is waiting for approval.", required: true },
  { key: "stock_counts", label: "Stock counts finished", done: false, detail: "1 stock count not finished.", required: false },
];

const trialBalance = {
  unit: { id: 1, code: "TVL", name: "Tivoli" }, legal_entity: { id: 1, code: "tivoli", name: "Tivoli Group" },
  scope: "unit", scope_label: "Tivoli (Tivoli Group)", can_view_company: false, start: "2026-10-01", end: "2026-10-27",
  rows: [], totals: { opening_debit: "0.00", opening_credit: "0.00", debit: "0.00", credit: "0.00", closing_debit: "0.00", closing_credit: "0.00" },
  checks: { period_balanced: true, closing_balanced: true, unbalanced_entries: 0 }, entry_count: 0, kinds: [], policy_label: "TEST ONLY",
};
const section = (key: string, label: string, rows: [number, string, string, string, string?][], total: Record<string, string>) => ({
  key, label, total,
  rows: rows.map(([id, code, name, first, second]) => ({ account_id: id, code, name, ...(second === undefined ? { balance: first } : { period: first, year_to_date: second }) })),
});
const incomeStatement = {
  scope_label: "Tivoli (Tivoli Group)", start: "2026-10-01", end: "2026-10-27", year_start: "2026-01-01",
  sections: [
    section("sales", "Sales", [[1, "TEST_REVENUE", "TEST ONLY — Delivery revenue", "4000.00", "9000.00"]], { period: "4000.00", year_to_date: "9000.00" }),
    section("cost_of_sales", "Cost of sales", [[2, "TEST_COGS", "TEST ONLY — Cost of goods sold", "2400.00", "5400.00"]], { period: "2400.00", year_to_date: "5400.00" }),
    section("other_income", "Other income", [], { period: "0.00", year_to_date: "0.00" }),
    section("other_expenses", "Other expenses", [[3, "TEST_FX_LOSS", "TEST ONLY — Realized exchange loss", "1630.00", "30.00"]], { period: "1630.00", year_to_date: "30.00" }),
  ],
  gross_profit: { period: "1600.00", year_to_date: "3600.00" }, gross_margin_percent: { period: "40.0", year_to_date: "40.0" },
  net_profit: { period: "-30.00", year_to_date: "3570.00" }, policy_label: "TEST ONLY",
};
const balanceSheet = {
  scope_label: "Tivoli (Tivoli Group)", as_of: "2026-10-27",
  sections: [
    section("assets", "Assets", [[4, "TEST_INVENTORY", "TEST ONLY — Inventory asset", "9600.00"]], { balance: "9600.00" }),
    section("liabilities", "Liabilities", [[5, "TEST_GRNI", "TEST ONLY — Receipt clearing", "8000.00"]], { balance: "8000.00" }),
    { key: "equity", label: "Equity", rows: [{ code: "", name: "Profit kept from years before 2026", balance: "0.00" }, { code: "", name: "Profit for 2026 to date", balance: "1600.00" }], total: { balance: "1600.00" } },
  ],
  total_assets: "9600.00", total_liabilities_and_equity: "9600.00", balanced: true, difference: "0.00", policy_label: "TEST ONLY",
};
const months = () => ({
  scope_label: "Tivoli (Tivoli Group)", can_close: canClose,
  months: [
    { month: "2026-10", label: "October 2026", status: "current", entry_count: 3, debit: "500.00", credit: "500.00", closed_by: "", closed_at: "", can_close: false, close_blocked: "This month has not ended yet.", warnings: [], can_reopen: false },
    septemberClosed
      ? { month: "2026-09", label: "September 2026", status: "closed", entry_count: 4, debit: "800.00", credit: "800.00", closed_by: "Finance Controller", closed_at: "2026-10-27T08:00:00Z", can_close: false, close_blocked: "", warnings: [], can_reopen: canClose }
      : { month: "2026-09", label: "September 2026", status: "open", entry_count: 4, debit: "800.00", credit: "800.00", closed_by: "", closed_at: "", can_close: canClose, close_blocked: "", warnings: ["1 stock count is not finished. Their write-offs and adjustments will be posted in a later month."], can_reopen: false, checklist: checklist() },
  ],
  history: septemberClosed ? [{ month: "September 2026", action: "close", action_label: "Closed", actor: "Finance Controller", at: "2026-10-27T08:00:00Z", reason: bankOpen ? "TEST ONLY statement arrives next week" : "", entry_count: 4, total_debit: "800.00", checks_not_done: bankOpen ? ["Bank and cash reconciled"] : [] }] : [],
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const buttons = (label: string) => [...container.querySelectorAll("button")].filter((button) => button.textContent === label) as HTMLButtonElement[];
async function open(tab: string) {
  act(() => root.render(<GeneralLedger selectedUnitId={1} csrfToken="test-token" />));
  await flush();
  await act(async () => buttons(tab)[0].click());
  await flush();
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  canClose = true;
  septemberClosed = false;
  bankOpen = false;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/ledger/trial-balance/") return json(trialBalance);
    if (url.pathname === "/api/finance/ledger/income-statement/") return json(incomeStatement);
    if (url.pathname === "/api/finance/ledger/balance-sheet/") return json(balanceSheet);
    if (url.pathname === "/api/finance/ledger/months/") return json(months());
    if (url.pathname === "/api/finance/ledger/months/close/") { septemberClosed = true; return json(months()); }
    if (url.pathname === "/api/finance/ledger/months/reopen/") { septemberClosed = false; return json(months()); }
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the profit and loss shows sales, cost of sales, gross and net profit for the period and the year", async () => {
  await open("Profit and loss");
  const call = calls.find((item) => item.url.pathname === "/api/finance/ledger/income-statement/")!;
  assert.equal(call.url.searchParams.get("unit"), "1");
  assert.match(call.url.searchParams.get("start") ?? "", /^\d{4}-\d{2}-01$/);
  assert.deepEqual([...container.querySelectorAll(".fs-table .gl-class th")].map((cell) => cell.textContent), ["Sales", "Cost of sales", "Other income", "Other expenses"]);
  const gross = [...container.querySelectorAll(".fs-result td")].map((cell) => cell.textContent);
  assert.deepEqual(gross, ["1,600.00 · 40.0% margin", "3,600.00 · 40.0% margin"]);
  assert.deepEqual([...container.querySelectorAll(".fs-table tfoot td")].map((cell) => cell.textContent), ["(30.00)", "3,570.00"], "A loss shows in brackets");
  assert.ok(container.textContent?.includes("Nothing posted"), "An empty section says so");
  const excel = new URL((container.querySelector(".fs-statement .gl-excel") as HTMLAnchorElement).getAttribute("href")!, "http://localhost");
  assert.equal(excel.pathname, "/api/finance/ledger/export/income-statement/");
  await act(async () => buttons("TEST ONLY — Delivery revenue")[0].click());
  await flush();
  assert.ok(calls.some((item) => item.url.pathname === "/api/finance/ledger/accounts/1/"), "An account opens its ledger");
  await act(async () => buttons("← Back")[0].click());
  await flush();
  assert.ok(container.querySelector("#fs-pnl-title"), "Back returns to the profit and loss");
});

test("the balance sheet uses one as-of date and shows the balance check", async () => {
  await open("Balance sheet");
  const labels = [...container.querySelectorAll(".gl-toolbar label")].map((label) => label.firstChild?.textContent);
  assert.deepEqual(labels, ["As of"], "Only the as-of date is shown");
  const call = calls.find((item) => item.url.pathname === "/api/finance/ledger/balance-sheet/")!;
  assert.match(call.url.searchParams.get("as_of") ?? "", /^\d{4}-\d{2}-\d{2}$/);
  assert.match(container.querySelector(".fs-statement .gl-checks")?.textContent ?? "", /Assets equal liabilities plus equity/);
  assert.deepEqual([...container.querySelectorAll(".fs-subtotal td")].map((cell) => cell.textContent), ["9,600.00", "8,000.00", "1,600.00"]);
  assert.equal(container.querySelector(".fs-table tfoot td")?.textContent, "9,600.00");
  assert.ok(container.textContent?.includes("Profit for 2026 to date"));
});

test("a finance manager closes an ended month and must give a reason to reopen it", async () => {
  await open("Month-end close");
  assert.equal(container.querySelectorAll(".gl-toolbar label").length, 0, "Month close has no date filters");
  assert.ok(container.textContent?.includes("This month has not ended yet."));
  assert.match(container.querySelector("[aria-label='Before closing September 2026']")?.textContent ?? "", /Bank and cash reconciled.*Stock counts finished \(good to do\)/);
  assert.match(container.querySelector(".fs-checklist strong")?.textContent ?? "", /2 of 3 done/);
  await act(async () => buttons("Close September 2026")[0].click());
  await flush();
  const close = calls.find((item) => item.url.pathname === "/api/finance/ledger/months/close/")!;
  assert.equal(close.init?.method, "POST");
  assert.equal((close.init?.headers as Record<string, string>)["X-CSRFToken"], "test-token");
  assert.deepEqual(JSON.parse(String(close.init?.body)), { month: "2026-09" });
  assert.match(container.querySelector(".fs-notice")?.textContent ?? "", /September 2026 is closed/);
  assert.match(container.querySelector(".fs-history")?.textContent ?? "", /Closed September 2026/);
  await act(async () => buttons("Reopen")[0].click());
  const input = container.querySelector(".fs-reopen input") as HTMLInputElement;
  const submit = buttons("Reopen month")[0];
  assert.equal(submit.disabled, true, "A reason is needed");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, "Late supplier invoice for September");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  assert.equal(submit.disabled, false);
  await act(async () => submit.click());
  await flush();
  const reopen = calls.find((item) => item.url.pathname === "/api/finance/ledger/months/reopen/")!;
  assert.deepEqual(JSON.parse(String(reopen.init?.body)), { month: "2026-09", reason: "Late supplier invoice for September" });
});

test("closing with a required check not done needs a reason", async () => {
  bankOpen = true;
  await open("Month-end close");
  assert.match(container.querySelector(".fs-checklist .is-open")?.textContent ?? "", /Not approved yet: TEST ONLY QNB current/);
  await act(async () => buttons("Close September 2026")[0].click());
  assert.equal(calls.filter((item) => item.url.pathname === "/api/finance/ledger/months/close/").length, 0, "Asks for the reason first");
  assert.match(container.querySelector(".fs-close-anyway")?.textContent ?? "", /Not done yet: bank and cash reconciled/);
  const input = container.querySelector(".fs-close-anyway input") as HTMLInputElement;
  const submit = buttons("Close anyway")[0];
  assert.equal(submit.disabled, true);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "TEST ONLY statement arrives next week");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => submit.click());
  await flush();
  const close = calls.find((item) => item.url.pathname === "/api/finance/ledger/months/close/")!;
  assert.deepEqual(JSON.parse(String(close.init?.body)), { month: "2026-09", reason: "TEST ONLY statement arrives next week" });
  assert.match(container.querySelector(".fs-history")?.textContent ?? "", /Closed before finishing: Bank and cash reconciled.*Reason: TEST ONLY statement arrives next week/);
});

test("without the close permission the months are shown but cannot be closed", async () => {
  canClose = false;
  await open("Month-end close");
  assert.equal(buttons("Close September 2026").length, 0);
  assert.ok(container.textContent?.includes("closing needs the “Close months” permission"));
});
