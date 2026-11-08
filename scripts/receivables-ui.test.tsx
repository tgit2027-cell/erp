import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Receivables from "../src/Receivables";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: URL[];
let empty = false;

const buckets = [
  { key: "current", label: "0–30 days" }, { key: "days_31_60", label: "31–60 days" },
  { key: "days_61_90", label: "61–90 days" }, { key: "days_91_120", label: "91–120 days" }, { key: "over_120", label: "Over 120 days" },
];
const yearBuckets = [
  { key: "year_1", label: "Up to 1 year" }, { key: "years_1_2", label: "1–2 years" }, { key: "years_2_3", label: "2–3 years" },
  { key: "years_3_4", label: "3–4 years" }, { key: "years_4_5", label: "4–5 years" }, { key: "over_5_years", label: "Over 5 years" },
];
const customer = { id: 3, name: "TEST ONLY Al Sadd family", title: "Sheikh", client_number: "C-000002", project_number: "P-000001", contact: "+974 5555 0101" };
const aging = () => ({
  unit: { id: 1, code: "TVL", name: "Tivoli" }, as_of: "2026-10-31", by: "days", buckets, policy_label: "TEST ONLY policy.",
  customers: empty ? [] : [{
    ...customer, buckets: { current: "400.00", days_31_60: "0.00", days_61_90: "0.00", days_91_120: "0.00", over_120: "250.00" }, total: "650.00", oldest_days: 121,
    invoices: [
      { id: 11, number: "TVL-INV-2026-00011", date: "2026-07-03", quote_id: 40, total: "2500.00", paid: "2250.00", credited: "0.00", owed: "250.00", age_days: 121, bucket: "over_120" },
      { id: 12, number: "TVL-INV-2026-00012", date: "2026-10-20", quote_id: 41, total: "4000.00", paid: "3600.00", credited: "0.00", owed: "400.00", age_days: 11, bucket: "current" },
    ],
  }],
  totals: empty ? { current: "0.00", days_31_60: "0.00", days_61_90: "0.00", days_91_120: "0.00", over_120: "0.00" } : { current: "400.00", days_31_60: "0.00", days_61_90: "0.00", days_91_120: "0.00", over_120: "250.00" },
  total: empty ? "0.00" : "650.00",
});
const statement = {
  unit: { id: 1, code: "TVL", name: "Tivoli" }, customer, show: "all", by: "days", start: "2026-01-01", end: "2026-10-31",
  opening_balance: "0.00", closing_balance: "400.00", total_debits: "4000.00", total_credits: "3600.00",
  lines: [
    { kind: "payment", reference: "Quote 41", description: "Deposit received (simulated cash)", date: "2026-10-10", debit: "", credit: "3600.00", balance: "-3600.00" },
    { kind: "invoice", reference: "TVL-INV-2026-00012", description: "Invoice for quote 41", date: "2026-10-20", debit: "4000.00", credit: "", balance: "400.00" },
  ],
  aging: { current: "400.00", days_31_60: "0.00", days_61_90: "0.00", days_91_120: "0.00", over_120: "0.00" }, invoices_owed: "400.00", buckets, policy_label: "TEST ONLY policy.",
};

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (label: string) => [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(label)) as HTMLButtonElement;
const cells = (selector: string) => [...container.querySelectorAll(selector)].map((cell) => cell.textContent);

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  empty = false;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(url);
    if (url.pathname === "/api/finance/receivables/" && url.searchParams.get("by") === "years") {
      const data = aging();
      return json({ ...data, by: "years", buckets: yearBuckets,
        totals: { year_1: "650.00", years_1_2: "0.00", years_2_3: "0.00", years_3_4: "0.00", years_4_5: "0.00", over_5_years: "0.00" },
        customers: data.customers.map((row) => ({ ...row, buckets: { year_1: "650.00", years_1_2: "0.00", years_2_3: "0.00", years_3_4: "0.00", years_4_5: "0.00", over_5_years: "0.00" } })) });
    }
    if (url.pathname === "/api/finance/receivables/") return json(aging());
    if (url.pathname === "/api/finance/receivables/3/statement/" && url.searchParams.get("show") === "unpaid") {
      return json({ ...statement, show: "unpaid", lines: [], unpaid: [
        { id: 11, number: "TVL-INV-2026-00011", date: "2026-07-03", quote_id: 40, total: "2500.00", paid: "2250.00", credited: "0.00", owed: "250.00", age_days: 121, bucket: "over_120", running: "250.00" },
        { id: 12, number: "TVL-INV-2026-00012", date: "2026-10-20", quote_id: 41, total: "4000.00", paid: "3600.00", credited: "0.00", owed: "400.00", age_days: 11, bucket: "current", running: "650.00" },
      ], invoices_owed: "650.00" });
    }
    if (url.pathname === "/api/finance/receivables/3/statement/") return json(statement);
    return json({ detail: "You do not have permission to view the general ledger for this business unit." }, 403);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("Finance sees what each customer owes by age, with the late amount flagged", async () => {
  act(() => root.render(<Receivables selectedUnitId={1} />));
  await flush();
  assert.equal(calls[0].searchParams.get("unit"), "1");
  assert.match(calls[0].searchParams.get("as_of") ?? "", /^\d{4}-\d{2}-\d{2}$/);
  assert.deepEqual(cells(".rv-tiles strong"), ["650.00", "400.00", "—", "—", "—", "250.00"]);
  assert.ok(container.querySelector(".rv-tile-late"));
  assert.match(container.querySelector(".rv-table tbody th")?.textContent ?? "", /SheikhAl Sadd family.*2 unpaid invoices · oldest 121 days/);
  assert.equal(container.querySelector(".rv-number")?.textContent, "C-000002 · P-000001");
  assert.deepEqual(cells(".rv-table tbody tr:first-child td.rv-num"), ["400.00", "—", "—", "—", "250.00", "650.00"]);
  assert.ok(container.querySelector("td.rv-late"));
  await act(async () => button("By years").click());
  await flush();
  assert.equal(calls.at(-1)?.searchParams.get("by"), "years");
  assert.deepEqual(cells(".rv-table thead th.rv-num"), ["Up to 1 year", "1–2 years", "2–3 years", "3–4 years", "4–5 years", "Over 5 years", "Total"]);
  assert.equal(container.querySelector("td.rv-late"), null);
});

