import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import CashForecast from "../src/CashForecast";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const week = (index: number, opening: number, money_in: number, out: number, lines: unknown[] = []) => ({
  index, start: `2026-11-${String(1 + (index - 1) * 7).padStart(2, "0")}`, end: `2026-11-${String(7 + (index - 1) * 7).padStart(2, "0")}`,
  opening: opening.toFixed(2), in: money_in.toFixed(2), out: out.toFixed(2), net: (money_in - out).toFixed(2),
  closing: (opening + money_in - out).toFixed(2), lines,
});
const forecast = () => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" }, today: "2026-11-01", end: "2026-11-28",
  weeks: [
    week(1, 50000, 12000, 30000, [
      { date: "2026-11-01", direction: "in", source: "customers", source_label: "Customers paying invoices", label: "TVL-INV-2026-00004 · TEST ONLY Al Thani", amount: "12000.00", late: true },
      { date: "2026-11-03", direction: "out", source: "planned", source_label: "Planned items", label: "TEST ONLY Showroom rent", amount: "30000.00", late: false },
    ]),
    week(2, 32000, 0, 45000), week(3, -13000, 60000, 5000), week(4, 42000, 0, 0),
  ],
  opening: "50000.00", closing: "42000.00", lowest: { week: 2, start: "2026-11-08", closing: "-13000.00" }, goes_negative: true,
  by_source: [{ key: "customers", label: "Customers paying invoices", in: "72000.00", out: "0.00" }, { key: "suppliers", label: "Paying suppliers", in: "0.00", out: "50000.00" },
    { key: "planned", label: "Planned items", in: "0.00", out: "30000.00" }],
  late: { in: "12000.00", out: "0.00" }, later: { in: "8000.00", out: "0.00" },
  assumptions: { weeks: 4, customer_days: { individual: 30, company: 60, government: 90 }, supplier_days: 30,
    customer_types: [{ code: "individual", label: "Individual" }, { code: "company", label: "Company" }, { code: "government", label: "Government" }] },
  planned: [{ id: 5, name: "TEST ONLY Showroom rent", direction: "out", amount: "30000.00", first_date: "2026-11-03", repeat: "monthly", repeat_label: "Every month", until: null, created_by: "Synthetic Admin" }],
  can_plan: true, currency: "QAR", policy: "TEST ONLY policy",
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

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/cash-forecast/" && init?.method === "POST") return json({ id: 6 }, 201);
    if (url.pathname === "/api/finance/cash-forecast/") return json(forecast());
    if (init?.method === "DELETE") return json({ removed: 5 });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the forecast warns when the balance goes below zero and shows each week", async () => {
  act(() => root.render(<CashForecast selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  const tiles = container.querySelector(".ex-tiles")?.textContent ?? "";
  assert.match(tiles, /In the bank today50,000\.00.*Lowest point-13,000\.00week 2, from 8 Nov · goes below zero.*In 4 weeks42,000\.00.*Already late12,000\.00/);
  assert.match(container.querySelector("[role=alert]")?.textContent ?? "", /goes below zero/);
  assert.equal(container.querySelectorAll(".cf-chart path").length, 4);
  assert.equal(container.querySelectorAll('.cf-chart path[fill="#d14b3a"]').length, 1, "Only the negative week is red");
  assert.match(container.querySelector(".cf-chart svg")?.getAttribute("aria-label") ?? "", /lowest -13,000\.00 in week 2/);
  assert.match(container.querySelector(".cf-table")?.textContent ?? "", /Week 28 Nov to 14 Nov32,000\.0045,000\.00-13,000\.00/);
  await act(async () => button("Week 1")!.click());
  const detail = container.querySelector(".cf-week")?.textContent ?? "";
  assert.match(detail, /TVL-INV-2026-00004 · Al ThaniCustomers paying invoices · already late\+12,000\.00/);
  assert.match(detail, /Showroom rentPlanned items−30,000\.00/);
});

test("changing the assumptions asks again and planned items are added and removed", async () => {
  act(() => root.render(<CashForecast selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(field("Government customers pay after").value, "90");
  await setValue(field("Government customers pay after"), "120");
  await flush();
  assert.equal(calls.at(-1)?.url.searchParams.get("government_days"), "120");
  assert.equal(calls.at(-1)?.url.searchParams.get("individual_days"), "30");
  await act(async () => button("Add a planned item")!.click());
  await setValue(field("What it is"), "TEST ONLY Salaries");
  await setValue(field("Amount (QAR)"), "80000");
  await act(async () => button("Add to the forecast")!.click());
  await flush();
  const post = calls.find((call) => call.init?.method === "POST")!;
  const body = JSON.parse(String(post.init?.body));
  assert.deepEqual([body.name, body.direction, body.amount, body.repeat, body.until], ["TEST ONLY Salaries", "out", "80000", "monthly", null]);
  await act(async () => button("Remove")!.click());
  await flush();
  assert.equal(calls.find((call) => call.init?.method === "DELETE")?.url.pathname, "/api/finance/cash-forecast/items/5/");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /Showroom rent is removed from the forecast/);
});
