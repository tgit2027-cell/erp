import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import StockValue from "../src/StockValue";

const nativeFetch = globalThis.fetch;
let root: ReturnType<typeof createRoot>;
let container: HTMLDivElement;
let calls: URL[];
let withLedger = true;

const line = (sku: string, location: string, brand: string, category: string, value: string, extra = {}) => ({
  variant_id: sku.length, sku, item_code: "TVF-002", description: "TEST ONLY lounge chair", variant_description: "linen",
  brand, category, location_id: location.length, location, in_transit: false, quantity: "2.00", value, average_cost: String(Number(value) / 2),
  ...extra,
});
const report = (asOf: string) => ({
  unit: { id: 1, code: "TVL", name: "Tivoli" }, as_of: asOf, today: "2026-11-03",
  total: { value: "9000.00", quantity: "6.00", item_count: 3, in_transit: "1000.00" },
  groups: {
    location: [{ name: "Showroom", quantity: "4.00", value: "8000.00", item_count: 2, share: "88.9" },
      { name: "In transit", quantity: "2.00", value: "1000.00", item_count: 1, share: "11.1", in_transit: true }],
    brand: [{ name: "Linea", quantity: "6.00", value: "9000.00", item_count: 3, share: "100.0" }],
    category: [{ name: "Chairs", quantity: "4.00", value: "6000.00", item_count: 2, share: "66.7" },
      { name: "Tables", quantity: "2.00", value: "3000.00", item_count: 1, share: "33.3" }],
  },
  lines: [line("TVF-001", "Showroom", "Linea", "Chairs", "5000.00"), line("TVF-0002", "Showroom", "Linea", "Tables", "3000.00"),
    line("TVF-00003", "In transit", "Linea", "Chairs", "1000.00", { in_transit: true })],
  line_count: 3, policy_label: "TEST ONLY policy.",
  ledger: withLedger ? (asOf === "2026-11-03" ? { balance: "9000.00", difference: "0.00", agrees: true } : { balance: "8500.00", difference: "500.00", agrees: false }) : null,
});

function json(data: unknown, code = 200) {
  return new Response(JSON.stringify(data), { status: code, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 4; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((node) => node.textContent === text) as HTMLButtonElement | undefined;
const rows = (label: string) => [...container.querySelectorAll(`table[aria-label="${label}"] tbody tr`)].map((row) => row.querySelector("th")?.textContent ?? "");

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  withLedger = true;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(url);
    if (url.pathname === "/api/inventory/stock-value/") return json(report(url.searchParams.get("as_of") || "2026-11-03"));
    return json({ detail: "Not found" }, 404);
  }) as typeof fetch;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("shows the total, the tie to the books, groups and items, and filters by a group", async () => {
  await act(async () => root.render(<StockValue selectedUnitId={1} />));
  await flush();
  const text = container.textContent ?? "";
  assert.match(text, /Stock value on 3 November 2026QAR 9,000\.00/);
  assert.match(text, /Agrees with the stock/);
  assert.deepEqual(rows("Stock value by location"), ["Showroom", "In transitIn transit or missing, not on a shelf"]);
  assert.equal(rows("Stock value by item").length, 3);
  const link = container.querySelector<HTMLAnchorElement>("a[download]");
  assert.equal(link?.getAttribute("href"), "/api/inventory/stock-value/?unit=1&as_of=2026-11-03&export=xlsx");

  await act(async () => button("By category")?.click());
  assert.deepEqual(rows("Stock value by category"), ["Chairs", "Tables"]);
  await act(async () => button("Chairs")?.click());
  assert.deepEqual(rows("Stock value by item").map((row) => row.split("lounge")[0]), ["TVF-001", "TVF-00003"]);
  assert.match(container.textContent ?? "", /Items in Chairs/);
  await act(async () => button("Show all")?.click());
  assert.equal(rows("Stock value by item").length, 3);
});

test("a past month end shows the gap with the books, and the ledger figure needs permission", async () => {
  await act(async () => root.render(<StockValue selectedUnitId={1} />));
  await flush();
  await act(async () => button("Last month end")?.click());
  await flush();
  assert.equal(calls.at(-1)?.searchParams.get("as_of"), "2026-10-31");
  assert.match(container.textContent ?? "", /Differs by QAR 500\.00/);
  assert.match(container.textContent ?? "", /booked in the ledger on a different date/);

  withLedger = false;
  await act(async () => button("Today")?.click());
  await flush();
  assert.match(container.textContent ?? "", /Needs the “view general ledger” permission/);
});
