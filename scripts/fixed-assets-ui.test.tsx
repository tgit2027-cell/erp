import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import FixedAssets from "../src/FixedAssets";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: { url: URL; init?: RequestInit }[];

const asset = (id: number, extra = {}) => ({
  id, number: `FA-TVL-0000${id}`, name: "TEST ONLY showroom sofas", category: "furniture", category_label: "Furniture and fittings",
  supplier: "TEST ONLY Roche Bobois", purchase_date: "2026-07-10", cost: "12000.00", residual_value: "0.00", life_months: 60,
  monthly: "200.00", depreciated: "800.00", book_value: "11200.00", fully_depreciated: false,
  paid_from: { id: 1, name: "TEST ONLY QNB current" }, source: "purchase", source_label: "Bought", from_stock: null, status: "in_use", status_label: "In use", submitted_by: "Synthetic Admin",
  decided_by: "Omar", rejection_reason: "", journal_entry_id: 9, disposal: null, can_decide: false, can_dispose: true, ...extra,
});
let current: () => ReturnType<typeof base> = () => base();
const overview = () => current();
const base = () => ({
  unit: { id: 7, code: "TVL", name: "Tivoli" },
  rows: [asset(1), asset(2, { name: "TEST ONLY delivery van", status: "submitted", status_label: "Waiting for approval", journal_entry_id: null, depreciated: "0.00", can_decide: true, can_dispose: false })],
  totals: { count: 1, cost: "12000.00", depreciated: "800.00", book_value: "11200.00", monthly: "200.00", waiting: 1 },
  last_run: { month: "2026-09", total: "200.00", by: "Synthetic Admin", journal_entry_id: 12 },
  next_run: { month: "2026-10", total: "200.00", assets: 1, unbilled: [] as { receipt_id: number; supplier: string; received_on: string }[] },
  runs: [{ month: "2026-09", total: "200.00", by: "Synthetic Admin", journal_entry_id: 12 }],
  categories: [{ value: "furniture", label: "Furniture and fittings", usual_life_months: 60 }, { value: "computers", label: "Computers and IT equipment", usual_life_months: 36 }],
  accounts: [{ id: 1, name: "TEST ONLY QNB current", kind: "bank", opening_date: "2026-01-01" }],
  under_construction: "30000.00",
  stock_options: [{ variant_id: 5, location_id: 9, sku: "TVF-002-LIN", location: "STIV-G", free: "3.00", unit_value: "1200.00" }],
  sources: [{ value: "purchase", label: "Bought and paid from bank or cash" }, { value: "construction", label: "Finished construction" },
    { value: "stock", label: "Taken from stock" }],
  can_manage: true, can_approve: true, policy_label: "TEST ONLY",
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
  current = () => base();
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, init });
    if (url.pathname === "/api/finance/fixed-assets/" && init?.method === "POST") return json(asset(3, { status: "submitted" }), 201);
    if (url.pathname === "/api/finance/fixed-assets/depreciation/") return json({ ...overview(), next_run: null, last_run: { month: "2026-10", total: "200.00", by: "Synthetic Admin", journal_entry_id: 13 } });
    if (url.pathname === "/api/finance/fixed-assets/") return json(overview());
    if (url.pathname === "/api/finance/fixed-assets/1/" && init?.method === "POST") {
      return JSON.parse(String(init.body)).date > "2026-12-31" ? json({ detail: "The date cannot be in the future." }, 400) : json(asset(1, { status: "disposed" }));
    }
    if (url.pathname === "/api/finance/fixed-assets/2/") return json(asset(2, { status: "in_use" }));
    if (url.pathname === "/api/finance/fixed-assets/1/") {
      return json({ ...asset(1), schedule: [{ month: "2026-07", amount: "200.00", posted: true }, { month: "2026-11", amount: "200.00", posted: false }] });
    }
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the register shows value in the books and posts the next month's depreciation", async () => {
  act(() => root.render(<FixedAssets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".ex-tiles")?.textContent ?? "", /Value in the books11,200\.00/);
  assert.match(container.querySelector(".fa-run")?.textContent ?? "", /October 2026 depreciation is not posted yet: QAR 200\.00 across 1 asset/);
  assert.match(container.querySelector(".fa-table")?.textContent ?? "", /FA-TVL-00001showroom sofasFurniture and fittings · 5 years/);
  await act(async () => button("Post October 2026 depreciation")!.click());
  await flush();
  assert.deepEqual(JSON.parse(String(posts()[0].init?.body)), { month: "2026-10" });
  assert.match(container.querySelector(".fa-run")?.textContent ?? "", /posted up to October 2026/);

  await act(async () => button("FA-TVL-00001")!.click());
  await flush();
  assert.match(container.querySelector(".fa-months")?.textContent ?? "", /July 2026200\.00posted.*November 2026200\.00planned/);
});

