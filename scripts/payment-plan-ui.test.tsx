import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import PaymentPlan from "../src/PaymentPlan";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const line = (payee: string, currency: string, amount: string, value: string, extra: Record<string, unknown> = {}) => ({
  due: "2026-11-09", source: "planned", source_label: "Planned payment", label: "Once", payee, currency, late: false,
  amount, value, total_amount: amount, total_value: value, send_on: [{ date: "2026-11-09", amount, value }], split: false,
  first_day: "2026-11-09", last_day: "2026-11-09", after_due: false, days_after_due: 0, ...extra,
});
const qnb = { id: 3, name: "TEST ONLY QNB current", bank_name: "QNB", account_last4: "1234" };
const plan = (view = "week") => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" }, today: "2026-11-02", view, end: "2026-11-14", total: "209200.00",
  periods: [
    { index: 1, start: "2026-11-01", end: "2026-11-07", value: "0.00", count: 0, currencies: [], groups: [], days: [], late: false, busy: false },
    { index: 2, start: "2026-11-08", end: "2026-11-14", value: "209200.00", count: 3, late: false, busy: true,
      currencies: [{ currency: "QAR", amount: "100000.00", value: "100000.00" }, { currency: "USD", amount: "30000.00", value: "109200.00" }],
      groups: [{ account: qnb, value: "209200.00",
        currencies: [{ currency: "QAR", amount: "100000.00", value: "100000.00" }, { currency: "USD", amount: "30000.00", value: "109200.00" }],
        lines: [line("TEST ONLY Italian factory", "USD", "20000.00", "72800.00"), line("TEST ONLY Showroom rent", "QAR", "100000.00", "100000.00"),
          line("TEST ONLY Shipping line", "USD", "10000.00", "36400.00", { send_on: [{ date: "2026-11-10", amount: "10000.00", value: "36400.00" }],
            first_day: "2026-11-10", last_day: "2026-11-10", after_due: true, days_after_due: 1 })] }],
      days: ["2026-11-08", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"].map((date) => ({ date,
        banks: [{ bank_name: "QNB", used: date === "2026-11-09" ? "72800.00" : date === "2026-11-10" ? "36400.00" : "0.00", limit: "100000.00" }] })) },
  ],
  late: { count: 0, value: "0.00" },
  busy_days: [{ bank_name: "QNB", date: "2026-11-09", due: "109200.00", limit: "100000.00", over_by: "9200.00" }],
  moved: [{ payee: "TEST ONLY Shipping line", label: "Once", currency: "USD", amount: "10000.00", due: "2026-11-09", last_day: "2026-11-10", split: false }],
  after_end: { count: 0, value: "0.00" },
  limits: [{ bank_name: "QNB", daily_limit: "100000.00", set_by: "Synthetic Admin" }],
  accounts: [{ ...qnb, is_default: true }], rates: [{ currency: "USD", rate: "3.64", source: "indicative" }],
  planned: [{ id: 5, name: "TEST ONLY Showroom rent", amount: "100000.00", currency: "QAR", first_date: "2026-11-09", repeat: "monthly",
    repeat_label: "Every month", until: null, account: null, created_by: "Synthetic Admin" }],
  assumptions: { supplier_days: 30, periods: 2 }, can_manage: true, currency: "QAR", policy: "TEST ONLY policy",
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
    if (url.pathname === "/api/finance/payment-plan/limits/") return json({ bank_name: "QNB", daily_limit: "150000.00" });
    if (url.pathname === "/api/finance/payment-plan/") return json(plan(url.searchParams.get("view") ?? "week"));
    if (init?.method === "DELETE") return json({ removed: 5 });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the plan shows each week by bank and currency and warns about the bank limit", async () => {
  act(() => root.render(<PaymentPlan selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.equal(calls[0].url.searchParams.get("view"), "week");
  const tiles = container.querySelector(".ex-tiles")?.textContent ?? "";
  assert.match(tiles, /To pay this week0\.00.*Days over a bank limit11 payment moved to later days/);
  const warning = container.querySelector(".pp-warning")?.textContent ?? "";
  assert.match(warning, /QNB: QAR 109,200\.00 due, limit 100,000\.00 \(over by 9,200\.00\)/);
  assert.match(warning, /Shipping line USD 10,000\.00 \(due 9 Nov, last part 10 Nov\)/);
  assert.match(container.querySelector(".pp-periods")?.textContent ?? "", /Week of 8 Nov8 Nov to 14 Nov · bank limit reachedQAR 100,000\.00USD 30,000\.003209,200\.00/);
  await act(async () => button("Week of 8 Nov")!.click());
  const detail = container.querySelector(".pp-detail")?.textContent ?? "";
  assert.match(detail, /QNB · QNB current ··1234QAR 100,000\.00USD 30,000\.00QAR 209,200\.00/);
  assert.match(detail, /Tue 10 Novdue 9 NovShipping linePlanned paymentOnceMoved 1 day for the bank limitUSD 10,000\.0036,400\.00/);
  assert.equal(container.querySelectorAll(".pp-day-row .pp-meter").length, 5);
});

test("switching to months, adding a regular payment and changing a limit", async () => {
  act(() => root.render(<PaymentPlan selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("By month")!.click());
  await flush();
  assert.deepEqual([calls.at(-1)?.url.searchParams.get("view"), calls.at(-1)?.url.searchParams.get("periods")], ["month", "3"]);
  await act(async () => button("Add a payment")!.click());
  await setValue(field("Pay to / what for"), "TEST ONLY Natuzzi standing order");
  await setValue(field("Amount"), "15000");
  await setValue(field("Currency"), "EUR");
  await setValue(field("Paid from"), "3");
  await act(async () => button("Add the payment")!.click());
  await flush();
  const post = JSON.parse(String(calls.find((call) => call.url.pathname === "/api/finance/cash-forecast/" && call.init?.method === "POST")!.init?.body));
  assert.deepEqual([post.name, post.direction, post.amount, post.currency, post.account, post.repeat],
    ["TEST ONLY Natuzzi standing order", "out", "15000", "EUR", 3, "monthly"]);
  const limit = container.querySelector(".pp-limit input") as HTMLInputElement;
  assert.equal(limit.value, "100000.00");
  await setValue(limit, "150000");
  await act(async () => [...container.querySelectorAll(".pp-limits button")].find((node) => node.textContent === "Save")!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await flush();
  const saved = calls.find((call) => call.url.pathname === "/api/finance/payment-plan/limits/")!;
  assert.deepEqual(JSON.parse(String(saved.init?.body)), { bank_name: "QNB", daily_limit: "150000" });
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /QNB's daily limit is now QAR 150,000\.00/);
});
