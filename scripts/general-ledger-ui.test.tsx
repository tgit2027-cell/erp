import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import GeneralLedger from "../src/GeneralLedger";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let urls: URL[];

const amounts = (debit: string, credit: string, closingDebit: string, closingCredit: string) => ({
  opening_debit: "0.00", opening_credit: "0.00", debit, credit, closing_debit: closingDebit, closing_credit: closingCredit,
});
const owner = (date: string, posted = date, unit = "TVL") => ({ document_date: date, posted_on: posted, legal_entity_code: "tivoli", unit_code: unit });
let canViewCompany = false;
const trialBalance = (scope: string) => ({
  unit: { id: 1, code: "TVL", name: "Tivoli" }, legal_entity: { id: 1, code: "tivoli", name: "Tivoli Group" },
  scope, scope_label: scope === "company" ? "Tivoli Group" : "Tivoli (Tivoli Group)", can_view_company: canViewCompany, start: "2026-10-01", end: "2026-10-24",
  rows: [
    { account_id: 5, code: "TEST_INVENTORY", name: "TEST ONLY — Inventory asset", account_class: "asset", class_label: "Asset", ...amounts("12000.00", "2400.00", "9600.00", "0.00"), closing_balance: "9600.00" },
    { account_id: 6, code: "TEST_GRNI", name: "TEST ONLY — Receipt clearing", account_class: "liability", class_label: "Liability", ...amounts("0.00", "10000.00", "0.00", "10000.00"), closing_balance: "10000.00" },
    { account_id: 7, code: "TEST_COGS", name: "TEST ONLY — Cost of goods sold", account_class: "expense", class_label: "Expense", ...amounts("2400.00", "0.00", "2400.00", "0.00"), closing_balance: "2400.00" },
  ],
  totals: { ...amounts("14400.00", "12400.00", "12000.00", "10000.00") },
  checks: { period_balanced: true, closing_balanced: true, unbalanced_entries: 0 },
  entry_count: 3, kinds: [{ kind: "receipt", label: "Receipt" }, { kind: "delivery", label: "Delivery" }], policy_label: "TEST ONLY synthetic ledger",
});
const ledger = {
  account: { id: 5, code: "TEST_INVENTORY", name: "TEST ONLY — Inventory asset", class_label: "Asset", normal_side: "debit" },
  start: "2026-10-01", end: "2026-10-24", opening_balance: "0.00", closing_balance: "9600.00", debit: "12000.00", credit: "2400.00",
  lines: [
    { id: 1, entry_id: 41, ...owner("2026-09-30", "2026-10-02"), kind: "receipt", description: "TEST ONLY receipt", memo: "Goods in", debit: "12000.00", credit: "0.00", balance: "12000.00" },
    { id: 2, entry_id: 43, ...owner("2026-10-09"), kind: "delivery", description: "TEST ONLY delivery", memo: "Goods out", debit: "0.00", credit: "2400.00", balance: "9600.00" },
  ],
  pagination: { page: 1, page_size: 100, total_count: 2 },
};
const entry = {
  id: 43, ...owner("2026-10-09"), kind_label: "Delivery", description: "TEST ONLY delivery", policy_label: "TEST ONLY",
  lines: [
    { account_id: 7, code: "TEST_COGS", name: "TEST ONLY — Cost of goods sold", debit: "2400.00", credit: "0.00", memo: "Cost" },
    { account_id: 5, code: "TEST_INVENTORY", name: "TEST ONLY — Inventory asset", debit: "0.00", credit: "2400.00", memo: "Goods out" },
  ],
  debit: "2400.00", credit: "2400.00", balanced: true,
};
const entries = {
  entries: [{ id: 43, ...owner("2026-10-09", "2026-10-09", "RBB"), kind: "delivery", kind_label: "Delivery", description: "TEST ONLY delivery", total: "2400.00", line_count: 2 }],
  pagination: { page: 1, page_size: 50, total_count: 1 },
};

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const buttons = (label: string) => [...container.querySelectorAll("button")].filter((button) => button.textContent === label) as HTMLButtonElement[];

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  urls = [];
  canViewCompany = false;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    urls.push(url);
    if (url.pathname === "/api/finance/ledger/trial-balance/") return json(trialBalance(url.searchParams.get("scope") ?? "unit"));
    if (url.pathname === "/api/finance/ledger/accounts/5/") return json(ledger);
    if (url.pathname === "/api/finance/ledger/entries/43/") return json(entry);
    if (url.pathname === "/api/finance/ledger/entries/") return json(entries);
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the trial balance groups accounts by class, totals them and shows the checks", async () => {
  act(() => root.render(<GeneralLedger selectedUnitId={1} />));
  await flush();
  assert.equal(urls[0].searchParams.get("unit"), "1");
  assert.match(urls[0].searchParams.get("start") ?? "", /^\d{4}-\d{2}-01$/);
  assert.deepEqual([...container.querySelectorAll(".gl-class th")].map((cell) => cell.textContent), ["Asset", "Liability", "Expense"]);
  assert.deepEqual([...container.querySelectorAll(".gl-checks li")].map((item) => item.textContent),
    ["✓Period debits equal credits", "✓Closing balances net to zero", "✓Every posted entry balances", "3 entries dated in this period"]);
  const total = [...container.querySelectorAll(".gl-trial-balance tfoot td")].map((cell) => cell.textContent);
  assert.deepEqual(total, ["—", "—", "14,400.00", "12,400.00", "12,000.00", "10,000.00"]);
  const excel = container.querySelector(".gl-excel") as HTMLAnchorElement;
  const href = new URL(excel.getAttribute("href")!, "http://localhost");
  assert.equal(href.pathname, "/api/finance/ledger/export/trial-balance/");
  assert.equal(href.searchParams.get("scope"), "unit");
  assert.equal(excel.textContent, "Download Excel");
  assert.equal(buttons("Whole company").length, 0, "No company switch without access to every unit");
});