test("an asset is registered with the usual life for its category", async () => {
  act(() => root.render(<FixedAssets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Register an asset")!.click());
  await act(async () => {
    setValue(field("What it is"), "TEST ONLY laptops");
    setValue(field("Category"), "computers");
    setValue(field("Cost"), "3600");
  });
  assert.match(container.textContent ?? "", /about QAR 100\.00 a month for 3 years/);
  await act(async () => (container.querySelector("form[aria-label='Register a fixed asset']") as HTMLFormElement).requestSubmit());
  await flush();
  const sent = JSON.parse(String(posts()[0].init?.body));
  assert.equal(sent.category, "computers");
  assert.equal(sent.account_id, 1);
  assert.equal(sent.life_months, "", "Left empty, the server uses the usual life");
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /booked once a second person approves it/);
});

test("approve a purchase, and sell or write off with a reason", async () => {
  act(() => root.render(<FixedAssets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Approve")!.click());
  await flush();
  assert.equal(posts()[0].url.pathname, "/api/finance/fixed-assets/2/");
  await act(async () => button("Sell or write off")!.click());
  const form = container.querySelector(".fa-dispose") as HTMLFormElement;
  assert.equal(form.querySelector("select"), null, "No account is asked for a write-off");
  await act(async () => setValue(form.querySelector("input[aria-label='Amount received (QAR)']") as HTMLInputElement, "5000"));
  assert.ok(form.querySelector("select[aria-label='Paid into']"));
  await act(async () => setValue(form.querySelector("input[aria-label='Reason']") as HTMLInputElement, "TEST ONLY replaced"));
  await act(async () => form.requestSubmit());
  await flush();
  const body = JSON.parse(String(posts()[1].init?.body));
  assert.equal(body.decision, "dispose");
  assert.equal(body.proceeds, "5000");
  assert.equal(body.account_id, 1);
  assert.match(container.querySelector("[role=status]")?.textContent ?? "", /FA-TVL-00001 sold/);
});

test("a finished construction and stock taken for the company", async () => {
  act(() => root.render(<FixedAssets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  await act(async () => button("Register an asset")!.click());
  await act(async () => setValue(field("How it was added"), "construction"));
  assert.match(container.textContent ?? "", /QAR 30,000\.00 is waiting on assets under construction/);
  assert.equal(field("Paid from"), undefined, "No bank account for a construction");
  await act(async () => {
    setValue(field("What it is"), "TEST ONLY showroom fit-out");
    setValue(field("Cost"), "24000");
  });
  await act(async () => (container.querySelector("form[aria-label='Register a fixed asset']") as HTMLFormElement).requestSubmit());
  await flush();
  let sent = JSON.parse(String(posts()[0].init?.body));
  assert.equal(sent.source, "construction");
  assert.equal(sent.cost, "24000");
  assert.equal("account_id" in sent, false);

  await act(async () => button("Register an asset")!.click());
  await act(async () => setValue(field("How it was added"), "stock"));
  assert.equal(field("Cost"), undefined, "The cost comes from the stock value");
  await act(async () => {
    setValue(field("What it is"), "TEST ONLY office sofa");
    setValue(field("Item and location"), "5-9");
    setValue(field("Quantity"), "2");
  });
  assert.match(container.textContent ?? "", /about QAR 40\.00 a month for 5 years/);
  await act(async () => (container.querySelector("form[aria-label='Register a fixed asset']") as HTMLFormElement).requestSubmit());
  await flush();
  sent = JSON.parse(String(posts()[1].init?.body));
  assert.deepEqual([sent.source, sent.variant_id, sent.location_id, sent.quantity, sent.cost], ["stock", 5, 9, "2", undefined]);
});

test("a month waits until the deliveries in it are billed", async () => {
  current = () => ({ ...base(), next_run: { month: "2026-10", total: "200.00", assets: 1,
    unbilled: [{ receipt_id: 44, supplier: "TEST ONLY Roche Bobois", received_on: "2026-10-12" }] } });
  act(() => root.render(<FixedAssets selectedUnitId={7} csrfToken="csrf" />));
  await flush();
  assert.match(container.querySelector(".fa-run")?.textContent ?? "", /record the supplier bills.*receipt 44 from Roche Bobois \(2026-10-12\)/);
  assert.equal(button("Post October 2026 depreciation"), undefined);
});
