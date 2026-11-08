import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { ReportsArea } from "../src/ReportingArea";

const nativeFetch = globalThis.fetch;
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
let calls: Array<{ url: URL; method: string; body: unknown }>;
let links: Array<Record<string, unknown>>;
let sharedResponse: { status: number; body: unknown };

const unitScope = {
  business_units: [{ id: 3, code: "TVL", name: "Tivoli" }, { id: 4, code: "RBB", name: "Roche Bobois" }],
  companies: [],
  locations: [{ id: 7, name: "Tivoli showroom", business_units: [4] }],
};
const empty = { business_units: [], companies: [], locations: [] };
const catalog = {
  panels: [{ code: "sales", label: "Sales" }, { code: "inventory", label: "Inventory" }],
  scopes: {
    sales: unitScope, category_sales: empty, supplier_cost: empty, landed_cost: empty, gross_margin: empty,
    expenses: empty, inventory: unitScope, purchase_history: empty, transfer_history: empty, budgets: empty, export: unitScope,
  },
};
function report(panel: string, unit: number) {
  return {
    panel, title: panel === "sales" ? "Sales" : "Inventory", granularity: "daily", currency: "QAR",
    filters: { start: "2026-10-01", end: "2026-10-27", location_id: null, unit_id: unit, company_id: null },
    scope: { scope_type: "business_unit", business_unit: { id: unit, code: "U", name: "Unit" }, business_unit_ids: [unit], company: null },
    fields: ["period", "sales_amount"], rows: [{ period: "2026-10-01", sales_amount: "100.00" }],
    pagination: { page: 1, page_size: 50, total_count: 1, total_pages: 1, previous_page: null, next_page: null },
    policy_label: "TEST ONLY",
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
async function flush() {
  for (let index = 0; index < 6; index += 1) await act(async () => { await new Promise(setImmediate); });
}
const button = (text: string) => [...container.querySelectorAll("button")].find((item) => item.textContent === text) as HTMLButtonElement;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  calls = [];
  links = [];
  sharedResponse = { status: 404, body: { detail: "not used" } };
  window.history.replaceState(null, "", "/#/reports");
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body });
    if (url.pathname === "/api/reports/") return json(catalog);
    if (url.pathname === "/api/reports/share-links/" && method === "POST") {
      const link = {
        id: 11, panel: body.panel, panel_label: "Sales", filters: { period: body.period, start: body.start, end: body.end, unit: body.unit },
        note: body.note, created_at: "2026-10-27T08:00:00Z", expires_at: "2026-11-03T08:00:00Z", revoked_at: null,
        open_count: 0, last_opened_at: null, state: "active",
      };
      links.unshift(link);
      return json({ ...link, url: "http://localhost/?shared_report=SYNTHETIC-TOKEN#/reports" }, 201);
    }
    if (url.pathname === "/api/reports/share-links/") return json({ links });
    if (url.pathname === "/api/reports/share-links/11/revoke/") {
      links[0] = { ...links[0], state: "revoked", revoked_at: "2026-10-27T09:00:00Z" };
      return json(links[0]);
    }
    if (url.pathname.startsWith("/api/reports/shared/")) return json(sharedResponse.body, sharedResponse.status);
    if (url.pathname === "/api/reports/sales/") return json(report("sales", Number(url.searchParams.get("unit"))));
    if (url.pathname === "/api/reports/inventory/") return json(report("inventory", Number(url.searchParams.get("unit"))));
    if (url.pathname === "/api/report-views/") return json({ views: [], panels: [] });
    return json({ detail: "not used" }, 404);
  }) as typeof fetch;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  globalThis.fetch = nativeFetch;
});

test("the report offers a PDF export next to Excel and Word", async () => {
  act(() => root.render(<ReportsArea csrfToken="TEST-ONLY" selectedUnitId={3} canManagePanels={false} />));
  await flush();
  assert.ok(button("Export Excel"));
  assert.ok(button("Export Word"));
  assert.ok(button("Export PDF"));
  assert.ok(button("Share link"));
});