test("an account opens its postings with a running balance, and an entry opens all its lines", async () => {
  act(() => root.render(<GeneralLedger selectedUnitId={1} />));
  await flush();
  await act(async () => buttons("TEST ONLY — Inventory asset")[0].click());
  await flush();
  assert.match(container.querySelector("#gl-account-title")?.textContent ?? "", /normally a debit balance/);
  assert.deepEqual([...container.querySelectorAll(".gl-account tbody tr")].map((row) => row.lastElementChild?.textContent), ["12,000.00", "9,600.00"]);
  assert.equal(container.querySelector(".gl-account tbody .gl-date")?.textContent, "2026-09-30posted 2026-10-02", "Document date first, posting date when different");
  await act(async () => buttons("#43")[0].click());
  await flush();
  assert.match(container.querySelector("#gl-entry-title")?.textContent ?? "", /Journal entry #43/);
  assert.match(container.querySelector(".gl-entry-meta")?.textContent ?? "", /Document date2026-10-09.*Legal entitytivoli.*Business unitTVL/);
  assert.equal(container.querySelectorAll(".gl-entry tbody tr").length, 2);
  assert.match(container.querySelector(".gl-entry .gl-checks")?.textContent ?? "", /Debits equal credits/);
  await act(async () => buttons("← Back")[0].click());
  assert.ok(container.querySelector("#gl-account-title"), "Back returns to the account the entry was opened from");
});

test("journal entries can be filtered by type and searched", async () => {
  act(() => root.render(<GeneralLedger selectedUnitId={1} />));
  await flush();
  await act(async () => buttons("Journal entries")[0].click());
  await flush();
  assert.equal(container.querySelectorAll(".gl-entries tbody tr").length, 1);
  const select = container.querySelector(".gl-filters select") as HTMLSelectElement;
  await act(async () => {
    select.value = "delivery";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  const last = urls.filter((url) => url.pathname === "/api/finance/ledger/entries/").at(-1)!;
  assert.equal(last.searchParams.get("kind"), "delivery");
});

test("a permission error from the server is shown instead of the ledger", async () => {
  globalThis.fetch = (async () => json({ detail: "You do not have permission to view the general ledger for this business unit." }, 403)) as typeof fetch;
  act(() => root.render(<GeneralLedger selectedUnitId={2} />));
  await flush();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /permission to view the general ledger/);
  assert.equal(container.querySelector(".gl-trial-balance"), null);
});

test("people with access to every unit can switch to the whole company", async () => {
  canViewCompany = true;
  act(() => root.render(<GeneralLedger selectedUnitId={1} />));
  await flush();
  await act(async () => buttons("Whole company")[0].click());
  await flush();
  assert.equal(urls.at(-1)!.searchParams.get("scope"), "company");
  assert.match(container.querySelector("#gl-title")?.textContent ?? "", /Tivoli Group/);
  await act(async () => buttons("Journal entries")[0].click());
  await flush();
  const entriesCall = urls.filter((url) => url.pathname === "/api/finance/ledger/entries/").at(-1)!;
  assert.equal(entriesCall.searchParams.get("scope"), "company");
  assert.ok([...container.querySelectorAll(".gl-entries thead th")].some((cell) => cell.textContent === "Unit"));
  assert.ok([...container.querySelectorAll(".gl-entries tbody td")].some((cell) => cell.textContent === "RBB"));
  const excel = new URL((container.querySelector(".gl-entries .gl-excel") as HTMLAnchorElement).getAttribute("href")!, "http://localhost");
  assert.equal(excel.pathname, "/api/finance/ledger/export/entries/");
  assert.equal(excel.searchParams.get("scope"), "company");
});