test("opening a customer lists the unpaid invoices", async () => {
  act(() => root.render(<Receivables selectedUnitId={1} />));
  await flush();
  await act(async () => button("Al Sadd family").click());
  assert.equal(button("Al Sadd family").getAttribute("aria-expanded"), "true");
  assert.deepEqual(cells(".rv-invoices tbody td:last-child"), ["250.00", "400.00"]);
  assert.match(container.querySelector(".rv-invoices tbody td")?.textContent ?? "", /TVL-INV-2026-00011Quote 40/);
});

test("the statement shows a running balance and explains what the closing balance means", async () => {
  act(() => root.render(<Receivables selectedUnitId={1} />));
  await flush();
  await act(async () => button("Statement").click());
  await flush();
  const statementCall = calls.find((url) => url.pathname.endsWith("/statement/"))!;
  assert.equal(statementCall.searchParams.get("unit"), "1");
  assert.match(statementCall.searchParams.get("start") ?? "", /^\d{4}-01-01$/);
  assert.match(container.querySelector(".rv-statement-head h3")?.textContent ?? "", /Sheikh.*Al Sadd family/);
  assert.deepEqual(cells(".rv-table tbody td:last-child"), ["0.00", "-3,600.00", "400.00"]);
  assert.match(container.querySelector(".rv-closing")?.textContent ?? "", /400\.00 QAR owed by the customer/);
  assert.equal(statementCall.searchParams.get("show"), "all");
  await act(async () => button("Unpaid invoices only").click());
  await flush();
  assert.equal(calls.at(-1)?.searchParams.get("show"), "unpaid");
  assert.equal(container.querySelector('input[type="date"]')?.closest("label")?.textContent?.startsWith("Unpaid at the end of"), true, "No start date for unpaid invoices");
  assert.deepEqual(cells('[aria-label="Unpaid invoices"] tbody td:last-child'), ["250.00", "650.00"]);
  assert.match(container.querySelector('[aria-label="Unpaid invoices"] tfoot')?.textContent ?? "", /Total unpaid650\.00/);
  assert.equal(container.querySelector(".rv-closing"), null);
  await act(async () => button("Back to who owes us").click());
  await flush();
  assert.ok(container.querySelector(".rv-tiles"));
});

test("nothing owed and refusals are said plainly", async () => {
  empty = true;
  act(() => root.render(<Receivables selectedUnitId={1} />));
  await flush();
  assert.match(container.querySelector(".rv-empty")?.textContent ?? "", /No customer owes Tivoli anything/);
  globalThis.fetch = (async () => json({ detail: "You do not have permission to view the general ledger for this business unit." }, 403)) as typeof fetch;
  act(() => root.render(<Receivables key="other" selectedUnitId={3} />));
  await flush();
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /permission to view the general ledger/);
});
