import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { ReportsArea } from "../src/ReportingArea";
import { VisualChartCard } from "../src/ReportVisuals";
import type { VisualChart } from "../src/reporting-api";

const nativeFetch = globalThis.fetch;
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: URL[];

const unitScope = { business_units: [{ id: 3, code: "TVL", name: "Tivoli" }], companies: [], locations: [] };
const empty = { business_units: [], companies: [], locations: [] };
const catalog = {
  panels: [{ code: "sales", label: "Sales" }, { code: "gross_margin", label: "Gross margin" }],
  scopes: {
    sales: unitScope, category_sales: empty, supplier_cost: empty, landed_cost: empty, gross_margin: empty,
    expenses: empty, inventory: empty, purchase_history: empty, transfer_history: empty, budgets: empty,
  },
};
const marginChart: VisualChart = {
  id: "revenue-vs-cost", title: "Revenue and cost of goods sold", kind: "column", unit: "QAR",
  categories: ["2026-08", "2026-09", "2026-10"], stacked: false, note: "",
  series: [
    { key: "revenue", label: "Revenue", values: ["12000.00", "15500.00", "9800.00"] },
    { key: "cost_of_goods_sold", label: "Cost of goods sold", values: ["7000.00", "8100.00", "6200.00"] },
  ],
};
const salesVisual = {
  panel: "sales", title: "Sales", granularity: "monthly", currency: "QAR", row_count: 42,
  filters: { start: "2026-08-01", end: "2026-10-23", location_id: null, unit_id: 3, company_id: null },
  scope: { scope_type: "business_unit", business_unit: { id: 3, code: "TVL", name: "Tivoli" }, business_unit_ids: [3], company: null },
  kpis: [{ label: "Sales", value: "37300.00", unit: "QAR" }, { label: "Quantity sold", value: "18.00", unit: "units" }],
  charts: [
    { id: "sales-by-period", title: "Sales by period", kind: "column", unit: "QAR", categories: ["2026-08", "2026-09"], stacked: false, note: "",
      series: [{ key: "sales_amount", label: "Sales", values: ["12000.00", "25300.00"] }] },
    { id: "quantity-by-period", title: "Quantity sold by period", kind: "line", unit: "units", categories: ["2026-08", "2026-09"], stacked: false, note: "",
      series: [{ key: "quantity", label: "Quantity", values: ["6.00", "12.00"] }] },
  ],
  policy_label: "TEST ONLY",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 5; index += 1) await act(async () => { await new Promise(setImmediate); });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(url);
    if (url.pathname === "/api/reports/") return json(catalog);
    if (url.pathname === "/api/reports/sales/visual/") return json(salesVisual);
    if (url.pathname === "/api/reports/gross_margin/visual/") return json({ detail: "No gross margin data is granted for this scope." }, 403);
    if (url.pathname === "/api/report-views/") return json({ views: [], panels: [] });
    return json(url.pathname.startsWith("/api/reports/") ? { detail: "not used" } : {}, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("a grouped column chart has a legend, one column per value, a hover tooltip and a table", async () => {
  act(() => root.render(<VisualChartCard chart={marginChart} />));
  assert.deepEqual([...container.querySelectorAll(".viz-legend li")].map((item) => item.textContent), ["Revenue", "Cost of goods sold"]);
  assert.equal(container.querySelectorAll("svg path").length, 6);
  const target = container.querySelectorAll("svg rect[tabindex]")[1];
  assert.match(target.getAttribute("aria-label") ?? "", /2026-09: Revenue 15,500.00 QAR, Cost of goods sold 8,100.00 QAR/);
  await act(async () => { target.dispatchEvent(new window.MouseEvent("mouseover", { bubbles: true })); });
  assert.match(container.querySelector(".viz-tooltip")?.textContent ?? "", /2026-09Revenue: 15,500.00 QARCost of goods sold: 8,100.00 QAR/);
  assert.equal(container.querySelectorAll(".viz-table tbody tr").length, 3);
});

test("a single-series bar chart needs no legend and labels each bar with its value", () => {
  act(() => root.render(<VisualChartCard chart={{
    ...marginChart, id: "by-location", kind: "bar", title: "Units on hand by location", unit: "units",
    categories: ["Showroom", "Warehouse"], series: [{ key: "on_hand_quantity", label: "Units on hand", values: ["40.00", "10.00"] }],
  }} />));
  assert.equal(container.querySelector(".viz-legend"), null);
  assert.deepEqual([...container.querySelectorAll(".viz-bar-value")].map((item) => item.textContent), ["40 units", "10 units"]);
  assert.equal((container.querySelectorAll(".viz-bar")[1] as HTMLElement).style.width, "25%");
});

test("the visual dashboard loads every assigned panel with the chosen scope and explains a denial", async () => {
  act(() => root.render(<ReportsArea csrfToken="TEST-ONLY" selectedUnitId={3} canManagePanels={false} />));
  await flush();
  const before = calls.length;
  const toggle = [...container.querySelectorAll(".report-view-toggle button")] as HTMLButtonElement[];
  assert.equal(toggle[0].getAttribute("aria-pressed"), "true");
  await act(async () => { toggle[1].click(); });
  await flush();
  assert.equal(container.querySelector(".report-panel-select"), null, "The dashboard shows every panel, so no panel picker");
  const visualCalls = calls.filter((url) => url.pathname.endsWith("/visual/"));
  assert.deepEqual(visualCalls.map((url) => url.pathname).sort(), ["/api/reports/gross_margin/visual/", "/api/reports/sales/visual/"]);
  assert.ok(visualCalls.every((url) => url.searchParams.get("unit") === "3" && !url.searchParams.has("page")));
  const sales = container.querySelector("[data-visual-panel=sales]")!;
  assert.match(sales.querySelector(".viz-kpis")?.textContent ?? "", /Sales37,300.00 QARQuantity sold18 units/);
  assert.equal(sales.querySelectorAll(".viz-chart").length, 2);
  assert.match(container.querySelector("[data-visual-panel=gross_margin] .viz-denied")?.textContent ?? "", /No gross margin data/);
  assert.equal(calls.slice(before).some((url) => url.pathname === "/api/reports/sales/"), false, "The standard report is not fetched in visual mode");
});
