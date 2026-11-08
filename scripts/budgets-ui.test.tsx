import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import Budgets from "../src/Budgets";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];
let status: "" | "draft" | "submitted" | "approved";
let decide: boolean;

const zeros = () => Array(12).fill("0.00");
const cmp = (budget: string, actual: string, flag = "", used: number | null = 100) => ({
  budget, actual, difference: (Number(actual) - Number(budget)).toFixed(2), used_percent: used, flag,
});
const change = (actual: string, delta: string, percent: number | null = null) => ({ actual, change: delta, change_percent: percent });
const previous = (last = change("0.00", "0.00"), before = change("0.00", "0.00"), full = ["0.00", "0.00"]) => [
  { year: 2025, month: last, to_date: last, full_year: full[0] }, { year: 2024, month: before, to_date: before, full_year: full[1] }];
const line = (key: string, label: string, kind: string, extra = {}) => ({ previous: previous(),
  line: key, label, kind, budget: zeros(), actual: zeros(), last_year: zeros(), year_budget: "0.00",
  month: cmp("0.00", "0.00", "", null), to_date: cmp("0.00", "0.00", "", null), ...extra,
});
const view = () => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" }, year: 2026, month: 10, years: [2025, 2026, 2027],
  budget: status ? { id: 1, status, status_label: { draft: "Being prepared", submitted: "Waiting for approval", approved: "Approved" }[status], note: "", prepared_by: "Synthetic Admin", approved_by: status === "approved" ? "Omar" : "", approved_at: "", updated_at: "" } : null,
  lines: [
    line("sales", "Sales", "income", { month: cmp("50000.00", "42000.00", "below", 84), to_date: cmp("500000.00", "510000.00", "", 102), year_budget: "600000.00" }),
    line("cost_of_sales", "Cost of sales", "cost"),
    line("rent", "Rent", "cost", { month: cmp("18000.00", "18000.00"), to_date: cmp("180000.00", "180000.00"), year_budget: "216000.00", last_year: Array(12).fill("17500.00") }),
    line("utilities", "Electricity and water", "cost", { month: cmp("1000.00", "1250.00", "over", 125), to_date: cmp("10000.00", "10900.00", "over", 109),
      previous: previous(change("9800.00", "1100.00", 11), change("9000.00", "1900.00", 21), ["11900.00", "11000.00"]) }),
    line("provisions", "Provisions", "cost"),
    line("bank_interest", "Bank interest", "income", { month: cmp("100.00", "130.00", "", 130), to_date: cmp("1000.00", "1200.00", "", 120),
      previous: previous(change("1500.00", "-300.00", -20), change("900.00", "300.00", 33), ["1800.00", "1100.00"]) }),
    line("other_income", "Other income", "income"),
  ],
  gross_profit: { month: cmp("50000.00", "42000.00", "below"), to_date: cmp("500000.00", "510000.00"), year_budget: "600000.00", previous: previous(change("480000.00", "30000.00", 6)) },
  net_profit: { month: cmp("31000.00", "22750.00", "below"), to_date: cmp("310000.00", "319100.00"), year_budget: "372000.00",
    previous: previous(change("300000.00", "19100.00", 6), change("280000.00", "39100.00", 14), ["360000.00", "330000.00"]) },
  history: status ? [{ at: "2026-10-31T10:00:00Z", by: "Synthetic Admin", what: "saved changes", note: "", total: "234000.00" }] : [],
  can_prepare: true, can_approve: decide, can_edit: status === "" || status === "draft", can_submit: status === "draft",
  can_decide: decide && status === "submitted", can_reopen: decide && status === "approved", policy_label: "TEST ONLY",
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent?.startsWith(text)) as HTMLButtonElement | undefined;
const input = (label: string) => container.querySelector(`input[aria-label="${label}"]`) as HTMLInputElement;
function type(field: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
}
const posts = () => calls.filter((call) => call.init?.method === "POST");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  status = "";
  decide = false;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/budgets/" && init?.method === "POST") { status = "draft"; return json(view()); }
    const action = url.pathname.match(/^\/api\/finance\/budgets\/2026\/(\w+)\/$/)?.[1];
    if (action) {
      status = ({ submit: "submitted", approve: "approved", return: "draft", reopen: "draft" } as const)[action as "submit"];
      return json(view());
    }
    if (url.pathname === "/api/finance/budgets/") return json(view());
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("budget against actual shows the month, the year so far and flags overspending", async () => {
  status = "approved";
  act(() => root.render(<Budgets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const tiles = container.querySelector(".ex-tiles")?.textContent ?? "";
  assert.match(tiles, /Sales in October42,000budget 50,000 · -8,000/);
  assert.match(tiles, /Running costs in October19,250budget 19,000 · \+250/);
  const utilities = container.querySelector("tr[data-line=utilities]")!;
  assert.match(utilities.textContent ?? "", /1,0001,250\+250/);
  assert.equal(utilities.querySelectorAll(".bg-flag-over").length, 2);
  assert.match(utilities.querySelector(".bg-used")?.textContent ?? "", /109%/);
  assert.equal(container.querySelector("tr[data-line=sales] .bg-flag-below")?.textContent, "-8,000");
  assert.equal(button("Change budget"), undefined, "An approved budget is locked");

  await act(async () => {
    const select = [...container.querySelectorAll("select")][1] as HTMLSelectElement;
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")?.set?.call(select, "3");
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  assert.equal(calls.at(-1)!.url.searchParams.get("month"), "3");
});

test("a budget is prepared with yearly totals spread over the months and sent for approval", async () => {
  act(() => root.render(<Budgets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Prepare the 2026 budget")!.click());
  await act(async () => type(input("Rent for the year"), "100"));
  await act(async () => (container.querySelector("button[aria-label='Spread Rent over the year']") as HTMLButtonElement).click());
  assert.equal(input("Rent, January").value, "8");
  assert.equal(input("Rent, December").value, "9", "Leftover riyals go to the last months");
  await act(async () => type(input("Electricity and water, July"), "1500"));
  await act(async () => button("Start from 2025 actual figures")!.click());
  assert.equal(input("Rent, March").value, "17500");
  await act(async () => type(input("Rent, March"), "19000"));
  await act(async () => button("Save and send for approval")!.click());
  await flush();
  const saved = JSON.parse(String(posts()[0].init?.body));
  assert.equal(saved.year, 2026);
  assert.equal(saved.amounts.rent[2], "19000");
  assert.equal(saved.amounts.rent[0], "17500");
  assert.equal(posts()[1].url.pathname, "/api/finance/budgets/2026/submit/");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /sent for approval/);
  assert.match(container.querySelector(".bg-state")?.textContent ?? "", /Waiting for approval/);
});

test("an approver approves, or sends back and reopens with a reason", async () => {
  status = "submitted";
  decide = true;
  act(() => root.render(<Budgets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Send back")!.click());
  await act(async () => type(input("Why it is sent back"), "TEST ONLY rent rises in July"));
  await act(async () => (container.querySelector(".bg-reason") as HTMLFormElement).requestSubmit());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/finance/budgets/2026/return/");
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { reason: "TEST ONLY rent rises in July" });

  status = "submitted";
  await act(async () => button("Send for approval")?.click());
  await flush();
  await act(async () => button("Approve budget")!.click());
  await flush();
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /approved/);
  await act(async () => button("Reopen to change")!.click());
  assert.ok(input("Why it is reopened"));
});

test("other income lines and the comparison with the two previous years", async () => {
  act(() => root.render(<Budgets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const rows = [...container.querySelectorAll('[aria-label="Budget against actual"] tbody tr')].map((row) => row.querySelector("th")?.textContent);
  assert.deepEqual(rows, ["Sales", "Cost of sales", "Gross profit", "Rent", "Electricity and water", "Provisions", "Bank interest", "Other income", "Net profit"]);
  const earlier = container.querySelector('[aria-label="Compared with earlier years"]')!;
  assert.match(earlier.querySelector("thead")?.textContent ?? "", /January to October.*Full year.*2026.*2025.*Change.*2024.*Change.*2025.*2024/);
  const interest = earlier.querySelector('tr[data-line="bank_interest"]')!;
  assert.match(interest.textContent ?? "", /^Bank interest1,2001,500-300 -20%900\+3001,8001,100$/);
  assert.equal(interest.querySelectorAll(".bg-flag-over").length, 1, "Less interest than last year is red");
  const utilities = earlier.querySelector('tr[data-line="utilities"]')!;
  assert.equal(utilities.querySelectorAll(".bg-flag-over").length, 2, "Costs up on both years are red");
  assert.match([...earlier.querySelectorAll("tr.bg-total")].at(-1)?.textContent ?? "", /Net profit319,100300,000\+19,100 \+6%280,000\+39,100360,000330,000/);
  const select = container.querySelector(".bg-earlier-head select") as HTMLSelectElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(select), "value")?.set?.call(select, "month");
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  assert.match(earlier.querySelector("thead")?.textContent ?? "", /^QAR, actualOctoberFull year/);
  assert.match(earlier.querySelector('tr[data-line="bank_interest"]')?.textContent ?? "", /^Bank interest130/);
});