test("a share link is created for the scope on screen, shown once, and can be withdrawn", async () => {
  act(() => root.render(<ReportsArea csrfToken="TEST-ONLY" selectedUnitId={3} canManagePanels={false} />));
  await flush();
  await act(async () => { button("Share link").click(); });
  const note = container.querySelector(".rs-field input") as HTMLInputElement;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    setter.call(note, "For the Monday meeting");
    note.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
  await act(async () => { button("Create link").click(); });
  await flush();
  const created = calls.find((call) => call.url.pathname === "/api/reports/share-links/" && call.method === "POST")!;
  assert.equal((created.body as { unit: number }).unit, 3);
  assert.equal((created.body as { panel: string }).panel, "sales");
  assert.equal((created.body as { note: string }).note, "For the Monday meeting");
  assert.equal("page" in (created.body as object), false, "A link stores the report, not the page being looked at");
  assert.match(container.textContent ?? "", /It is shown only once/);
  assert.equal((container.querySelector(".rs-url input") as HTMLInputElement).value, "http://localhost/?shared_report=SYNTHETIC-TOKEN#/reports");

  const mine = container.querySelector(".rs-mine") as HTMLDetailsElement;
  await act(async () => {
    mine.open = true;
    mine.dispatchEvent(new window.Event("toggle"));
  });
  await flush();
  assert.match(mine.textContent ?? "", /SalesFor the Monday meeting|For the Monday meeting/);
  assert.match(mine.textContent ?? "", /Active/);
  await act(async () => { button("Withdraw").click(); });
  await flush();
  assert.match(mine.textContent ?? "", /Withdrawn/);
  assert.equal(button("Withdraw"), undefined);
});

test("opening a share link applies its panel, scope, period and location, then drops the token from the address", async () => {
  window.history.replaceState(null, "", "/?shared_report=SYNTHETIC-TOKEN#/reports");
  sharedResponse = {
    status: 200,
    body: {
      panel: "inventory", panel_label: "Inventory", note: "Stock for the showroom",
      filters: { period: "daily", start: "2026-10-01", end: "2026-10-27", unit: 4, location: 7 },
      shared_by: "Synthetic Sam", created_at: "2026-10-27T08:00:00Z", expires_at: "2026-11-03T08:00:00Z",
    },
  };
  act(() => root.render(<ReportsArea csrfToken="TEST-ONLY" selectedUnitId={3} canManagePanels={false} />));
  await flush();
  assert.equal(window.location.search, "", "The token is removed from the address bar");
  assert.equal(window.location.hash, "#/reports");
  assert.match(container.querySelector(".rs-notice-ok")?.textContent ?? "", /Shared by Synthetic Sam.*Stock for the showroom.*your own report permissions/);
  const latest = calls.filter((call) => call.url.pathname === "/api/reports/inventory/").at(-1)!;
  assert.equal(latest.url.searchParams.get("unit"), "4");
  assert.equal(latest.url.searchParams.get("location"), "7");
  assert.equal(latest.url.searchParams.get("start"), "2026-10-01");
  assert.equal(latest.url.searchParams.get("period"), "daily");
});

test("a refused share link explains why and shows no report from it", async () => {
  window.history.replaceState(null, "", "/?shared_report=SYNTHETIC-TOKEN#/reports");
  sharedResponse = { status: 403, body: { detail: "This shared report covers data you are not allowed to see. Ask the sender or a reporting administrator." } };
  act(() => root.render(<ReportsArea csrfToken="TEST-ONLY" selectedUnitId={3} canManagePanels={false} />));
  await flush();
  assert.match(container.querySelector(".rs-notice")?.textContent ?? "", /could not be opened.*not allowed to see/);
  assert.equal(calls.some((call) => call.url.pathname === "/api/reports/inventory/"), false);
  await act(async () => { button("Dismiss").click(); });
  assert.equal(container.querySelector(".rs-notice"), null);
});

test("a share link still opens when the reports screen remounts while it loads", async () => {
  window.history.replaceState(null, "", "/?shared_report=SYNTHETIC-TOKEN#/reports");
  sharedResponse = {
    status: 200,
    body: {
      panel: "sales", panel_label: "Sales", note: "",
      filters: { period: "monthly", start: "2026-05-01", end: "2026-10-27", unit: 4 },
      shared_by: "Synthetic Sam", created_at: "2026-10-27T08:00:00Z", expires_at: "2026-11-03T08:00:00Z",
    },
  };
  act(() => root.render(<ReportsArea csrfToken="TEST-ONLY" selectedUnitId={3} canManagePanels={false} />));
  act(() => root.unmount());
  root = createRoot(container);
  act(() => root.render(<ReportsArea csrfToken="TEST-ONLY" selectedUnitId={3} canManagePanels={false} />));
  await flush();
  assert.equal(calls.filter((call) => call.url.pathname.startsWith("/api/reports/shared/")).length, 1, "The link is opened once");
  assert.match(container.querySelector(".rs-notice-ok")?.textContent ?? "", /Shared by Synthetic Sam/);
  const latest = calls.filter((call) => call.url.pathname === "/api/reports/sales/").at(-1)!;
  assert.equal(latest.url.searchParams.get("unit"), "4");
  assert.equal(latest.url.searchParams.get("start"), "2026-05-01");
});
